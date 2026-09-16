"""Run all 20 D3 golden cases and write auditable result artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_core import (
    AuditLogger,
    KnowledgeBase,
    TeachBackAgent,
    normalize_text,
    provider_from_name,
)


CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent
EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_GOLDEN = EVAL_DIR / "golden_set.json"
DEFAULT_RESULTS_JSONL = EVAL_DIR / "results.jsonl"
DEFAULT_REPORT = EVAL_DIR / "run_results.md"


def question_is_leak_free(response: str) -> bool:
    text = normalize_text(response)
    forbidden = (
        "dap an day du la",
        "bon y gom",
        "cau tra loi dung la",
    )
    return not any(marker in text for marker in forbidden)


def score_case(case: dict[str, Any], actual: dict[str, Any], knowledge: KnowledgeBase) -> dict[str, Any]:
    expected_covered = set(case["expected_covered"])
    expected_missing = set(case["expected_missing"])
    expected_misconceptions = set(case["expected_misconceptions"])
    required_evidence = set(case.get("required_evidence_any", []))
    actual_evidence = set(actual["evidence_source_ids"])
    expected_mastery_complete = bool(
        case.get("expected_mastery_complete", case["expected_action"] == "COMPLETE_SESSION")
    )

    checks = {
        "status": actual["status"] == case["expected_status"],
        "covered_points": set(actual["covered_points"]) == expected_covered,
        "missing_points": set(actual["missing_points"]) == expected_missing,
        "misconceptions": set(actual["misconceptions"]) == expected_misconceptions,
        "next_action": actual["next_action"] == case["expected_action"],
        "mastery_complete": actual["mastery_complete"] == expected_mastery_complete,
        "evidence_hit": (not required_evidence) or bool(required_evidence & actual_evidence),
        "citations_valid": bool(actual["citations_valid"])
        and actual_evidence <= set(knowledge.sources),
        "answer_leak_free": question_is_leak_free(actual["agent_response"]),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "id": case["id"],
        "taxonomy_layer": case["taxonomy_layer"],
        "passed": not failures,
        "failed_checks": failures,
        "checks": checks,
        "expected": {
            "status": case["expected_status"],
            "covered_points": case["expected_covered"],
            "missing_points": case["expected_missing"],
            "misconceptions": case["expected_misconceptions"],
            "next_action": case["expected_action"],
            "mastery_complete": expected_mastery_complete,
            "required_evidence_any": case.get("required_evidence_any", []),
        },
        "actual": {
            "status": actual["status"],
            "covered_points": actual["covered_points"],
            "missing_points": actual["missing_points"],
            "misconceptions": actual["misconceptions"],
            "next_action": actual["next_action"],
            "mastery_complete": actual["mastery_complete"],
            "evidence_source_ids": actual["evidence_source_ids"],
            "agent_response": actual["agent_response"],
            "progress": actual["progress"],
        },
    }


def write_report(
    path: Path,
    *,
    provider_name: str,
    results: list[dict[str, Any]],
    started_at: str,
    dataset_version: str,
) -> None:
    passed = sum(1 for row in results if row["passed"])
    failed = len(results) - passed
    pass_rate = (passed / len(results) * 100) if results else 0
    by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_layer[row["taxonomy_layer"]].append(row)
    failure_counts = Counter(
        check for row in results for check in row["failed_checks"]
    )

    lines = [
        "# D3 - Kết quả chạy golden set",
        "",
        f"- Thời điểm UTC: `{started_at}`",
        f"- Dataset version: `{dataset_version}`",
        f"- Provider: `{provider_name}`",
        f"- Tổng số ca: **{len(results)}**",
        f"- Đạt: **{passed}**",
        f"- Không đạt: **{failed}**",
        f"- Tỷ lệ đạt: **{pass_rate:.1f}%**",
        "",
    ]
    if provider_name == "offline_rule_baseline":
        lines.extend(
            [
                "> **Lưu ý:** Đây là lượt chạy baseline bằng luật cục bộ vì môi trường chưa có `OPENAI_API_KEY`. "
                "Kết quả này xác minh harness, retrieval, citation và runner; không được trình bày như kết quả model AI thật. "
                "Chạy lại với `--provider openai` trước video CP3.",
                "",
            ]
        )

    lines.extend(
        [
            "## Kết quả theo taxonomy",
            "",
            "| Lớp | Đạt | Tổng | Tỷ lệ |",
            "|---|---:|---:|---:|",
        ]
    )
    for layer in sorted(by_layer):
        layer_rows = by_layer[layer]
        layer_passed = sum(1 for row in layer_rows if row["passed"])
        lines.append(
            f"| {layer} | {layer_passed} | {len(layer_rows)} | "
            f"{layer_passed / len(layer_rows) * 100:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Chi tiết 20 ca",
            "",
            "| Case | Lớp | Kết quả | Status thực tế | Action thực tế | Complete | Kiểm tra sai |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in results:
        failures = ", ".join(row["failed_checks"]) or "-"
        lines.append(
            f"| {row['id']} | {row['taxonomy_layer']} | "
            f"{'Đạt' if row['passed'] else 'Không đạt'} | "
            f"{row['actual']['status']} | {row['actual']['next_action']} | "
            f"{'yes' if row['actual']['mastery_complete'] else 'no'} | {failures} |"
        )

    lines.extend(["", "## Phân tích sai lệch", ""])
    if not failure_counts:
        lines.append("Không có sai lệch trên lượt chạy này.")
    else:
        for check, count in failure_counts.most_common():
            explanation = {
                "status": "Phân loại trạng thái tổng chưa khớp nhãn.",
                "covered_points": "Nhận diện semantic coverage K1-K4 chưa chính xác.",
                "missing_points": "Knowledge gap suy ra chưa khớp ground truth.",
                "misconceptions": "Bỏ sót hoặc báo nhầm misconception; đây là lỗi rủi ro cao.",
                "next_action": "Policy chọn câu hỏi/hành động tiếp theo chưa phù hợp.",
                "mastery_complete": "Điều kiện dừng phiên học chưa được áp dụng đúng.",
                "evidence_hit": "Retriever chưa đưa ra một nguồn nằm trong nhóm nguồn mong đợi.",
                "citations_valid": "Citation không nằm trong source registry hoặc ngoài kết quả retrieval.",
                "answer_leak_free": "Câu hỏi có dấu hiệu tiết lộ trực tiếp đáp án.",
            }.get(check, "Kiểm tra không đạt.")
            lines.append(f"- **{check}: {count} ca.** {explanation}")

    failing_cases = [row for row in results if not row["passed"]]
    if failing_cases:
        lines.extend(["", "### Case cần xem lại", ""])
        for row in failing_cases:
            lines.append(
                f"- `{row['id']}`: expected status/action "
                f"`{row['expected']['status']}/{row['expected']['next_action']}`, "
                f"actual `{row['actual']['status']}/{row['actual']['next_action']}`; "
                f"sai ở {', '.join(row['failed_checks'])}."
            )

    lines.extend(
        [
            "",
            "## Quality bar đề xuất",
            "",
            "- Ít nhất **16/20 ca đạt (80%)**.",
            "- **0 false-mastered** trên các ca có misconception.",
            "- **100% citation hợp lệ** và nằm trong kết quả retrieval của lượt đó.",
            "- Không kết thúc phiên khi còn misconception chưa được xử lý.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_dataset(dataset: dict[str, Any]) -> None:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) != 20:
        raise ValueError(
            f"Golden set must have exactly 20 cases, got {len(cases) if isinstance(cases, list) else 0}"
        )
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Golden set case IDs must be unique")
    expected_layers = {"L1_INPUT", "L2_SEMANTIC", "L3_GROUNDING", "L4_DIALOGUE"}
    layer_counts = Counter(case.get("taxonomy_layer") for case in cases)
    if set(layer_counts) != expected_layers or any(
        layer_counts[layer] != 5 for layer in expected_layers
    ):
        raise ValueError(f"Expected 5 cases per taxonomy layer, got {dict(layer_counts)}")
    expected_points = {"K1", "K2", "K3", "K4"}
    for case in cases:
        covered = set(case.get("expected_covered", []))
        missing = set(case.get("expected_missing", []))
        if covered & missing or covered | missing != expected_points:
            raise ValueError(f"{case['id']} has an invalid covered/missing partition")
    if not any(case.get("previous_state") for case in cases):
        raise ValueError("Golden set must include at least one multi-turn case")
    if not any(case.get("expected_action") == "COMPLETE_SESSION" for case in cases):
        raise ValueError("Golden set must test the COMPLETE_SESSION stop condition")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the D3 golden set")
    parser.add_argument("--provider", choices=("auto", "openai", "offline"), default="auto")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS_JSONL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat()
    dataset = json.loads(args.golden.read_text(encoding="utf-8"))
    validate_dataset(dataset)
    cases = dataset["cases"]

    logger = AuditLogger()
    provider = provider_from_name(args.provider, logger=logger)
    knowledge = KnowledgeBase()
    agent = TeachBackAgent(provider=provider, knowledge=knowledge, logger=logger)
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases)}] {case['id']} ({case['taxonomy_layer']})")
        actual = agent.run_turn(case["input"], case.get("previous_state"))
        results.append(score_case(case, actual, knowledge))

    args.results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.results_jsonl.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_report(
        args.report,
        provider_name=provider.name,
        results=results,
        started_at=started_at,
        dataset_version=str(dataset.get("version", "unknown")),
    )

    passed = sum(1 for row in results if row["passed"])
    print(f"Completed: {passed}/{len(results)} passed ({passed / len(results) * 100:.1f}%)")
    print(f"Report: {args.report}")
    print(f"Detailed results: {args.results_jsonl}")


if __name__ == "__main__":
    main()
