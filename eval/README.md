# D3 evaluation dataset

## Lát cắt

Một học viên dạy lại cho agent: **“Vì sao LLM có thể bịa (hallucinate)?”** Agent đối chiếu lời giải thích với nguồn bài học, hỏi đúng khoảng trống và chỉ hoàn thành phiên khi misconception nghiêm trọng đã được sửa.

Ground truth: `../knowledge/d3-llm-hallucination-ground-truth.json`.

## Golden set

`golden_set.json` là golden set **duy nhất** trong repo, dùng cho `codebase/run_eval_risk.py` — 30 case gắn taxonomy rủi ro sản phẩm 4 lớp (L1_INPUT/L2_SEMANTIC/L3_GROUNDING/L4_DIALOGUE, đọc đúng nghĩa ở field `taxonomy` trong chính file, không suy ra từ tên khoá). File này gộp lại từ 3 bộ cũ đã bị xoá khỏi repo ngày 17/09:

- Bộ K1-K4 cũ (từng tên `golden_set.json`, 20→28 case, dùng cho `run_eval.py` — script này cũng đã bị xoá vì không còn dataset schema tương thích để chạy).
- `golden_set_v1.json` (v1.0, risk-taxonomy, dùng cho CP4).
- `golden_set_v1_1.json` (v1.1, sửa nhãn mâu thuẫn `GS1-029`, chuẩn hóa intent/action và khóa nghĩa evidence) — nội dung của bản này chính là `golden_set.json` hiện tại.

Quy ước evidence: `grounded_claims` chứng minh ý learner đã phát biểu; `evidence_source_ids`/`source_cards` là nguồn dùng cho phản hồi hoặc knowledge gap được hỏi tiếp theo.

File `d3-golden-set.jsonl` là bản nháp lịch sử, không được dùng để chấm và có thể có nhãn cũ.

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="gpt-5-mini"
D:\Conda\python.exe codebase/run_eval_risk.py --pipeline web --golden eval/golden_set.json
```

Runner chỉ chạy online bằng OpenAI và dừng ngay nếu thiếu `OPENAI_API_KEY`; không có
fallback offline. Kết quả chính thức vẫn cần hoàn tất checklist `hard_constraints`
bởi hai người chấm (xem `grading_protocol` trong chính `golden_set.json`).

Các lớp rủi ro kiểm tra gồm:

- đúng nhưng diễn đạt khác tài liệu;
- đúng một phần;
- sai nhưng tự tin;
- dán lại câu từ nguồn;
- không nhớ, trả lời quá ngắn hoặc ngoài phạm vi;
- tiếng Việt không dấu;
- hiểu sai về temperature, RAG, knowledge cutoff và context.

## Artifact mỗi lần chạy

Mỗi lần chạy `run_eval_risk.py` tạo bộ bốn file `*_summary.json`, `*_results.jsonl`,
`*_report.md` và model-call log trong `runs/`, nên các lần chạy không ghi đè lịch sử
audit. `run_history.jsonl` là chỉ mục cộng dồn giữa các lần chạy — mỗi dòng chứa run
ID, provider/model, accuracy toàn bộ và accuracy theo từng taxonomy layer. `run_results_v1.md`
luôn là lượt chạy gần nhất; kiểm tra trường `Provider`/`Dataset` trước khi dùng con số
trong demo. Kết quả OpenAI v1.0 cũ (chạy trên `golden_set_v1.json` trước khi gộp file)
được giữ ở `run_results_openai_v1.md`/`results_openai_v1.jsonl` làm lịch sử đối chiếu,
không dùng để công bố chất lượng model hiện tại. API key không được ghi vào bất kỳ
artifact nào.

## Input cho flow_app

Mỗi lượt gửi lên backend tối thiểu gồm:

```json
{
  "dataset_id": "d3-teach-back-why-llm-hallucinates-v1",
  "session_id": "demo-session-01",
  "turn": 1,
  "student_explanation": "LLM dự đoán token tiếp theo...",
  "previous_state": {
    "covered_points": [],
    "resolved_misconceptions": [],
    "failed_attempts": 0
  }
}
```

Output phải tuân theo `response_contract` trong ground truth. Ví dụ:

```json
{
  "status": "partial",
  "covered_points": ["K1"],
  "missing_points": ["K2", "K3", "K4"],
  "misconceptions": [],
  "next_action": "ASK_CAUSE",
  "agent_response": "Nếu model chọn phần tiếp theo nghe hợp lý, điều gì khiến phần đó vẫn có thể sai sự thật?",
  "evidence_source_ids": ["D1-S11", "T06-136"]
}
```

## Cách tính một case đạt

`auto_score()` trong `run_eval_risk.py` chỉ chấm được các field máy kiểm được — field nào
vắng mặt trong case (`expected_status`, `expected_action`, `expected_covered`, v.v.) coi
như không áp dụng cho case đó:

1. `status` khớp `expected_status`/`acceptable_status`.
2. `next_action` khớp `expected_action`/`acceptable_action`.
3. `covered_points`/`missing_points`/`misconceptions` khớp đúng tập kỳ vọng (hoặc một trong `acceptable_misconceptions`).
4. `evidence_source_ids` không chứa ID nào ngoài registry nguồn (`no_fabricated_evidence`) và giao khác rỗng với `required_evidence_any` khi case yêu cầu.

Phần còn lại — mỗi `hard_constraints` của case — không tự động chấm được, phải do
**hai người chấm độc lập** đọc `agent_response` thật và xác nhận nhị phân đúng/sai
(quy trình đầy đủ nằm ở `grading_protocol` trong `golden_set.json`). Auto-check 100%
không đồng nghĩa đã đạt Quality Bar chính thức — xem `codebase/spec.md` §7.

## Bảo mật

Không đưa `data/slides/` hoặc `data/transcript/` lên repo công khai. Hai file dataset này chỉ lưu diễn giải ngắn và mã định vị nguồn.
