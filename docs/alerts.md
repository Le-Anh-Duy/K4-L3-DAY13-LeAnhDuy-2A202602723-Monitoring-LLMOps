# Alert và Runbook

Mỗi alert dựa trên triệu chứng người dùng thấy được (chậm, lỗi, tốn tiền), không dựa vào tên implementation nội bộ. Rule tương ứng nằm trong [`../config/alert_rules.yaml`](../config/alert_rules.yaml); ngưỡng lấy từ baseline trong [`../config/slo.yaml`](../config/slo.yaml).

Baseline tham chiếu (2026-09-30, 30 request): latency P50 475ms / P95 785ms / P99 1136ms, TTFT P95 50ms, error 0%, cost trung bình $0.002/request, quality 0.88.

## Alert 1

- Tên: `HighLatencyP95`
- Severity: `warning`
- Duration: `5m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: primary SLO `fast_successful_requests` (latency ≤ 3000ms, 99.5% / 28 ngày), đo trên `response_sent.latency_ms`
- Điều kiện và thời gian duy trì: `p95(latency_ms) > 3000ms` liên tục 5 phút
- Ảnh hưởng tới người dùng: người dùng chờ lâu trước khi nhận câu trả lời; mỗi request chậm hơn 3000ms tiêu vào error budget
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Latency: xác nhận P95/P99 tăng từ lúc nào; so với TTFT P95 để biết chậm trước hay sau khi LLM bắt đầu trả lời (TTFT bình thường nghĩa là phần chậm nằm trước LLM, ví dụ retrieval).
  2. Lọc `data/logs.jsonl` trong khoảng đó theo `event == "response_sent"` và `latency_ms > 3000`, lấy một `correlation_id`.
  3. Mở trace cùng `correlation_id` trên Langfuse, so thời lượng span `retrieval` và `llm-generate` với trace bình thường.
- Mitigation tạm thời: nếu span retrieval chậm thì khôi phục cấu hình/nguồn retrieval hoặc tắt practice scenario; nếu generation chậm sau khi đổi prompt thì rollback label `production` về version trước; giảm tải nếu do concurrency.
- Owner: `student-2A202602723`

## Alert 2

- Tên: `HighErrorRateOrRetrievalFailing`
- Severity: `critical`
- Duration: `5m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: primary SLO (request lỗi không phải good event) và guardrails `error_rate_pct_max: 2`, `retrieval_success_rate_pct_min: 90`
- Điều kiện và thời gian duy trì: `error_rate_pct > 2` hoặc `retrieval success < 90%` liên tục 5 phút
- Ảnh hưởng tới người dùng: người dùng nhận HTTP 500 thay vì câu trả lời; error budget 0.5% cạn rất nhanh
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Errors: xem error rate, breakdown theo `error_type` và retrieval success rate.
  2. Lọc log `event == "request_failed"`, đọc `error_type`, `tool_name`, `payload.detail` và lấy `correlation_id`.
  3. Mở trace cùng `correlation_id`: span nào có level ERROR và status message là gì.
- Mitigation tạm thời: nếu lỗi ở retrieval thì khôi phục vector store/dependency hoặc tắt practice scenario; nếu lỗi bắt đầu sau khi deploy/đổi prompt thì rollback.
- Owner: `student-2A202602723`

## Alert 3

- Tên: `CostPerRequestSpike`
- Severity: `warning`
- Duration: `15m`
- Kênh thông báo: Slack `#k4-l3b-alerts`
- SLI/SLO liên quan: guardrail `daily_cost_usd_max: 2.5`; ngưỡng $0.004/request = 2× baseline
- Điều kiện và thời gian duy trì: `avg(cost_usd) > 0.004` liên tục 15 phút (dùng 15 phút vì cost là vấn đề tích lũy, không cần đánh thức người trực ngay)
- Ảnh hưởng tới người dùng: không thấy ngay, nhưng câu trả lời dài bất thường và ngân sách ngày có thể cạn, dẫn tới phải hạn chế dịch vụ
- Ba bước kiểm tra đầu tiên:
  1. Mở panel Cost và Tokens: cost tăng do `tokens_in` (prompt/context dài) hay `tokens_out` (câu trả lời dài).
  2. Lọc log `response_sent` có `cost_usd` cao, lấy `correlation_id`.
  3. Mở trace: xem usage/cost của `llm-generate` và `prompt_version` trong metadata để biết có trùng lúc đổi prompt không.
- Mitigation tạm thời: rollback prompt nếu version mới làm output dài hơn; giới hạn max output tokens; tắt practice scenario.
- Owner: `student-2A202602723`
