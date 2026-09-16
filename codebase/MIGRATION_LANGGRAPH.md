# Migration: LangGraph + bug fixes + Socratic question redesign

Ghi lại những gì đã đổi khi chuyển `TeachBackAgent` từ một hàm `run_turn`
imperative sang một `StateGraph` (LangGraph), cùng các bug đã sửa và tính năng
đã thêm trong cùng đợt. Tham khảo thêm `spec.md` (mục đích/khoá tiêu chí),
`README.md` (cách chạy gốc) — file này bổ sung phần đã thay đổi.

## Vì sao đổi

1. **Session persistence** — `server.py` trước đây dùng `MemorySessionStore`
   (dict thuần), mất toàn bộ state khi restart server.
2. **Chất lượng câu hỏi gợi mở** — câu hỏi Socratic được sinh chung một lời
   gọi JSON với phần chấm K1-K4/misconception; nếu không an toàn thì rơi
   thẳng về câu mẫu tĩnh, không có cơ hội sửa.
3. **Bug regex** — rà soát toàn bộ `POINT_SIGNAL_PATTERNS`/
   `MISCONCEPTION_PATTERNS`/`DOMAIN_PATTERNS` phát hiện nhiều lỗi khớp từ
   khoá sai ngữ cảnh do mất dấu tiếng Việt.
4. **Muốn kiến trúc luồng quyết định rõ ràng hơn** — chuyển if/elif dài thành
   graph các node tường minh.

## Đã sửa (bug fixes)

Tất cả trong `codebase/agent_core.py`:

| # | Vị trí | Vấn đề | Cách sửa |
|---|---|---|---|
| 1 | `POINT_SIGNAL_PATTERNS["K4"]` | Bare `"kiem chung"` khớp cả câu K2 "không tự kiểm chứng sự thật", khiến K4 bị gán sai. | Thêm negative lookbehind `(?<!khong tu )` chỉ loại đúng cụm collision, không làm yếu tín hiệu K4 thật. |
| 2 | `POINT_SIGNAL_PATTERNS["K1"]` | Bare `tu`/`chon` khớp như substring bên trong từ khác (`tuyet`, `chong`...). | Bọc `\btu\b`, `\bchon\b`. |
| 3 | `MISCONCEPTION_PATTERNS["M2"]` | Bare `lua` khớp trong `luan`/`luat`. | Bọc `\blua\b`. |
| 4 | `MISCONCEPTION_PATTERNS["M5"]` | Bare `cu` khớp trong `cua` (từ "của" — cực phổ biến). Mức nghiêm trọng cao nhất trong đợt rà soát. | Bọc `\bcu\b`. |
| 5 | `MISCONCEPTION_PATTERNS["M3"/"M5"/"M6"]` | Bare `luon` khớp trong `luong`. | Bọc `\bluon\b`. |
| 6 | `POINT_SIGNAL_PATTERNS["K3"]` | `moi` mơ hồ giữa "mới"/"mỗi"/"mời", khoảng cách match quá rộng (20 ký tự). | Bọc `\bmoi\b`, thu hẹp còn 8 ký tự để chỉ bắt "mới" liền kề. |
| 7 | `DOMAIN_PATTERNS` | Bare `ai` collide với đại từ nghi vấn tiếng Việt "ai" (who), khiến câu ngoài chủ đề chứa "ai" bị coi là in-scope. | Bỏ `ai` khỏi domain keywords; thêm kiểm tra case-sensitive riêng `\bAI\b` (chữ hoa nguyên vẹn trên text gốc, trước khi lowercase) trong `looks_out_of_scope()` để vẫn nhận diện đúng khi học viên dùng "AI" làm chủ ngữ (ví dụ D3-006: "AI có thể tạo ra câu nghe rất hợp lý..."). |
| 8 | `agent_graph.py` — `finalize_and_persist` | **Bug phát sinh trong lúc migrate**: node cuối không ghi các trường bền (`covered_points`, `unresolved_misconceptions`, `attempts_by_gap`, `awaiting_transfer`, `transfer_passed`, `mastery_complete`) trở lại state top-level, nên checkpoint SQLite của lượt sau không có gì để đọc — qua HTTP nhiều lượt, coverage không tích luỹ (chỉ đường gọi `run_turn` trực tiếp mới "che" được lỗi vì luôn tự truyền lại state). | `finalize_and_persist` giờ trả về đầy đủ các trường bền ở top-level, không chỉ nhét trong `result`/`state`. |
| 9 | `agent_core.py` — `OpenAIResponsesProvider.draft_question` | **Bug phát sinh trong lúc migrate**: khi tách câu hỏi ra lời gọi model riêng, quên truyền `ground_truth`/`accepted_signals` của K-point hoặc `claim` của misconception, khiến model không biết đang dạy chủ đề gì → sinh câu hỏi lạc đề hoàn toàn (vd hỏi về "sinh vật", "sức khỏe tinh thần"). | Thêm `rubric_excerpt` (task, ground_truth, accepted_signals, misconception_claim) vào cả interface `draft_question` và node tương ứng trong `agent_graph.py`; verify lại bằng golden set thật — câu hỏi bám đúng chủ đề LLM hallucination. |

## Đã thêm (features)

- **LangGraph StateGraph** thay cho if/elif dài trong `run_turn` (chi tiết
  kiến trúc bên dưới).
- **SQLite persistence** cho session qua HTTP (`GraphSessionRunner` +
  `SqliteSaver`), sống sót qua restart server — trước đây mất hết khi restart.
- **Retry-once cho câu hỏi Socratic**: nếu câu hỏi model soạn không an toàn
  (`_question_is_safe` fail), hệ thống gửi lại model kèm lý do bị từ chối,
  thử thêm 1 lần trước khi rơi về câu mẫu tĩnh (`_fallback_question`).
- **Turn limit**: giới hạn `MAX_TURNS_PER_SESSION = 20` mỗi session — vượt
  quá sẽ trả lời gọn (`status=needs_recovery`, `session_limit_reached=True`)
  mà **không gọi model**, tránh phiên chạy vô hạn.
- **`reset` qua checkpoint thật**: `POST /api/teach` với `"reset": true` giờ
  gọi `SqliteSaver.delete_thread(session_id)` để xoá sạch checkpoint, không
  chỉ pop khỏi dict tạm như trước.

## Kiến trúc graph (`codebase/agent_graph.py`)

```
START → enforce_turn_limit
  ├─ (hết lượt) → graceful_session_limit_exit → END
  └─ (bình thường) → assess → validate_and_ground → merge_coverage_and_regression
       → decide_route
            ├─ (có target_gap) → retrieve_evidence
            │      ├─ (SHOW_RECOVERY/COMPLETE_SESSION) → apply_static_fallback → finalize_and_persist
            │      └─ (khác) → draft_question
            └─ (không có target_gap, vd out_of_scope) → draft_question
       draft_question → validate_question
            ├─ (an toàn) → finalize_and_persist
            ├─ (không an toàn, chưa retry) → draft_question (lặp lại 1 lần, kèm lý do)
            └─ (không an toàn, đã retry) → finalize_and_persist (dùng câu mẫu tĩnh)
  finalize_and_persist → END
```

Toàn bộ logic nghiệp vụ (chấm điểm, gộp coverage, quy tắc regression khi có
misconception mới, ngưỡng retry SHOW_RECOVERY...) được port **nguyên vẹn**
từ `run_turn` cũ vào các node tương ứng — không đổi hành vi ngoài 2 điểm chủ
đích: retry câu hỏi và turn limit.

## File đã thêm

| File | Nội dung |
|---|---|
| `codebase/agent_graph.py` | State schema (`TeachBackState`), toàn bộ node, `build_graph()`, `MAX_TURNS_PER_SESSION`, `GraphSessionRunner` (SQLite-checkpointed runner dùng cho HTTP server). |
| `codebase/requirements.txt` | `langgraph>=1.2,<2`, `langgraph-checkpoint-sqlite>=3.1,<4` (kéo theo `langchain-core` — không thêm `langchain-openai`, vẫn dùng `urllib` thuần cho OpenAI Responses API). |
| `codebase/MIGRATION_LANGGRAPH.md` | File này. |

## File đã sửa

| File | Thay đổi chính |
|---|---|
| `codebase/agent_core.py` | 9 bug fix ở trên; thêm `draft_question()` vào `AssessmentProvider` protocol + `OpenAIResponsesProvider` + `OfflineRuleProvider`; thêm helper `_question_rejection_reason`; `TeachBackAgent.__init__` build & compile graph; `run_turn()` giờ chỉ build input state rồi gọi `self._graph.invoke(...)` — không còn if/elif nghiệp vụ trong file này. |
| `codebase/server.py` | Bỏ `MemorySessionStore`, dùng `GraphSessionRunner` từ `agent_graph.py`; bỏ import `threading` không dùng nữa. Request/response JSON của `POST /api/teach` **không đổi**. |
| `.gitignore` | Thêm `/codebase/state/` (nơi chứa `sessions.sqlite3`, tương tự cách `/codebase/logs/` đã bị ignore). |

File **không đổi** (chỉ dùng để verify): `codebase/tests/test_agent.py`,
`codebase/run_eval.py`, `eval/golden_set.json`.

## Hướng dẫn chạy

### Cài đặt (lần đầu, hoặc sau khi pull code mới)

```powershell
.\.venv\Scripts\python.exe -m pip install -r codebase\requirements.txt
```

### Biến môi trường

`codebase/.env` đã có `OPENAI_API_KEY`/`OPENAI_MODEL`, nhưng code **không
tự động đọc file `.env`** — cần export thủ công trước khi chạy path OpenAI:

PowerShell:
```powershell
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_MODEL="gpt-4o-mini"
```

Bash (git bash):
```bash
set -a && source codebase/.env && set +a
```

### Chạy test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s codebase\tests
```

### Chạy golden set (20 case)

```powershell
# Baseline không gọi AI, không cần key
.\.venv\Scripts\python.exe codebase\run_eval.py --provider offline

# Lượt AI thật
.\.venv\Scripts\python.exe codebase\run_eval.py --provider openai
```
Kết quả ghi vào `eval/run_results.md` + `eval/results.jsonl`.

### Chạy server

```powershell
.\.venv\Scripts\python.exe codebase\server.py --provider offline --port 8000
# hoặc --provider openai (cần OPENAI_API_KEY) / --provider auto
```

Session state giờ lưu tại `codebase/state/sessions.sqlite3` (tự tạo, đã
gitignore) — restart server giữa phiên vẫn tiếp tục đúng chỗ cũ nhờ
`thread_id = session_id`.

```
POST /api/teach
{
  "session_id": "abc",
  "student_explanation": "...",
  "reset": false   // true để xoá sạch checkpoint của session này
}
```

## Đã verify

- 10/10 unit test (`tests/test_agent.py`) — không đổi hành vi.
- 20/20 golden set với `--provider offline`.
- 20/20 golden set với `--provider openai` (trước khi thêm `rubric_excerpt`
  cho câu hỏi); sau khi thêm `rubric_excerpt`, một lượt chạy lại cho 18/20 —
  2 ca lệch chỉ ở `covered_points` do model tự đánh giá thêm 1 K-point ngoài
  dự kiến, `status`/`next_action` vẫn đúng cả hai ca. Đây là **model call
  variance** (gpt-4o-mini không cố định temperature/seed), không phải do
  migration — `spec.md` cũng ghi nhận baseline OpenAI từng dao động 50%→100%
  giữa các lần chạy trước đây.
- Multi-turn K1→K2→K3→K4→transfer qua HTTP thật: coverage tích luỹ đúng qua
  từng lượt, xác nhận bug K2/K4 collision đã hết.
- Restart server giữa phiên → resume đúng từ SQLite (không reset về turn 0).
- `reset: true` → session bắt đầu lại sạch từ turn 1.
- Gửi 21+ lượt trên 1 session → turn 21 trả `session_limit_reached=True`,
  không phát sinh lời gọi model mới (chặn ở node `enforce_turn_limit` trước
  khi vào `assess`).

## Giới hạn còn lại (chưa làm trong đợt này)

- Homonym thật (không thể sửa bằng `\b`): "đúng" và "dụng" (sử dụng/áp dụng)
  đều mất dấu thành `dung` — không có ca golden/test nào bị ảnh hưởng hiện
  tại, ghi nhận làm rủi ro còn lại của `normalize_text`.
- `GraphSessionRunner` dùng 1 connection SQLite + 1 lock toàn cục — đủ cho
  quy mô demo hackathon, chưa tối ưu cho tải cao/nhiều tiến trình.
