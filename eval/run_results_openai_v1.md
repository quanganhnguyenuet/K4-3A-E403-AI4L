# D3 - Kết quả chạy golden set lần đầu

- Thời điểm UTC: `2026-09-16T18:16:05.826163+00:00`
- Provider: `openai`
- Tổng số ca: **20**
- Đạt: **10**
- Không đạt: **10**
- Tỷ lệ đạt: **50.0%**

## Kết quả theo taxonomy

| Lớp | Đạt | Tổng | Tỷ lệ |
|---|---:|---:|---:|
| L1_INPUT | 1 | 5 | 20.0% |
| L2_SEMANTIC | 3 | 5 | 60.0% |
| L3_GROUNDING | 2 | 5 | 40.0% |
| L4_DIALOGUE | 4 | 5 | 80.0% |

## Chi tiết 20 ca

| Case | Lớp | Kết quả | Status thực tế | Action thực tế | Kiểm tra sai |
|---|---|---|---|---|---|
| D3-001 | L2_SEMANTIC | Đạt | mastered | ASK_TRANSFER | - |
| D3-002 | L2_SEMANTIC | Không đạt | partial | ASK_CAUSE | covered_points, missing_points, next_action |
| D3-003 | L3_GROUNDING | Không đạt | partial | ASK_CAUSE | status, covered_points, missing_points, next_action |
| D3-004 | L3_GROUNDING | Đạt | mastered | ASK_TRANSFER | - |
| D3-005 | L2_SEMANTIC | Không đạt | misconception | SOCRATIC_CORRECTION | status, covered_points, missing_points, misconceptions, next_action, evidence_hit |
| D3-006 | L2_SEMANTIC | Đạt | partial | ASK_MECHANISM | - |
| D3-007 | L3_GROUNDING | Không đạt | misconception | SOCRATIC_CORRECTION | status, covered_points, missing_points, misconceptions, next_action |
| D3-008 | L3_GROUNDING | Không đạt | partial | ASK_MECHANISM | covered_points, missing_points |
| D3-009 | L2_SEMANTIC | Đạt | partial | ASK_CAUSE | - |
| D3-010 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | - |
| D3-011 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | - |
| D3-012 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | - |
| D3-013 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | - |
| D3-014 | L3_GROUNDING | Đạt | misconception | SOCRATIC_CORRECTION | - |
| D3-015 | L1_INPUT | Đạt | needs_recovery | SHOW_RECOVERY | - |
| D3-016 | L1_INPUT | Không đạt | needs_recovery | SHOW_RECOVERY | status, next_action |
| D3-017 | L1_INPUT | Không đạt | misconception | SOCRATIC_CORRECTION | status, misconceptions, next_action |
| D3-018 | L1_INPUT | Không đạt | partial | ASK_CAUSE | status, next_action |
| D3-019 | L1_INPUT | Không đạt | partial | ASK_MITIGATION | status, covered_points, missing_points, next_action |
| D3-020 | L4_DIALOGUE | Không đạt | misconception | SOCRATIC_CORRECTION | covered_points, missing_points, misconceptions, evidence_hit |

## Phân tích sai lệch

- **next_action: 8 ca.** Policy chọn câu hỏi/hành động tiếp theo chưa phù hợp.
- **covered_points: 7 ca.** Nhận diện semantic coverage K1-K4 chưa chính xác.
- **missing_points: 7 ca.** Knowledge gap suy ra chưa khớp ground truth.
- **status: 7 ca.** Phân loại trạng thái tổng chưa khớp nhãn.
- **misconceptions: 4 ca.** Bỏ sót hoặc báo nhầm misconception; đây là lỗi rủi ro cao.
- **evidence_hit: 2 ca.** Retriever chưa đưa ra một nguồn nằm trong nhóm nguồn mong đợi.

### Case cần xem lại

- `D3-002`: expected status/action `partial/ASK_MITIGATION`, actual `partial/ASK_CAUSE`; sai ở covered_points, missing_points, next_action.
- `D3-003`: expected status/action `mastered/ASK_TRANSFER`, actual `partial/ASK_CAUSE`; sai ở status, covered_points, missing_points, next_action.
- `D3-005`: expected status/action `partial/ASK_CAUSE`, actual `misconception/SOCRATIC_CORRECTION`; sai ở status, covered_points, missing_points, misconceptions, next_action, evidence_hit.
- `D3-007`: expected status/action `partial/ASK_MECHANISM`, actual `misconception/SOCRATIC_CORRECTION`; sai ở status, covered_points, missing_points, misconceptions, next_action.
- `D3-008`: expected status/action `partial/ASK_MECHANISM`, actual `partial/ASK_MECHANISM`; sai ở covered_points, missing_points.
- `D3-016`: expected status/action `out_of_scope/OUT_OF_SCOPE`, actual `needs_recovery/SHOW_RECOVERY`; sai ở status, next_action.
- `D3-017`: expected status/action `needs_recovery/SHOW_RECOVERY`, actual `misconception/SOCRATIC_CORRECTION`; sai ở status, misconceptions, next_action.
- `D3-018`: expected status/action `copied_source/ASK_REPHRASE`, actual `partial/ASK_CAUSE`; sai ở status, next_action.
- `D3-019`: expected status/action `mastered/ASK_TRANSFER`, actual `partial/ASK_MITIGATION`; sai ở status, covered_points, missing_points, next_action.
- `D3-020`: expected status/action `misconception/SOCRATIC_CORRECTION`, actual `misconception/SOCRATIC_CORRECTION`; sai ở covered_points, missing_points, misconceptions, evidence_hit.

## Quality bar đề xuất

- Ít nhất **16/20 ca đạt (80%)**.
- **0 false-mastered** trên các ca có misconception.
- **100% citation hợp lệ** và nằm trong kết quả retrieval của lượt đó.
- Không kết thúc phiên khi còn misconception chưa được xử lý.
