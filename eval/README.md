# D3 evaluation dataset

## Lát cắt

Một học viên dạy lại cho agent: **“Vì sao LLM có thể bịa (hallucinate)?”** Agent đối chiếu lời giải thích với nguồn bài học, hỏi đúng khoảng trống và chỉ hoàn thành phiên khi misconception nghiêm trọng đã được sửa.

Ground truth: `../knowledge/d3-llm-hallucination-ground-truth.json`.

Golden set chính và duy nhất được runner sử dụng: `golden_set.json`, gồm 28 cách trả lời được gắn taxonomy 4 lớp. Version 1.2 bổ sung prompt injection/out-of-scope, câu đúng phủ định một hiểu sai, câu vừa đúng vừa sai, sai lặp lại cần recovery và ca sửa được misconception. File `d3-golden-set.jsonl` là bản nháp lịch sử, không được dùng để chấm và có thể có nhãn cũ.

Kết quả OpenAI v1.0 được giữ ở `run_results_openai_v1.md` và `results_openai_v1.jsonl` để so sánh trước-sau. `run_results.md` luôn là lượt chạy gần nhất của dataset hiện hành; kiểm tra trường `Provider` trước khi dùng con số trong demo. Mỗi lần chạy mới còn tạo bộ ba `*_summary.json`, `*_results.jsonl`, `*_report.md` và model-call log trong `runs/`, nên các lần chạy không ghi đè lịch sử audit.

`run_history.jsonl` là chỉ mục cộng dồn giữa các lần chạy. Mỗi dòng chứa run ID, provider/model, accuracy toàn bộ và accuracy theo từng taxonomy. Trong file `*_results.jsonl`, mỗi case lưu cả `session_id`, `turn`, provider, kết quả pass/fail và `accuracy` 0 hoặc 1. API key không được ghi vào bất kỳ artifact nào.

- đúng nhưng diễn đạt khác tài liệu;
- đúng một phần;
- sai nhưng tự tin;
- dán lại câu từ nguồn;
- không nhớ, trả lời quá ngắn hoặc ngoài phạm vi;
- tiếng Việt không dấu;
- hiểu sai về temperature, RAG, knowledge cutoff và context.

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

Một case chỉ đạt khi đồng thời:

1. `status` bằng `expected_status`.
2. Không bỏ sót hoặc bịa thêm misconception.
3. `next_action` bằng `expected_action`.
4. Có ít nhất một `evidence_source_id` thuộc `required_evidence_any`, trừ case ngoài phạm vi.
5. Câu hỏi tiếp theo không tiết lộ toàn bộ đáp án.
6. Source ID trả về tồn tại trong ground truth.
7. Kết quả có `diagnosis` có cấu trúc; nếu sai, phải có misconception ID và lý do sai.
8. Mỗi claim đúng được ghi trong `grounded_claims` với source ID thuộc registry.

Chỉ số CP3 nên công bố:

```text
pass_rate = số case đạt toàn bộ điều kiện / 28
```

Ghi cả bảng lỗi; không chỉ ghi phần trăm tổng.

Chạy baseline không tốn API:

```powershell
D:\conda\python.exe codebase/run_eval.py --provider offline
```

Chạy model thật sau khi đã đặt `OPENAI_API_KEY`:

```powershell
D:\conda\python.exe codebase/run_eval.py --provider openai
```

## Bảo mật

Không đưa `data/slides/` hoặc `data/transcript/` lên repo công khai. Hai file dataset này chỉ lưu diễn giải ngắn và mã định vị nguồn.
