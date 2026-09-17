"""Run eval/golden_set_v1.json (4-layer product-risk taxonomy) against the live agent.

Unlike run_eval.py (built for eval/golden_set.json's strict K1-K4 response_contract
schema), this dataset mixes machine-checkable fields (status/action/misconceptions/
covered/missing, evidence_source_ids) with free-text `hard_constraints` that need a
human (or an LLM judge) to read the actual agent_response and confirm. This script
auto-scores what it can and prints/writes the rest as a manual review checklist —
it does not force golden_set_v1.json into run_eval.py's schema.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_core import AuditLogger, KnowledgeBase, TeachBackAgent, provider_from_name

CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent
EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_GOLDEN = EVAL_DIR / "golden_set_v1.json"
DEFAULT_RESULTS_JSONL = EVAL_DIR / "results_v1.jsonl"
DEFAULT_REPORT = EVAL_DIR / "run_results_v1.md"
DEFAULT_RUNS_DIR = EVAL_DIR / "runs"


def auto_score(case: dict[str, Any], actual: dict[str, Any], known_source_ids: set[str]) -> dict[str, bool]:
    """Score only the fields that can be compared mechanically. A missing key means
    'not applicable for this case' (e.g. meta/authority cases with no K1-K4 status)."""
    checks: dict[str, bool] = {}

    if case.get("expected_status") is not None:
        allowed = case.get("acceptable_status") or [case["expected_status"]]
        checks["status"] = actual.get("status") in allowed

    if case.get("expected_action") is not None:
        allowed = case.get("acceptable_action") or [case["expected_action"]]
        checks["next_action"] = actual.get("next_action") in allowed

    if case.get("expected_covered") is not None:
        checks["covered_points"] = set(actual.get("covered_points", [])) == set(case["expected_covered"])

    if case.get("expected_missing") is not None:
        checks["missing_points"] = set(actual.get("missing_points", [])) == set(case["expected_missing"])

    if case.get("expected_misconceptions") is not None:
        actual_set = set(actual.get("misconceptions", []))
        allowed_sets = case.get("acceptable_misconceptions")
        if allowed_sets:
            checks["misconceptions"] = any(actual_set == set(option) for option in allowed_sets)
        else:
            checks["misconceptions"] = actual_set == set(case["expected_misconceptions"])

    evidence = set(actual.get("evidence_source_ids", []))
    checks["no_fabricated_evidence"] = evidence <= known_source_ids

    required = set(case.get("required_evidence_any", []))
    if required:
        checks["evidence_hit"] = bool(evidence & required)

    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Run golden_set_v1.json (risk taxonomy) against the agent")
    parser.add_argument("--provider", choices=("auto", "openai", "offline"), default="auto")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS_JSONL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    started_at = started.isoformat()
    dataset = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    # Never use AuditLogger's default path: that file (codebase/logs/model_calls.jsonl)
    # is shared with TeachBackAgent/server.py's own default logger, so writing there
    # would mix eval noise into the app's real audit log. Isolate per run instead,
    # matching how run_eval.py archives its own model-call log.
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}_{args.provider}_riskset"
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    logger = AuditLogger(args.archive_dir / f"{run_id}_model_calls.jsonl")
    provider = provider_from_name(args.provider, logger=logger)
    knowledge = KnowledgeBase()
    agent = TeachBackAgent(provider=provider, knowledge=knowledge, logger=logger)
    known_source_ids = set(knowledge.sources)

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases)}] {case['id']} ({case['risk_layer']}, {case['frequency']}/{case['provenance']})")
        actual = agent.run_turn(case["input"], case.get("previous_state"))
        checks = auto_score(case, actual, known_source_ids)
        auto_pass = all(checks.values()) if checks else None
        results.append(
            {
                "id": case["id"],
                "risk_layer": case["risk_layer"],
                "frequency": case["frequency"],
                "provenance": case["provenance"],
                "auto_checks": checks,
                "auto_pass": auto_pass,
                "hard_constraints": case.get("hard_constraints", []),
                "scoring_note": case.get("scoring_note", ""),
                "actual": actual,
            }
        )

    args.results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.results_jsonl.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    auto_checkable = [row for row in results if row["auto_pass"] is not None]
    auto_passed = sum(1 for row in auto_checkable if row["auto_pass"])

    lines = [
        "# D3 - golden_set_v1 (risk taxonomy) - ket qua chay",
        "",
        f"- Thoi diem UTC: `{started_at}`",
        f"- Provider: `{provider.name}`",
        f"- Tong so ca: **{len(results)}**",
        f"- Ca co the tu dong cham mot phan: **{len(auto_checkable)}**",
        f"- Dat phan tu dong (trong so co the cham): **{auto_passed}/{len(auto_checkable)}**",
        "",
        "> File nay khong the cham day du tu dong: moi case con `hard_constraints` dang van ban, "
        "can nguoi doc (hoac LLM-judge) doi chieu voi `actual.agent_response` o phan chi tiet ben duoi. "
        "`auto_pass=null` nghia la case khong co status/action ky vong (case meta ve tham quyen), "
        "chi cham duoc bang hard_constraints thu cong.",
        "",
        "## Ket qua tu dong theo lop",
        "",
        "| Lop | Dat (tu dong) | Tong (co the cham) |",
        "|---|---:|---:|",
    ]
    layers = sorted({row["risk_layer"] for row in results})
    for layer in layers:
        layer_rows = [row for row in auto_checkable if row["risk_layer"] == layer]
        if not layer_rows:
            lines.append(f"| {layer} | - | 0 (chi cham tay) |")
            continue
        layer_passed = sum(1 for row in layer_rows if row["auto_pass"])
        lines.append(f"| {layer} | {layer_passed} | {len(layer_rows)} |")

    lines.extend(["", "## Chi tiet tung ca (tu dong + checklist cham tay)", ""])
    for row in results:
        status_label = (
            "PASS (tu dong)" if row["auto_pass"] is True
            else "FAIL (tu dong)" if row["auto_pass"] is False
            else "khong co check tu dong"
        )
        lines.append(f"### {row['id']} — {row['risk_layer']} — {status_label}")
        lines.append("")
        if row["auto_checks"]:
            failed = [name for name, ok in row["auto_checks"].items() if not ok]
            lines.append(f"- Auto checks: {row['auto_checks']}" + (f" — SAI: {failed}" if failed else ""))
        lines.append(f"- agent_response: {row['actual'].get('agent_response', '')!r}")
        lines.append(f"- evidence_source_ids: {row['actual'].get('evidence_source_ids', [])}")
        lines.append("- Hard constraints can nguoi/LLM-judge xac nhan:")
        for constraint in row["hard_constraints"]:
            lines.append(f"  - [ ] {constraint}")
        lines.append("")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"Auto-checkable: {auto_passed}/{len(auto_checkable)} passed "
        f"({(auto_passed / len(auto_checkable) * 100) if auto_checkable else 0:.1f}%)"
    )
    print(f"Report (co checklist cham tay): {args.report}")
    print(f"Detailed results: {args.results_jsonl}")


if __name__ == "__main__":
    main()
