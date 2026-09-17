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

## Hướng dẫn xây UI theo chức năng dự án

`server.py` chỉ expose `POST /api/teach` — chưa có UI thật nối vào backend.
`teach-back-prototype.html` ở repo root là **mock rời, không gọi API**, minh
hoạ cho một chủ đề khác ("Biến (Variable) là gì?"), nhưng style/component đã
được thiết kế sẵn (progress bar, checklist, chat bubble, recovery card, modal
kết thúc) — nên **tái dùng cấu trúc/style đó**, chỉ đổi nội dung/mapping dữ
liệu sang domain "Vì sao LLM có thể bịa" theo đúng field mà backend trả về.

### Luồng dữ liệu

1. UI gửi `POST /api/teach` với `{ session_id, student_explanation, reset? }`.
2. Trong lúc chờ phản hồi (có thể vài giây nếu `--provider openai` vì mỗi
   lượt gọi tối đa 2 lời gọi model: `assess` + `draft_question`), hiện trạng
   thái "đang gõ" (tái dùng `.typing-bubble` có sẵn trong prototype).
3. Nhận JSON kết quả, cập nhật toàn bộ UI theo bảng mapping bên dưới, rồi
   append `agent_response` như một bubble từ agent.
4. `session_id` phải được UI tự sinh (uuid) và giữ nguyên trong suốt phiên
   (localStorage/sessionStorage phía client) để backend tiếp tục đúng session
   qua SQLite checkpoint.

### Mapping field response → UI component

| Field trong response | Hiển thị ở đâu | Ghi chú |
|---|---|---|
| `progress` (0-100) | Thanh tiến độ (`.understanding-fill` + `%` text) | Không phải điểm thi — giữ đúng tinh thần `mastery_policy.purpose` ("Luyện tập, không phải điểm thi") khi viết copy UI. |
| `covered_points` (list K1-K4) | Checklist 4 mục, tick theo `knowledge_points[].label` trong `knowledge/d3-llm-hallucination-ground-truth.json` (K1 "Cơ chế sinh xác suất", K2 "Hợp lý không đồng nghĩa đúng", K3 "Nguồn gây thiếu hoặc sai căn cứ", K4 "Giảm rủi ro, không bảo đảm tuyệt đối") | Prototype cũ có 3 checklist item (`ui_checklist` UI1-UI3 trong ground-truth JSON gộp K3+K4 làm 1 dòng "Nêu được một nguồn rủi ro và một cách giảm rủi ro") — có thể dùng đúng 3 dòng `ui_checklist` thay vì 4 K-point riêng để giữ UI gọn như bản mock cũ. |
| `status` | Badge trạng thái (`#statusBadge`) | Map text: `mastered`→"Đang tổng kết", `partial`→"Đang học", `misconception`→"Cần sửa hiểu lầm", `needs_recovery`→"Cần xem lại gợi ý", `copied_source`→"Hãy diễn đạt lại", `out_of_scope`→"Ngoài chủ đề". |
| `next_action` | Quyết định UI phụ (có hiện recovery card không, có khoá input không...) | Xem bảng hành động bên dưới. |
| `agent_response` | Bubble chat phía agent (`.bubble-row.agent`) | Đây là **câu hỏi Socratic duy nhất mỗi lượt** — không tự thêm câu hỏi phụ ở UI, đúng nguyên tắc `agent_policy.must` ("Hỏi tối đa một câu mỗi lượt"). |
| `student_explanation` (input UI vừa gửi) | Bubble chat phía user (`.bubble-row.user`) | Tự thêm ở client ngay khi submit, không cần đợi response. |
| `misconceptions` (list M1-M6) | Banner cảnh báo nhỏ phía trên chat khi khác rỗng | Không hiện điểm số kèm theo — tránh cảm giác "chấm điểm" (`agent_policy.must_not`). |
| `recovery_card` | `.recovery-card` (chỉ hiện khi `next_action=SHOW_RECOVERY` và field khác `null`) | Nội dung lấy từ `recovery_card.text`; không tự chế thêm nội dung ngoài field này. |
| `source_cards` | Danh sách nguồn dạng chip/tooltip nhỏ dưới `recovery_card` hoặc dưới bubble agent (tuỳ thiết kế) | Mỗi item có `id/type/page hoặc segment/paraphrase` — dùng để trả lời "thông tin nằm ở đâu" theo đúng mục đích ghi trong `codebase/README.md`. |
| `citations_valid` | Không cần hiển thị trực tiếp cho học viên; dùng cho panel debug/QA nội bộ nếu có | Giá trị `false` gần như không nên xảy ra (harness đã validate) — nếu UI có chế độ debug, cảnh báo đỏ khi gặp `false`. |
| `mastery_complete` + `next_action=COMPLETE_SESSION` | Trigger modal kết thúc phiên (`#modalBackdrop`) | Chỉ true sau khi vượt transfer — copy modal nên nhấn "bạn đã dạy đủ 4 ý + qua được ví dụ chuyển giao", không dùng chữ "điểm tuyệt đối". |
| `session_limit_reached` (field mới, chỉ có khi vượt `MAX_TURNS_PER_SESSION`) | Banner nhẹ nhàng gợi ý tạm dừng, **không phải lỗi** | Khi field này `true`, khoá nút gửi và chỉ còn nút "Bắt đầu lại" (gọi lại API với `reset: true`). |
| `confidence` | Không cần hiển thị cho học viên | Nội bộ/debug; agent không được tự gán % hiểu (`spec.md`: "LLM không được tự gán phần trăm") — field này chỉ là độ tin cậy của bước chấm, không phải % hiểu bài. |
| `tool_trace`, `raw_assessment` | Không hiển thị trong UI học viên | Dữ liệu debug/audit; nếu cần panel dev, để ẩn sau một toggle riêng, không lẫn vào luồng học chính. |

### Hành động (`next_action`) → hành vi UI

| `next_action` | UI nên làm gì |
|---|---|
| `ASK_MECHANISM` / `ASK_CAUSE` / `ASK_MITIGATION` / `ASK_EXAMPLE` / `ASK_TRANSFER` | Hiện câu hỏi (`agent_response`) như bình thường, input mở để học viên trả lời tiếp. |
| `SOCRATIC_CORRECTION` | Giống trên nhưng có thể tô nhẹ màu cảnh báo (amber) cho bubble, vì đang sửa hiểu lầm — không dùng màu đỏ/rose để tránh cảm giác bị "bắt lỗi". |
| `ASK_REPHRASE` | Hiện gợi ý nhỏ "hãy diễn đạt lại bằng lời của bạn" cạnh input (trường hợp `copied_source`). |
| `SHOW_RECOVERY` | Mở `.recovery-wrap` (đã có sẵn animation trong prototype), khoá tạm nút gửi câu hỏi tiếp cho đến khi học viên bấm "Tôi đã đọc xong, thử dạy lại". |
| `OUT_OF_SCOPE` | Bubble nhắc quay lại chủ đề; không tick/untick checklist. |
| `COMPLETE_SESSION` | Trigger modal kết thúc + hiệu ứng confetti (đã có `#confetti-layer` trong prototype). |

### Nút phụ nên có (dựa theo `allowed_actions` và mock cũ)

- **"Tôi không nhớ / xem lại gợi ý"** (giống `#recoveryBtn` trong mock) — gửi
  một `student_explanation` rỗng/ngắn hợp lệ hoặc field riêng để chủ động xin
  `SHOW_RECOVERY` mà không cần đợi trả lời sai; nếu muốn giữ contract hiện
  tại đơn giản, có thể map nút này thành gửi câu trả lời dạng "Không biết"
  (backend đã nhận diện qua `INSUFFICIENT_PATTERNS`).
- **"Bắt đầu lại"** — gọi lại API với `reset: true`, xoá luôn state UI phía
  client (progress, checklist, chat).
- Không cần nút "hint" tách riêng khỏi flow — agent đã tự quyết định
  `ASK_*` phù hợp mỗi lượt theo đúng nguyên tắc "Hỏi tối đa một câu mỗi lượt,
  nhắm vào khoảng trống quan trọng nhất" (`agent_policy.must`).

### Việc KHÔNG nên làm trong UI

- Không tự tính lại % hiểu ở client — luôn dùng `progress` từ server.
- Không hiển thị số phần trăm gắn với từng câu trả lời như một bài kiểm tra
  có điểm (`mastery_policy.purpose`: "Luyện tập, không phải điểm thi").
- Không lộ `raw_assessment`/nội dung K1-K4 đầy đủ cho học viên trước khi họ
  tự nói ra — đó là dữ liệu chấm nội bộ, không phải đáp án để hiển thị.
- Không gọi thẳng `codebase/knowledge/...json` từ frontend để "gợi ý trước"
  câu trả lời — phá vỡ mục đích teach-back (học viên dạy lại agent, không
  phải đọc đáp án).

### Test UI thủ công sau khi build

1. Chạy `codebase/server.py --provider offline` để test UI không tốn API
   call, dùng script curl mẫu trong phần "Chạy server" ở trên làm tham chiếu
   field response.
2. Đi qua đủ 4 nhánh trạng thái: `partial` nhiều lượt → `mastered` →
   `COMPLETE_SESSION`; một lượt chứa misconception (vd nhắc "temperature
   bằng 0") để xem banner cảnh báo; một lượt input rỗng ("Không biết") để
   xem `recovery_card`; một câu hoàn toàn ngoài chủ đề để xem `OUT_OF_SCOPE`.
3. Bấm "Bắt đầu lại" (`reset: true`) và xác nhận UI với state trở về sạch.
4. Đổi sang `--provider openai` (cần `OPENAI_API_KEY`) để xem UI xử lý đúng
   độ trễ thật của 1-2 lời gọi model mỗi lượt.

## Checklist đối chiếu CP3 (còn thiếu trước khi nộp)

Rà lại toàn bộ yêu cầu CP3 ("Xây dựng prototype AI thật và đo lường kiểm thử
sơ bộ") so với trạng thái repo hiện tại (nhánh `tung`, commit `c68cf73`).
Mục đích của phần này là **liệt kê rõ việc còn thiếu**, không tự ý sửa code/
data/git — các quyết định (thêm ca golden set, merge main, nộp video...) để
bạn hoặc leader nhóm chủ động thực hiện.

### 1. Đã đạt (có bằng chứng cụ thể)

| Yêu cầu CP3 | Bằng chứng |
|---|---|
| Module quyết định trung tâm gọi AI thật, không gán cứng | `codebase/agent_core.py` → `OpenAIResponsesProvider` gọi thật OpenAI Responses API qua `urllib`; đã verify sống bằng API key thật (golden set 20/20 rồi 18/20 tuỳ lượt chạy — xem mục "Đã verify" ở trên). |
| Logging prompt + raw response của model | `AuditLogger` ghi `model_prompt`/`model_raw_response` vào `codebase/logs/model_calls.jsonl` (bị gitignore — cần đính kèm log mẫu khi nộp nếu ban tổ chức muốn xem bằng chứng kỹ thuật, vì thư mục này không lên repo công khai). |
| `eval/golden_set.json` đủ 20 ca, có script chạy hàng loạt | `codebase/run_eval.py --provider openai/offline`, output `eval/results.jsonl` + `eval/run_results.md`. |
| `eval/run_results.md` có bảng đạt/không đạt/tỷ lệ % | Có, kèm bảng theo taxonomy và bảng chi tiết 20 ca. |
| Giữ dấu vết lượt chạy đầu tiên, không "làm đẹp" số liệu | `eval/run_results_openai_v1.md` giữ nguyên kết quả thật đầu tiên **10/20 (50%)**, không bị ghi đè bởi các lượt sau — đúng tinh thần CP3: "kết quả 12/20 nhưng phân tích sâu vẫn nhận trọn điểm". |

### 2. Còn thiếu / cần quyết định

**a) Golden set thiếu ca lớp "③ Ngoài phạm vi/thẩm quyền" (mới có 1/2 tối thiểu)**

CP3 yêu cầu tối thiểu 2 ca cho mỗi lớp trong 4 lớp (① Nguồn sự thật,
② Mơ hồ/thiếu thông tin, ③ Ngoài phạm vi/thẩm quyền, ④ Đặc thù nghiệp vụ).
Taxonomy nội bộ dự án (`L1_INPUT/L2_SEMANTIC/L3_GROUNDING/L4_DIALOGUE`, xem
`spec.md` mục §5) ánh xạ gần đúng như sau:

| Taxonomy CP3 | Taxonomy dự án | Số ca hiện có | Đạt tối thiểu 2? |
|---|---|---:|---|
| ① Nguồn sự thật | `L3_GROUNDING` (D3-003, 004, 007, 008, 014) | 5 | ✅ |
| ② Mơ hồ/thiếu thông tin | `L1_INPUT` — nhánh insufficient (D3-015 "Không biết", D3-017 "Bịa là bịa thôi ạ") | 2 | ✅ (vừa đủ) |
| ③ Ngoài phạm vi/thẩm quyền | `L1_INPUT` — nhánh out_of_scope (**chỉ D3-016** "Em thích học bằng video hơn...") | **1** | ❌ chưa đủ |
| ④ Đặc thù nghiệp vụ | `L4_DIALOGUE` (D3-010, 011, 012, 013, 020 — misconception, regression, transfer) | 5 | ✅ |

D3-018 (copied_source) và D3-019 (tiếng Việt không dấu) là 2 ca L1_INPUT còn
lại, không thuộc nhóm ③ — chúng là edge case khác (sao chép nguồn / lỗi
chính tả đầu vào), không tính vào "ngoài phạm vi".

**Đề xuất case mới để bù (chưa thêm vào `golden_set.json`, chỉ nháp ở đây):**

```json
{
  "id": "D3-021",
  "taxonomy_layer": "L1_INPUT",
  "input": "Cô ơi hôm nay lớp mình học xong sớm không ạ, em muốn về sớm.",
  "expected_status": "out_of_scope",
  "expected_covered": [],
  "expected_missing": ["K1", "K2", "K3", "K4"],
  "expected_misconceptions": [],
  "expected_action": "OUT_OF_SCOPE",
  "required_evidence_any": []
}
```
Nếu thêm ca này, `run_eval.py`'s `validate_dataset()` sẽ cần cập nhật ràng
buộc "đúng 20 ca / đúng 5 ca mỗi lớp" (hiện `validate_dataset` ép cứng
`len(cases) != 20` và `layer_counts[layer] != 5` — xem `run_eval.py`) — tức
là phải **thay** một ca L1_INPUT hiện có bằng ca out-of-scope mới, không
phải thêm thành 21 ca, trừ khi cũng sửa lại hai ràng buộc cứng đó.

**b) Chưa xác minh được "≥10 ca trích trực tiếp từ dữ liệu/hội thoại thật"**

`data/slides/`, `data/transcript/` bị `.gitignore` (đúng luật bảo mật của
ban tổ chức), nên không thể đối chiếu từ repo. `eval/golden_set.json` có
trường `required_evidence_any` trỏ tới `source_ids` thật (vd `D1-S11`,
`T06-136`) trong `knowledge/d3-llm-hallucination-ground-truth.json`, nhưng
đó là nguồn để **chấm điểm**, không chứng minh nội dung `input` (câu trả lời
mô phỏng của "học viên") được trích trực tiếp từ hội thoại/khảo sát thật.
→ **Cần leader/nhóm tự xác nhận và ghi chú rõ** bao nhiêu trong 20 ca thực
sự lấy từ dữ liệu mining thật (vd phỏng vấn, khảo sát) vs. tự viết mô phỏng,
để tránh vi phạm nguyên tắc trung thực nếu bị hỏi trực tiếp.

**c) Phần "Phân tích sai lệch" trong `eval/run_results.md` còn hời hợt**

Bản hiện tại (18/20, provider openai, lượt gần nhất) chỉ có giải thích tự
động chung chung:
> "covered_points: 2 ca. Nhận diện semantic coverage K1-K4 chưa chính xác."
> "missing_points: 2 ca. Knowledge gap suy ra chưa khớp ground truth."

**Nguyên nhân gốc rễ thực tế** (rút ra khi debug 2 ca fail D3-017/D3-018
trong phiên làm việc này, nên chép/diễn giải lại vào `run_results.md` hoặc
báo cáo CP3 nếu muốn phần phân tích đạt độ sâu CP3 yêu cầu):

- **D3-017** (`"Bịa là bịa thôi ạ."`, expected `covered_points=[]`): model
  gpt-4o-mini tự đánh giá thêm `K2 = supported` dù input gần như không có
  nội dung để chấm — `status`/`next_action` (`needs_recovery`/
  `SHOW_RECOVERY`) vẫn đúng, chỉ verdict K-point của model bị "hào phóng"
  hơn kỳ vọng.
- **D3-018** (đoạn mô tả cơ chế K1 — token/phân bố xác suất/vòng lặp tự hồi
  quy — expected `covered_points=["K1"]`): model tự đánh giá thêm
  `K3 = supported`, nhiều khả năng vì input có nhắc từ "ngữ cảnh" một lần
  (thuộc câu mô tả K1) và model liên tưởng sang khái niệm K3 (context
  window/cutoff) dù ý gốc không nói về giới hạn ngữ cảnh. `status`/
  `next_action` (`copied_source`/`ASK_REPHRASE`) vẫn đúng.
- **Cả hai đều là model call variance** (gpt-4o-mini không cố định
  temperature/seed), không phải lỗi harness/regex/graph — harness đã đúng
  chức năng validate evidence (`_evidence_is_grounded`) chỉ chặn được
  misconception bịa bằng chứng, chưa chặn được model gán "supported" quá
  tay cho một K-point khi có 1 từ khoá liên quan xuất hiện tình cờ trong
  câu. Đây là giới hạn đã biết của kiến trúc "model đề xuất, harness kiểm
  chứng bằng-chứng-trực-tiếp" — siết thêm sẽ cần thêm điều kiện ngữ nghĩa
  chặt hơn ở `_validate_assessment`, chưa làm trong đợt này.
- `spec.md` đã ghi nhận baseline OpenAI từng dao động 50%→100% giữa các lần
  chạy khác nhau trước đây — biến thiên 90%↔100% ở đợt này nằm trong cùng
  loại rủi ro đã biết, không phải regression từ việc migrate LangGraph.

**d) `git push origin main` chưa thực hiện**

Toàn bộ code (bao gồm LangGraph migration + fix bug) đang nằm trên nhánh
`tung`, đã push lên `origin/tung`. Nhánh `main` vẫn ở commit `4163742`
("add gitignore + data") — **chưa có code/eval mới nhất**. Lệnh CP3 yêu cầu
push thẳng `main`:
```bash
git add codebase/ eval/
git commit -m "feat: integrate live AI call and document run 1 eval results"
git push origin main
```
Cần leader xác nhận cách merge mong muốn trước khi chạy (merge `tung` vào
`main` trực tiếp, hay qua Pull Request để review trước) — đây là hành động
ảnh hưởng nhánh chính nên không tự ý thực hiện.

**e) Video 30s + nộp form CP3**

Chưa thể xác minh từ repo. Cần quay màn hình thao tác thật: nhập câu trả
lời → gửi request → nhận `agent_response` từ model thật theo thời gian
thực (không cắt ghép), rồi đội trưởng nộp qua form CP3 trước **16:00
17/9** kèm số đo lượt đầu.

**f) Deadline**

CP3 hạn **16:00 17/9**; CP4 (khoá spec) hạn **21:00 17/9** cùng ngày (theo
`README.md`). Cần tự kiểm tra đồng hồ hiện tại — đây là hạn gấp, hai mốc
cách nhau chỉ 5 tiếng.
