"""Run eval/golden_set.json (4-layer product-risk taxonomy) against the live agent.

This dataset mixes machine-checkable fields (status/action/misconceptions/
covered/missing, evidence_source_ids) with free-text `hard_constraints` that need a
human (or an LLM judge) to read the actual agent_response and confirm. This script
auto-scores what it can and prints/writes the rest as a manual review checklist.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_core import AuditLogger, KnowledgeBase, TeachBackAgent, provider_from_name
from eval_runtime import ONLINE_EVAL_PROVIDER, require_openai_api_key
from platform_runtime import ConversationStore, LessonCatalog
from lesson_engine import TeachBackWebEngine

CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent
EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_GOLDEN = EVAL_DIR / "golden_set.json"
DEFAULT_RESULTS_JSONL = EVAL_DIR / "results_v1.jsonl"
DEFAULT_REPORT = EVAL_DIR / "run_results_v1.md"
DEFAULT_RUNS_DIR = EVAL_DIR / "runs"
RUN_HISTORY = EVAL_DIR / "run_history.jsonl"


def auto_score(case: dict[str, Any], actual: dict[str, Any], known_source_ids: set[str]) -> dict[str, bool]:
    """Score only the fields that can be compared mechanically. A missing key means
    'not applicable for this case' (e.g. meta/authority cases with no K1-K4 status)."""
    checks: dict[str, bool] = {}

    if case.get("expected_status") is not None:
        allowed = case.get("acceptable_status") or [case["expected_status"]]
        checks["status"] = actual.get("status") in allowed

    if case.get("expected_action") is not None or case.get("acceptable_action"):
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
    parser = argparse.ArgumentParser(description="Run golden_set.json (risk taxonomy) against the agent")
    parser.add_argument(
        "--provider",
        choices=(ONLINE_EVAL_PROVIDER,),
        default=ONLINE_EVAL_PROVIDER,
        help="Golden-set evaluation is online-only; the sole supported provider is OpenAI.",
    )
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--results-jsonl", type=Path, default=DEFAULT_RESULTS_JSONL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument(
        "--pipeline",
        choices=("web", "core"),
        default="web",
        help="web runs the same TeachBackWebEngine path served by server.py",
    )
    args = parser.parse_args()

    try:
        require_openai_api_key()
    except RuntimeError as exc:
        parser.error(str(exc))

    started = datetime.now(timezone.utc)
    started_at = started.isoformat()
    dataset = json.loads(args.golden.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    # Never use AuditLogger's default path: that file (codebase/logs/model_calls.jsonl)
    # is shared with TeachBackAgent/server.py's own default logger, so writing there
    # would mix eval noise into the app's real audit log. Isolate per run instead.
    resolved_provider = ONLINE_EVAL_PROVIDER
    dataset_version = str(dataset.get("version") or args.golden.stem)
    safe_version = dataset_version.lower().replace(" ", "_").replace(".", "_")
    run_id = (
        f"{started.strftime('%Y%m%dT%H%M%S%fZ')}_{resolved_provider}_"
        f"{args.pipeline}_{safe_version}"
    )
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    model_log_path = args.archive_dir / f"{run_id}_model_calls.jsonl"
    logger = AuditLogger(model_log_path)
    provider = provider_from_name(resolved_provider, logger=logger)
    knowledge = KnowledgeBase()
    agent = (
        TeachBackAgent(provider=provider, knowledge=knowledge, logger=logger)
        if args.pipeline == "core"
        else None
    )
    web_temp = tempfile.TemporaryDirectory(prefix="teachback-risk-web-")
    platform = (
        TeachBackWebEngine(
            catalog=LessonCatalog(),
            store=ConversationStore(Path(web_temp.name) / "learning.sqlite3"),
            model_log_path=model_log_path,
        )
        if args.pipeline == "web"
        else None
    )
    known_source_ids = set(knowledge.sources)

    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index:02d}/{len(cases)}] {case['id']} ({case['risk_layer']}, {case['frequency']}/{case['provenance']})")
        if agent is not None:
            actual = agent.run_turn(case["input"], case.get("previous_state"))
        else:
            assert platform is not None
            session = platform.create_session("why-llm-hallucinates")
            previous = case.get("previous_state") or {}
            covered = list(previous.get("covered_points", []))
            progress = min(100, len(covered) * 25)
            platform.store.update_session(
                session["id"],
                status="coaching" if covered else "new",
                progress=progress,
                covered_points=covered,
                misconceptions=list(previous.get("unresolved_misconceptions", [])),
                provider=resolved_provider,
                model=os.getenv("OPENAI_MODEL"),
                attempts_by_gap=dict(previous.get("attempts_by_gap", {})),
                last_target_gap=previous.get("last_target_gap"),
                recovery_stage=previous.get("recovery_stage", "none"),
                turn=int(previous.get("turn", 0)),
                awaiting_transfer=bool(previous.get("awaiting_transfer", False)),
                transfer_passed=bool(previous.get("transfer_passed", False)),
                mastery_complete=bool(previous.get("mastery_complete", False)),
            )
            actual = platform.send_message(
                session["id"],
                case["input"],
                provider=resolved_provider,
                model=os.getenv("OPENAI_MODEL"),
                allow_reroute=False,
            )
            actual["evidence_source_ids"] = [
                source["id"] for source in actual.get("source_cards", [])
            ]
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

    results_text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results)

    auto_checkable = [row for row in results if row["auto_pass"] is not None]
    auto_passed = sum(1 for row in auto_checkable if row["auto_pass"])
    accuracy_by_taxonomy: dict[str, dict[str, Any]] = {}
    for layer in sorted({row["risk_layer"] for row in results}):
        layer_rows = [row for row in auto_checkable if row["risk_layer"] == layer]
        layer_passed = sum(1 for row in layer_rows if row["auto_pass"])
        accuracy_by_taxonomy[layer] = {
            "passed": layer_passed,
            "total": len(layer_rows),
            "accuracy": round(layer_passed / len(layer_rows), 4) if layer_rows else None,
        }
    failed_check_counts = Counter(
        name
        for row in results
        for name, passed in row["auto_checks"].items()
        if not passed
    )

    lines = [
        "# D3 — golden_set (risk taxonomy) — kết quả chạy",
        "",
        f"- Thời điểm UTC: `{started_at}`",
        f"- Provider: `{resolved_provider}`",
        f"- Pipeline: `{args.pipeline}`",
        f"- Dataset/version: `{args.golden.name}` / `{dataset_version}`",
        f"- Tổng số ca: **{len(results)}**",
        f"- Ca có thể tự động chấm một phần: **{len(auto_checkable)}**",
        f"- Đạt phần tự động: **{auto_passed}/{len(auto_checkable)}**",
        "",
        "> `auto_pass` chỉ phản ánh các field máy chấm được. Mỗi case vẫn còn "
        "`hard_constraints` cần hai người đọc độc lập và đối chiếu với `actual.agent_response`; "
        "vì vậy 100% auto-check không đồng nghĩa đã đạt Quality Bar chính thức.",
        "",
        "> Quy ước evidence: `grounded_claims` chứng minh ý learner đã nói; "
        "`evidence_source_ids`/`source_cards` là nguồn cho phản hồi hoặc knowledge gap tiếp theo.",
        "",
        "## Kết quả tự động theo lớp",
        "",
        "| Lớp | Đạt (tự động) | Tổng (có thể chấm) |",
        "|---|---:|---:|",
    ]
    layers = sorted({row["risk_layer"] for row in results})
    for layer in layers:
        layer_rows = [row for row in auto_checkable if row["risk_layer"] == layer]
        if not layer_rows:
            lines.append(f"| {layer} | - | 0 (chỉ chấm tay) |")
            continue
        layer_passed = sum(1 for row in layer_rows if row["auto_pass"])
        lines.append(f"| {layer} | {layer_passed} | {len(layer_rows)} |")

    lines.extend(["", "## Chi tiết từng ca (tự động + checklist chấm tay)", ""])
    for row in results:
        status_label = (
            "PASS (tự động)" if row["auto_pass"] is True
            else "FAIL (tự động)" if row["auto_pass"] is False
            else "không có check tự động"
        )
        lines.append(f"### {row['id']} — {row['risk_layer']} — {status_label}")
        lines.append("")
        if row["auto_checks"]:
            failed = [name for name, ok in row["auto_checks"].items() if not ok]
            lines.append(f"- Auto checks: {row['auto_checks']}" + (f" — SAI: {failed}" if failed else ""))
        lines.append(f"- agent_response: {row['actual'].get('agent_response', '')!r}")
        lines.append(f"- evidence_source_ids: {row['actual'].get('evidence_source_ids', [])}")
        lines.append("- Hard constraints cần hai người xác nhận:")
        for constraint in row["hard_constraints"]:
            lines.append(f"  - [ ] {constraint}")
        lines.append("")

    report_text = "\n".join(lines)
    ended_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "provider": resolved_provider,
        "model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        "pipeline": args.pipeline,
        "dataset": str(args.golden),
        "dataset_version": dataset_version,
        "total": len(results),
        "auto_checkable": len(auto_checkable),
        "auto_passed": auto_passed,
        "auto_pass_rate": round(auto_passed / len(auto_checkable), 4) if auto_checkable else None,
        "accuracy_by_taxonomy": accuracy_by_taxonomy,
        "failed_check_counts": dict(sorted(failed_check_counts.items())),
    }

    archive_results = args.archive_dir / f"{run_id}_results.jsonl"
    archive_report = args.archive_dir / f"{run_id}_report.md"
    archive_summary = args.archive_dir / f"{run_id}_summary.json"
    summary["artifacts"] = {
        "results_jsonl": str(archive_results.resolve()),
        "report": str(archive_report.resolve()),
        "summary": str(archive_summary.resolve()),
        "model_calls": str(model_log_path.resolve()),
        "run_history": str(RUN_HISTORY.resolve()),
    }
    args.results_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.results_jsonl.write_text(results_text, encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")
    archive_results.write_text(results_text, encoding="utf-8")
    archive_report.write_text(report_text, encoding="utf-8")
    archive_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    model_log_path.touch(exist_ok=True)
    with RUN_HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
    if platform is not None:
        platform.store.close()
    web_temp.cleanup()

    print(
        f"Auto-checkable: {auto_passed}/{len(auto_checkable)} passed "
        f"({(auto_passed / len(auto_checkable) * 100) if auto_checkable else 0:.1f}%)"
    )
    print(f"Report (co checklist cham tay): {args.report}")
    print(f"Detailed results: {args.results_jsonl}")
    print(f"Archived run: {archive_summary}")


if __name__ == "__main__":
    main()
