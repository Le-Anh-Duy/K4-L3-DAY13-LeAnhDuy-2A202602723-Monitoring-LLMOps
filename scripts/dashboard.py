"""Runtime dashboard: the six panels of config/dashboard.yaml, computed from data/logs.jsonl.

    python scripts/dashboard.py        -> http://127.0.0.1:8050 (recomputed on every page refresh)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.metrics import percentile  # same percentile definition as the API's /metrics

CONFIG = yaml.safe_load((REPO_ROOT / "config/dashboard.yaml").read_text(encoding="utf-8"))["dashboard"]
LOG_PATH = REPO_ROOT / "data/logs.jsonl"
PORT = 8050


def load_events(since: datetime) -> list[dict]:
    events = []
    if not LOG_PATH.exists():
        return events
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
            ts = datetime.fromisoformat(event["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts >= since:
            event["_minute"] = ts.replace(second=0, microsecond=0)
            events.append(event)
    return events


def of(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e.get("event") == name]


def pct(values: list, p: int) -> float | None:
    return percentile(values, p) if values else None


def rate(part: int, total: int) -> float | None:
    return round(part / total * 100, 2) if total else None


def tool_success(events: list[dict]) -> float | None:
    flags = [e["tool_success"] for e in events if e.get("tool_success") is not None]
    return rate(sum(flags), len(flags))


def cumulative(values: list[float]) -> list[float]:
    out, running = [], 0.0
    for v in values:
        running += v
        out.append(round(running, 6))
    return out


def fmt(value: float | None, digits: int = 0) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def build() -> dict:
    minutes = CONFIG["time_range_minutes"]
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=minutes - 1)).replace(second=0, microsecond=0)
    buckets = [start + timedelta(minutes=i) for i in range(minutes)]
    events = load_events(start)
    by_minute: dict[datetime, list[dict]] = {b: [] for b in buckets}
    for e in events:
        by_minute.setdefault(e["_minute"], []).append(e)
    per_min = [by_minute[b] for b in buckets]

    sent, received, failed = of(events, "response_sent"), of(events, "request_received"), of(events, "request_failed")
    lat = [e["latency_ms"] for e in sent]
    ttft = [e["ttft_ms"] for e in sent]
    active = sorted({e["_minute"] for e in received})
    active_minutes = int((active[-1] - active[0]).total_seconds() // 60) + 1 if active else 0
    errors_by_type: dict[str, int] = {}
    for e in failed:
        errors_by_type[e.get("error_type") or "unknown"] = errors_by_type.get(e.get("error_type") or "unknown", 0) + 1
    cost_total = sum(e["cost_usd"] for e in sent)
    tokens_in, tokens_out = sum(e["tokens_in"] for e in sent), sum(e["tokens_out"] for e in sent)
    quality = [e["quality_score"] for e in sent]

    window = {  # value each panel's threshold.aggregation is checked against
        "latency": pct(lat, 95),
        "traffic": round(len(received) / active_minutes, 2) if active_minutes else None,
        "errors": rate(len(failed), len(received)),
        "cost": round(cost_total, 4),
        "tokens": max(tokens_in, tokens_out),
        "quality": round(mean(quality), 3) if quality else None,
    }
    stats = {
        "latency": [("P50", fmt(pct(lat, 50))), ("P95", fmt(pct(lat, 95))), ("P99", fmt(pct(lat, 99))), ("TTFT P95", fmt(pct(ttft, 95)))],
        "traffic": [("Requests", fmt(len(received))), ("Avg req/min (active span)", fmt(window["traffic"], 2))],
        "errors": [
            ("Error rate %", fmt(window["errors"], 2)),
            ("Retrieval success %", fmt(tool_success(events), 1)),
            ("By error_type", ", ".join(f"{k}: {v}" for k, v in errors_by_type.items()) or "none"),
        ],
        "cost": [("Total USD", fmt(cost_total, 4)), ("Avg USD/request", fmt(cost_total / len(sent) if sent else None, 4))],
        "tokens": [("tokens_in", fmt(tokens_in)), ("tokens_out", fmt(tokens_out))],
        "quality": [("Mean score", fmt(window["quality"], 3)), ("Responses", fmt(len(quality)))],
    }
    series = {
        "latency": [
            ("P50", [pct([e["latency_ms"] for e in of(m, "response_sent")], 50) for m in per_min]),
            ("P95", [pct([e["latency_ms"] for e in of(m, "response_sent")], 95) for m in per_min]),
            ("P99", [pct([e["latency_ms"] for e in of(m, "response_sent")], 99) for m in per_min]),
            ("TTFT P95", [pct([e["ttft_ms"] for e in of(m, "response_sent")], 95) for m in per_min]),
        ],
        "traffic": [("Requests/min", [len(of(m, "request_received")) for m in per_min])],
        "errors": [
            ("Error rate %", [rate(len(of(m, "request_failed")), len(of(m, "request_received"))) for m in per_min]),
            ("Retrieval success %", [tool_success(m) for m in per_min]),
        ],
        "cost": [("Cumulative USD", cumulative([sum(e["cost_usd"] for e in of(m, "response_sent")) for m in per_min]))],
        "tokens": [
            ("Cumulative tokens_in", cumulative([sum(e["tokens_in"] for e in of(m, "response_sent")) for m in per_min])),
            ("Cumulative tokens_out", cumulative([sum(e["tokens_out"] for e in of(m, "response_sent")) for m in per_min])),
        ],
        "quality": [("Mean score", [round(mean(q), 3) if (q := [e["quality_score"] for e in of(m, "response_sent")]) else None for m in per_min])],
    }

    panels = []
    for panel in CONFIG["panels"]:
        pid, th = panel["id"], panel["threshold"]
        value = window[pid]
        ok = None if value is None else (value <= th["value"] if th["operator"] == "lte" else value >= th["value"])
        panels.append({
            "id": pid,
            "title": panel["title"],
            "unit": panel["unit"],
            "query": panel["query"],
            "threshold": {**th, "symbol": "≤" if th["operator"] == "lte" else "≥"},
            "window_value": value,
            "ok": ok,
            "stats": stats[pid],
            "series": [{"name": n, "values": v} for n, v in series[pid]],
        })
    return {
        "title": CONFIG["title"],
        "minutes": minutes,
        "refresh": CONFIG["refresh_seconds"],
        "buckets": [b.isoformat() for b in buckets],
        "generated": now.isoformat(),
        "events": len(events),
        "panels": panels,
    }


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="__REFRESH__">
<title>Day 13 Dashboard</title>
<style>
:root { color-scheme: light; --surface:#fcfcfb; --card:#ffffff; --border:#e4e3df; --grid:#ecebe7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#76756f;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --good:#0ca30c; --critical:#d03b3b; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --surface:#1a1a19; --card:#232322;
  --border:#383835; --grid:#2e2e2c; --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#9a998f;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; } }
:root[data-theme="dark"] { color-scheme: dark; --surface:#1a1a19; --card:#232322; --border:#383835; --grid:#2e2e2c;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#9a998f; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; }
* { box-sizing: border-box; }
body { margin:0; background:var(--surface); color:var(--text-primary); font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { padding:20px 24px 8px; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:var(--text-secondary); font-size:13px; }
.meta b { color:var(--text-primary); font-weight:600; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(460px, 1fr)); gap:16px; padding:16px 24px 32px; }
@media (max-width: 520px) { .grid { grid-template-columns:1fr; padding:16px; } header { padding:16px 16px 4px; } }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; min-width:0; }
.head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }
h2 { font-size:15px; margin:0; }
.unit { color:var(--text-muted); font-size:12px; }
.status { font-size:12px; font-weight:600; white-space:nowrap; padding:2px 8px; border-radius:999px; border:1px solid currentColor; }
.status.ok { color:var(--good); } .status.bad { color:var(--critical); } .status.na { color:var(--text-muted); }
.stats { display:flex; flex-wrap:wrap; gap:6px 20px; margin:10px 0 6px; }
.stat .k { color:var(--text-secondary); font-size:12px; } .stat .v { font-size:18px; font-weight:600; font-variant-numeric:tabular-nums; }
.legend { display:flex; flex-wrap:wrap; gap:4px 14px; color:var(--text-secondary); font-size:12px; margin:4px 0; }
.legend i { display:inline-block; width:14px; height:3px; border-radius:2px; vertical-align:middle; margin-right:5px; }
.chart { position:relative; }
svg { width:100%; height:auto; display:block; overflow:visible; }
svg text { fill:var(--text-muted); font-size:11px; }
.tip { position:absolute; pointer-events:none; background:var(--card); border:1px solid var(--border); border-radius:6px;
  padding:6px 8px; font-size:12px; box-shadow:0 2px 8px rgba(0,0,0,.12); display:none; white-space:nowrap; z-index:2; }
.tip i { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; }
details { margin-top:6px; color:var(--text-secondary); font-size:12px; }
details code { font-size:11px; word-break:break-word; }
table { border-collapse:collapse; margin-top:6px; font-variant-numeric:tabular-nums; }
td, th { padding:2px 10px 2px 0; text-align:right; } th:first-child, td:first-child { text-align:left; }
</style></head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="meta" id="meta"></div>
</header>
<main class="grid" id="grid"></main>
<script>
const D = __DATA__;
const COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const W = 600, H = 210, L = 56, R = 12, T = 12, B = 26;
const hhmm = iso => new Date(iso).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
const nice = v => { const m = 10 ** Math.floor(Math.log10(v)), f = v / m; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 4 ? 4 : f <= 6 ? 6 : f <= 8 ? 8 : 10) * m; };
const num = (v, u) => v == null ? "—" : (u === "usd" ? v.toFixed(v >= 1 || v === 0 ? 2 : 4) : Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (+v.toFixed(2)).toString());
const el = (tag, attrs = {}, text) => { const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]); if (text != null) n.textContent = text; return n; };

document.getElementById("title").textContent = D.title;
document.getElementById("meta").innerHTML =
  `Time range: <b>last ${D.minutes} min</b> · Refresh: <b>${D.refresh}s</b> · Source: <b>data/logs.jsonl</b> · ` +
  `${D.events} events in window · Updated ${new Date(D.generated).toLocaleTimeString()}`;

for (const p of D.panels) {
  const card = document.createElement("section"); card.className = "card";
  const th = p.threshold;
  const status = p.ok == null ? `<span class="status na">– no data</span>`
    : p.ok ? `<span class="status ok">✓ within threshold</span>` : `<span class="status bad">⚠ breaching threshold</span>`;
  card.innerHTML = `<div class="head"><div><h2>${p.title}</h2><div class="unit">unit: ${p.unit} · threshold: ${th.aggregation} ${th.symbol} ${th.value}</div></div>${status}</div>
    <div class="stats">${p.stats.map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")}</div>`;
  if (p.series.length > 1)
    card.insertAdjacentHTML("beforeend", `<div class="legend">${p.series.map((s, i) => `<span><i style="background:${COLORS[i]}"></i>${s.name}</span>`).join("")}<span><i style="background:var(--text-secondary);height:1px"></i>threshold</span></div>`);

  const all = p.series.flatMap(s => s.values).filter(v => v != null);
  const yMax = p.unit === "score_0_to_1" ? 1 : p.unit === "percent" ? 100 : nice(Math.max(th.value, ...all, 1) * 1.05);
  const n = D.buckets.length, x = i => L + (W - L - R) * i / (n - 1), y = v => T + (H - T - B) * (1 - v / yMax);
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${p.title} per minute`});
  for (let k = 0; k <= 4; k++) { const v = yMax * k / 4;
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "var(--grid)"}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end"}, num(v, p.unit))); }
  for (let i = 0; i < n; i += 10) svg.append(el("text", {x: x(i), y: H - 6, "text-anchor": "middle"}, hhmm(D.buckets[i])));
  svg.append(el("line", {x1: L, x2: W - R, y1: y(th.value), y2: y(th.value), stroke: "var(--text-secondary)", "stroke-dasharray": "5 4"}));
  svg.append(el("text", {x: W - R, y: y(th.value) - 5, "text-anchor": "end"}, `${th.symbol} ${th.value}`));
  p.series.forEach((s, si) => {
    let d = "", pen = false;
    s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? "L" : "M"}${x(i)},${y(v)}`; pen = true; });
    if (d) svg.append(el("path", {d, fill: "none", stroke: COLORS[si], "stroke-width": 2, "stroke-linejoin": "round"}));
    s.values.forEach((v, i) => { if (v != null && s.values[i - 1] == null && s.values[i + 1] == null)
      svg.append(el("circle", {cx: x(i), cy: y(v), r: 4, fill: COLORS[si], stroke: "var(--card)", "stroke-width": 2})); });
  });
  const cross = el("line", {y1: T, y2: H - B, stroke: "var(--text-muted)", visibility: "hidden"}); svg.append(cross);
  const wrap = document.createElement("div"); wrap.className = "chart"; wrap.append(svg);
  const tip = document.createElement("div"); tip.className = "tip"; wrap.append(tip);
  svg.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) * W / r.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((sx - L) / (W - L - R) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${hhmm(D.buckets[i])}</b><br>` + p.series.map((s, si) => `<i style="background:${COLORS[si]}"></i>${s.name}: ${num(s.values[i], p.unit)}`).join("<br>");
    tip.style.display = "block";
    const px = x(i) * r.width / W; tip.style.left = (px > r.width / 2 ? px - tip.offsetWidth - 10 : px + 10) + "px"; tip.style.top = "8px";
  });
  svg.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); });
  card.append(wrap);

  const rows = D.buckets.map((b, i) => [b, p.series.map(s => s.values[i])]).filter(([, vs]) => vs.some(v => v != null && v !== 0));
  card.insertAdjacentHTML("beforeend", `<details><summary>Table view &amp; query</summary><code>${p.query}</code>
    <table><tr><th>minute</th>${p.series.map(s => `<th>${s.name}</th>`).join("")}</tr>
    ${rows.map(([b, vs]) => `<tr><td>${hhmm(b)}</td>${vs.map(v => `<td>${num(v, p.unit)}</td>`).join("")}</tr>`).join("")}</table></details>`);
  document.getElementById("grid").append(card);
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/":
            self.send_error(404)
            return
        body = PAGE.replace("__REFRESH__", str(CONFIG["refresh_seconds"])).replace(
            "__DATA__", json.dumps(build()).replace("</", "<\\/")
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://127.0.0.1:{PORT}  (source {LOG_PATH.relative_to(REPO_ROOT)}, Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
