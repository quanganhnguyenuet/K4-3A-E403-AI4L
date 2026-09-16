# D3 Teach-back Agent

Prototype cho lát cắt: học viên dạy lại **“Vì sao LLM có thể bịa?”**. Model đề xuất đánh giá semantic và hành động tiếp theo; harness giữ quyền tính mastery, kiểm tra citation, gọi retrieval tool và quyết định dừng.

## Thành phần

- `agent_core.py`: module quyết định trung tâm, OpenAI adapter, offline baseline, retrieval và mastery gate.
- `server.py`: HTTP API `POST /api/teach` để nối với giao diện.
- `run_eval.py`: chạy toàn bộ 20 golden cases và sinh báo cáo.
- `../eval/golden_set.json`: bộ test chính, 5 case cho mỗi lớp taxonomy.
- `../eval/run_results.md`: báo cáo lượt chạy gần nhất.
- `logs/model_calls.jsonl`: prompt, raw model response và quyết định của harness; thư mục này bị Git ignore vì có thể chứa dữ liệu người dùng.

## Chạy với OpenAI thật

PowerShell:

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="gpt-5-mini"
python codebase/server.py --provider openai
```

API dùng OpenAI Responses API với Structured Outputs và `store: false`. API key chỉ đọc từ biến môi trường và không được ghi log.

Gửi một lượt dạy:

```powershell
$body = @{
  session_id = "demo-01"
  student_explanation = "LLM dự đoán token tiếp theo theo xác suất..."
} | ConvertTo-Json
$bytes = [System.Text.Encoding]::UTF8.GetBytes($body)

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/teach `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $bytes
```

## Chạy eval

Lượt AI thật:

```powershell
python codebase/run_eval.py --provider openai
```

Baseline không gọi AI, chỉ để kiểm tra harness và runner:

```powershell
python codebase/run_eval.py --provider offline
```

`--provider auto` dùng OpenAI nếu có `OPENAI_API_KEY`, ngược lại dùng offline baseline và ghi cảnh báo rõ trong báo cáo.

## Mastery gate

- K1: 25 điểm - cơ chế dự đoán token.
- K2: 25 điểm - hợp lý không đồng nghĩa đúng.
- K3: 20 điểm - nguồn gây thiếu/sai căn cứ.
- K4: 15 điểm - giảm rủi ro nhưng không hứa tuyệt đối.
- Transfer: 15 điểm - ví dụ mới đúng.

Agent chỉ đạt 100% khi đủ K1-K4, không còn misconception và vượt câu transfer. LLM không được tự gán phần trăm.

## Mở rộng retrieval sau CP3

`KnowledgeBase.retrieve_evidence()` hiện lookup chính xác theo `knowledge_point_id`. API đã trả `source_cards` gồm source ID, loại tài liệu, file, trang/đoạn và diễn giải ngắn để UI có thể hiển thị “thông tin nằm ở đâu”. Khi có nhiều bài học, thay implementation này bằng hybrid/vector search có metadata filter `lesson_id`, `concept_id`, `knowledge_point_id`; contract của Agent và frontend không cần đổi.
