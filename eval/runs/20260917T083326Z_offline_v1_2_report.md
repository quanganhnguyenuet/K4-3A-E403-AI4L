# D3 - Kết quả chạy golden set

- Thời điểm UTC: `2026-09-17T08:33:26.129002+00:00`
- Dataset version: `1.2`
- Provider: `offline_rule_baseline`
- Tổng số ca: **28**
- Đạt: **28**
- Không đạt: **0**
- Tỷ lệ đạt: **100.0%**

> **Lưu ý:** Đây là lượt chạy baseline bằng luật cục bộ, không gọi API và không phát sinh chi phí. Kết quả này xác minh harness, retrieval, citation và runner; không được trình bày như kết quả model AI thật. Chạy lại với `--provider openai` trước video CP3.

## Kết quả theo taxonomy

| Lớp | Đạt | Tổng | Tỷ lệ |
|---|---:|---:|---:|
| L1_INPUT | 7 | 7 | 100.0% |
| L2_SEMANTIC | 7 | 7 | 100.0% |
| L3_GROUNDING | 7 | 7 | 100.0% |
| L4_DIALOGUE | 7 | 7 | 100.0% |

## Chi tiết 28 ca

| Case | Lớp | Kết quả | Status thực tế | Action thực tế | Complete | Kiểm tra sai |
|---|---|---|---|---|---|---|
| D3-001 | L2_SEMANTIC | Đạt | mastered | ASK_TRANSFER | no | - |
| D3-002 | L2_SEMANTIC | Đạt | partial | ASK_MITIGATION | no | - |
| D3-003 | L3_GROUNDING | Đạt | mastered | ASK_TRANSFER | no | - |
| D3-004 | L3_GROUNDING | Đạt | mastered | ASK_TRANSFER | no | - |
| D3-005 | L2_SEMANTIC | Đạt | partial | ASK_CAUSE | no | - |
| D3-006 | L2_SEMANTIC | Đạt | partial | ASK_MECHANISM | no | - |
| D3-007 | L3_GROUNDING | Đạt | partial | ASK_MECHANISM | no | - |
| D3-008 | L3_GROUNDING | Đạt | partial | ASK_MECHANISM | no | - |
| D3-009 | L2_SEMANTIC | Đạt | partial | ASK_CAUSE | no | - |
| D3-010 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-011 | L4_DIALOGUE | Đạt | mastered | COMPLETE_SESSION | yes | - |
| D3-012 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-013 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-014 | L3_GROUNDING | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-015 | L1_INPUT | Đạt | needs_recovery | SHOW_RECOVERY | no | - |
| D3-016 | L1_INPUT | Đạt | out_of_scope | OUT_OF_SCOPE | no | - |
| D3-017 | L1_INPUT | Đạt | needs_recovery | SHOW_RECOVERY | no | - |
| D3-018 | L1_INPUT | Đạt | copied_source | ASK_REPHRASE | no | - |
| D3-019 | L1_INPUT | Đạt | mastered | ASK_TRANSFER | no | - |
| D3-020 | L4_DIALOGUE | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-021 | L1_INPUT | Đạt | out_of_scope | OUT_OF_SCOPE | no | - |
| D3-022 | L1_INPUT | Đạt | out_of_scope | OUT_OF_SCOPE | no | - |
| D3-023 | L2_SEMANTIC | Đạt | partial | ASK_MECHANISM | no | - |
| D3-024 | L2_SEMANTIC | Đạt | misconception | SOCRATIC_CORRECTION | no | - |
| D3-025 | L3_GROUNDING | Đạt | partial | ASK_MECHANISM | no | - |
| D3-026 | L3_GROUNDING | Đạt | partial | ASK_MECHANISM | no | - |
| D3-027 | L4_DIALOGUE | Đạt | misconception | SHOW_RECOVERY | no | - |
| D3-028 | L4_DIALOGUE | Đạt | mastered | ASK_TRANSFER | no | - |

## Phân tích sai lệch

Không có sai lệch trên lượt chạy này.

## Quality bar đề xuất

- Ít nhất **24/28 ca đạt (85%)**.
- **0 false-mastered** trên các ca có misconception.
- **100% citation hợp lệ** và nằm trong kết quả retrieval của lượt đó.
- Không kết thúc phiên khi còn misconception chưa được xử lý.
