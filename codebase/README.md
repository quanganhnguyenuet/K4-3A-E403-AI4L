# Teach-back Studio

Ứng dụng web teach-back đa chủ đề, dùng knowledge có trích dẫn từ transcript đã làm sạch. Người học trò chuyện tự nhiên; backend tự nhận diện ý định và chủ đề trước khi đặt câu hỏi Socratic hoặc chấm phần dạy lại.

Hai luồng được giữ độc lập:

- `lesson_engine.py` + `platform_runtime.py` + `server.py`: adapter web đa chủ đề, session/message bền vững và runtime Offline/OpenAI; toàn bộ quyết định dạy học đi qua `agent_core.py`/`agent_graph.py`.
- `agent_core.py` + `agent_graph.py`: evaluation harness D3 ban đầu, giữ mastery gate. `run_eval.py` (harness cho bộ K1-K4 20/28-case cũ) đã bị xoá; toàn bộ eval hiện chạy qua `run_eval_risk.py` với `eval/golden_set.json` (30-case risk-taxonomy).

## Chạy web

Từ thư mục gốc repository:

```powershell
python codebase/server.py --port 8000
```

Mở <http://127.0.0.1:8000>. Server phục vụ cả UI và API nên không cần chạy thêm `http.server`.

Trong UI:

- Chọn **Offline rules** để thử workflow không tốn API.
- Chọn **OpenAI realtime**, nhập model và API key để đánh giá ngữ nghĩa thật.
- Bắt đầu bằng một prompt tự nhiên; router tự chọn bài học rồi mới tạo session và nạp rubric.
- Yêu cầu bắt đầu học, xin gợi ý, hỏi làm rõ và chào hỏi không bị chấm như câu trả lời kiến thức.
- Khi người dùng chủ động đổi chủ đề, backend tạo và trả về session mới để UI chuyển theo.
- Nút **Cuộc trò chuyện mới** chỉ mở chat trống, không tự sinh session rác.
- Có thể mở lại hoặc xóa toàn bộ lịch sử từ sidebar.
- API key nhập trên UI chỉ đi cùng request hiện tại, không được ghi vào SQLite hoặc log.

Cũng có thể đặt key ở terminal để không phải nhập trên UI:

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="gpt-5-mini"
python codebase/server.py --port 8000
```

## Dữ liệu và persistence

- `../knowledge/lesson_catalog.json`: catalog 5 bài học, knowledge points, câu hỏi gợi mở và source cards.
- `../data/transcript/transcript-01-clean.md` đến `transcript-06-clean.md`: nguồn gốc nội bộ; web chỉ trả diễn giải ngắn và mã đoạn, không trả transcript đầy đủ.
- `state/learning.sqlite3`: session, progress và toàn bộ message history của web.
- `state/sessions.sqlite3`: checkpoint LangGraph của evaluation harness cũ.

## API web

```text
GET  /health
GET  /api/runtime
GET  /api/lessons
GET  /api/lessons/{lesson_id}
POST /api/chat
POST /api/sessions
GET  /api/sessions
GET  /api/sessions/{session_id}
POST /api/sessions/{session_id}/messages
POST /api/sessions/clear
```

Ví dụ bắt đầu chat để backend tự định tuyến:

```powershell
$body = @{
  content = "Mình muốn ôn North Star Metric và các chỉ số dẫn dắt."
  provider = "offline"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/chat" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

`POST /api/teach` và `GET /api/session/{id}` vẫn được giữ làm adapter tương thích với client cũ.

## Chạy test

Test backend web không cần gọi model:

```powershell
python -m unittest codebase.tests.test_lesson_engine -v
```

Evaluation harness D3 cần cài dependency:

```powershell
python -m pip install -r codebase/requirements.txt
python -m unittest discover -s codebase/tests -v
```

Unit test ở trên kiểm tra logic code và không phải điểm chất lượng model. Runner
Golden set duy nhất (`run_eval_risk.py`) bắt buộc gọi OpenAI; không còn fallback
offline khi thiếu key. Chạy bộ 30 case risk-taxonomy (`eval/golden_set.json`) trên
đúng pipeline mà web dùng:

```powershell
# Cần key; prompt và raw response được log theo từng run
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="gpt-5-mini"
python codebase/run_eval_risk.py --pipeline web --golden eval/golden_set.json
```

Mỗi run tạo `results.jsonl`, `report.md`, `summary.json`, `model_calls.jsonl`
trong `eval/runs/` và thêm một dòng vào `eval/run_history.jsonl`. API key không
được ghi vào log.
