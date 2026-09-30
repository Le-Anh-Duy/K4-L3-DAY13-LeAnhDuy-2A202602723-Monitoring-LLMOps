from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx

from app import logging_config
from app.main import app


def test_chat_response_log_exposes_quality_for_dashboard(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    async def send_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.post(
                "/chat",
                json={
                    "user_id": "student-01",
                    "session_id": "session-01",
                    "feature": "qa",
                    "message": "Explain observability",
                },
            )

    response = asyncio.run(send_request())

    assert response.status_code == 200
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    response_event = next(event for event in events if event["event"] == "response_sent")
    assert response_event["quality_score"] == response.json()["quality_score"]
    assert response_event["ttft_ms"] == response.json()["ttft_ms"]
    assert response_event["tool_name"] == "retrieval"
    assert response_event["tool_success"] is True


def test_correlation_id_reused_or_generated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")
    body = {"user_id": "u1", "session_id": "s1", "feature": "qa", "message": "hi"}

    async def send(headers: dict) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/chat", json=body, headers=headers)

    kept = asyncio.run(send({"x-request-id": "req-deadbeef"}))
    assert kept.headers["x-request-id"] == "req-deadbeef" == kept.json()["correlation_id"]
    bogus = asyncio.run(send({"x-request-id": "evil\nvalue"}))
    assert bogus.headers["x-request-id"].startswith("req-") and bogus.headers["x-request-id"] != "evil\nvalue"

    event = next(
        json.loads(line) for line in (tmp_path / "logs.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "response_sent"
    )
    assert {"user_id_hash", "session_id", "feature", "model", "env"} <= event.keys()
