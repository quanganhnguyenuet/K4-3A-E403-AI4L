# Template AI Spec *(spec.md — commit trước hạn chốt spec: 21:00 17/9, tại CP4 · quality bar chốt từ thời điểm nộp)*

> Cấu trúc phủ đúng "SPEC 8 phần" của chương trình: Bằng chứng (§1-§2) · Lát cắt (§4) · Canvas (đính kèm CP1) · Augment/Automate (§4) · 4 đường đi của trải nghiệm (§6) · Kiểu lỗi (§5) · Kiểm thử (§7) · Phân công (§8). Hướng dẫn viết từng mục: `02-guide.md`.

```markdown
# AI SPEC — [Tên lát cắt] · Nhóm [XX] · Zone [X]
Hướng: [ ] A — VLearn  [ ] B — Trợ lý Học viên  [ ] C — Làn mở
Loại: [ ] Tối ưu tính năng có sẵn  [ ] Tính năng mới

## §1. User & Job
- Job executor + workflow (đính kèm worksheet JTBD / ảnh sơ đồ):
- Core JTBD (không tên sản phẩm/AI trong câu):
- Problem statement (KHÔNG chữ AI):
- Evidence (chuẩn A và/hoặc B — log đầy đủ trong repo):
  - Số liệu mining / kết quả khảo sát (n = ?, % xác nhận):
  - ≥5 quote/ví dụ nguyên văn + nguồn:

## §2. Impact & quyết định chọn
- Bảng impact ≥3 ứng viên (bao nhiêu người · tần suất · tốn gì mỗi lần · khả thi):
- Ứng viên ĐÃ LOẠI + vì sao:
- Ứng viên CHỌN + vì sao (bằng số):

## §3. Giải pháp tương tự đã nghiên cứu
- [Sản phẩm 1]: flow / đáng học / đáng né / mình khác gì
- [Sản phẩm 2]: ...

## §4. Thiết kế
- Lát cắt MỘT CÂU (1 user · 1 việc · 1 quyết định AI · 1 kết quả):
- Non-goals (≥3 thứ KHÔNG build):
- Mức prototype nhắm tới: [ ] Sketch [ ] Mock [ ] Working — phần nào mock, phần nào thật:
- Automation: [ ] augment [ ] conditional [ ] automate — lý do theo cost-of-error:
- §4b. Nguyên tắc đã áp dụng (≥4 — HAX/PAIR, xem guide):
  | Nguyên tắc | Áp cụ thể vào đâu trong prototype |
  |---|---|

## §5. Kiểu lỗi — 4 lớp chỗ khó + kịch bản (≥8) [bảng theo guide §2.5]

| Lớp | Chỗ khó | Kịch bản đại diện | Hành vi an toàn |
|---|---|---|---|
| L1_INPUT | Đầu vào quá ngắn | "Không biết" / "Bịa là bịa" | Hiện recovery card, không tự suy ra misconception |
| L1_INPUT | Ngoài phạm vi | Học viên nói về video hoặc đường truyền | Đưa về đúng câu hỏi, không chấm kiến thức |
| L1_INPUT | Sao chép nguồn | Dán gần nguyên văn slide | Yêu cầu diễn đạt lại và đưa ví dụ riêng |
| L2_SEMANTIC | Đúng nhưng khác chữ | "Bàn phím gợi ý mạnh hơn" | Chấm theo nghĩa, không exact keyword |
| L2_SEMANTIC | Đúng một phần | Chỉ giải thích dự đoán token | Ghi nhận K1 và hỏi đúng gap tiếp theo |
| L3_GROUNDING | Nhầm nguyên nhân | Bỏ sót cutoff/context/dữ liệu lệch | Retrieve nguồn gắn với knowledge gap |
| L3_GROUNDING | Nguồn không khớp | Citation thuộc bài nhưng không hỗ trợ claim | Chỉ dẫn nguồn nằm trong retrieval result |
| L4_DIALOGUE | Hiểu sai tự tin | "RAG chính xác 100%" | Socratic correction; không complete |
| L4_DIALOGUE | Regression | Đã hiểu rồi nhưng lượt sau phát biểu M6 | Thu hồi K3/K4 đang xung đột |
| L4_DIALOGUE | Dừng quá sớm | Đủ K1-K4 nhưng chưa có transfer example | Giữ 90%; chỉ complete sau transfer pass |

## §6. Bốn đường đi của trải nghiệm
- Happy path: · Low-confidence (②): · Failure/không căn cứ (①): · Correction (user sửa):
- Khi bị đòi ngoài phạm vi (③): · Case đặc thù domain (④):

## §7. Kiểm thử
- Chiều chất lượng: status, K1-K4 coverage, misconception, next action, evidence hit, citation validity, answer leak và mastery stop condition.
- Golden set: `eval/golden_set.json`, đúng 20 case; 5 case cho mỗi lớp L1-L4; có multi-turn, regression và `COMPLETE_SESSION`.
- Quality bar: **đạt khi ≥80% case qua toàn bộ checks, 0 false-mastered trên case misconception, 100% citation hợp lệ và không complete trước transfer pass.**
- Kết quả các lượt chạy:

| Dataset | Provider | Đạt | Ghi chú |
|---|---|---:|---|
| v1.0 | OpenAI | 10/20 (50%) | Baseline thật, lưu tại `eval/run_results_openai_v1.md` |
| v1.1 | Offline rules | 20/20 (100%) | Chỉ xác minh harness; phải chạy lại OpenAI trước demo |

## §8. Phân công & kế hoạch
- Phân công có tên: spec / evidence / prompt / code / demo
- Willing users (≥2 tên) + kế hoạch vòng validation *(bonus, nếu làm)*:
- Multi-prototype (nếu làm): trục khác biệt của ≥2 phương án + lý do chọn:

## §9. Changelog
| Thời điểm | Đổi gì | Vì sao (trỏ về feedback/case nào) |
|---|---|---|
| 17/09 | Bắt buộc evidence cho misconception; thêm semantic guard K1-K4 | V1 sai ở D3-002, 003, 005, 007, 008, 019, 020 |
| 17/09 | Tách out-of-scope và insufficient; thêm copy detector | V1 sai ở D3-016, 017, 018 |
| 17/09 | Thêm multi-turn transfer pass và regression | Golden v1.0 chưa kiểm tra điều kiện hoàn thành phiên |
```
