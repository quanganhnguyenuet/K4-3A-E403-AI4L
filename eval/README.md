# D3 evaluation dataset

## Lát cắt

Một học viên dạy lại cho agent: **“Vì sao LLM có thể bịa (hallucinate)?”** Agent đối chiếu lời giải thích với nguồn bài học, hỏi đúng khoảng trống và chỉ hoàn thành phiên khi misconception nghiêm trọng đã được sửa.

Ground truth: `../knowledge/d3-llm-hallucination-ground-truth.json`.

Golden set: `d3-golden-set.jsonl` gồm 20 cách trả lời, phủ các hard test của D3:

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
  "missing_points": ["K2", "K3"],
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

Chỉ số CP3 nên công bố:

```text
pass_rate = số case đạt đủ 6 điều kiện / 20
```

Ghi cả bảng lỗi; không chỉ ghi phần trăm tổng.

## Bảo mật

Không đưa `data/slides/` hoặc `data/transcript/` lên repo công khai. Hai file dataset này chỉ lưu diễn giải ngắn và mã định vị nguồn.
