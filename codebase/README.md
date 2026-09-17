# Teach-back Studio

Ứng dụng web teach-back đa chủ đề, dùng knowledge có trích dẫn từ transcript đã làm sạch. Người học trò chuyện tự nhiên; backend tự nhận diện ý định và chủ đề trước khi đặt câu hỏi Socratic hoặc chấm phần dạy lại.

Hai luồng được giữ độc lập:

- `learning_platform.py` + `server.py`: backend web đa chủ đề, session/message bền vững và runtime Offline/OpenAI.
- `agent_core.py` + `agent_graph.py` + `run_eval.py`: evaluation harness D3 ban đầu, giữ mastery gate và bộ 20 golden cases.

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
python -m unittest codebase.tests.test_learning_platform -v
```

Evaluation harness D3 cần cài dependency:

```powershell
python -m pip install -r codebase/requirements.txt
python -m unittest discover -s codebase/tests -v
python codebase/run_eval.py --provider offline
```

Chạy golden set bằng OpenAI thật:

```powershell
$env:OPENAI_API_KEY="..."
python codebase/run_eval.py --provider openai
```
