"""LangGraph state machine for the D3 Teach-back Agent's per-turn decision loop.

This module owns the control flow only. All domain logic (grounding checks,
regex signal detection, retrieval, safety checks, static fallbacks) stays in
``agent_core.py`` and is called from here through the ``TeachBackAgent``
instance's existing methods, so nothing is reimplemented.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agent_core import (
    ASSESSMENT_SCHEMA,
    POINT_PRIORITY,
    POINT_WEIGHTS,
    CODEBASE_DIR,
    SessionState,
    TeachBackAgent,
    detect_explicit_points,
    looks_insufficient,
    looks_out_of_scope,
    matches_any,
    normalize_text,
)

MAX_TURNS_PER_SESSION = 20
SESSION_LIMIT_MESSAGE = (
    "Mình đã hỏi khá nhiều lượt rồi; hãy đọc lại gợi ý này rồi quay lại dạy "
    "tiếp khi sẵn sàng nhé."
)


class TeachBackState(TypedDict, total=False):
    # input
    explanation: str
    session_id: str
    # SessionState mirror (in/out, persisted across turns)
    turn: int
    covered_points: list[str]
    unresolved_misconceptions: list[str]
    attempts_by_gap: dict[str, int]
    awaiting_transfer: bool
    transfer_passed: bool
    mastery_complete: bool
    # per-turn working values (explicitly reset every turn, never trusted stale)
    session_limit_hit: bool
    session_limit_reached: bool
    raw_assessment: dict[str, Any]
    assessment: dict[str, Any]
    explicit_points: list[str]
    covered: list[str]
    missing: list[str]
    unresolved: list[str]
    first_misconception: str | None
    status: str
    action: str
    target_gap: str | None
    retrieval: dict[str, Any] | None
    draft_question: str
    question_retry_count: int
    question_rejection_reason: str | None
    agent_response: str
    result: dict[str, Any]


def _session_state_from(state: TeachBackState) -> SessionState:
    return SessionState(
        session_id=state.get("session_id") or str(uuid.uuid4()),
        turn=int(state.get("turn", 0)),
        covered_points=list(state.get("covered_points", [])),
        unresolved_misconceptions=list(state.get("unresolved_misconceptions", [])),
        attempts_by_gap=dict(state.get("attempts_by_gap", {})),
        awaiting_transfer=bool(state.get("awaiting_transfer", False)),
        transfer_passed=bool(state.get("transfer_passed", False)),
        mastery_complete=bool(state.get("mastery_complete", False)),
    )


def build_graph(agent: TeachBackAgent) -> StateGraph:
    """Build the (uncompiled) per-turn decision graph bound to one agent."""

    def enforce_turn_limit(state: TeachBackState) -> dict[str, Any]:
        turn = int(state.get("turn", 0)) + 1
        return {
            "turn": turn,
            "session_limit_hit": turn > MAX_TURNS_PER_SESSION,
            # ephemeral question-drafting fields must never leak across turns
            "question_retry_count": 0,
            "question_rejection_reason": None,
        }

    def graceful_session_limit_exit(state: TeachBackState) -> dict[str, Any]:
        session_state = _session_state_from(state)
        result = {
            "session_id": session_state.session_id,
            "turn": session_state.turn,
            "provider": agent.provider.name,
            "status": "needs_recovery",
            "mastery_complete": False,
            "progress": sum(POINT_WEIGHTS[p] for p in session_state.covered_points),
            "covered_points": session_state.covered_points,
            "missing_points": [p for p in POINT_PRIORITY if p not in session_state.covered_points],
            "misconceptions": session_state.unresolved_misconceptions,
            "next_action": "SHOW_RECOVERY",
            "target_gap": None,
            "agent_response": SESSION_LIMIT_MESSAGE,
            "evidence_source_ids": [],
            "citations_valid": True,
            "source_cards": [],
            "recovery_card": None,
            "confidence": 0.0,
            "state": asdict(session_state),
            "tool_trace": [],
            "raw_assessment": {},
            "session_limit_reached": True,
        }
        agent.logger.write(
            "agent_decision",
            {
                "session_id": session_state.session_id,
                "turn": session_state.turn,
                "provider": agent.provider.name,
                "student_explanation": state.get("explanation", ""),
                "decision": result,
            },
        )
        return {"result": result}

    def assess(state: TeachBackState) -> dict[str, Any]:
        explanation = state["explanation"]
        session_state = _session_state_from(state)
        prompt = agent._build_prompt(explanation, session_state)
        raw = agent.provider.assess(
            prompt=prompt,
            explanation=explanation,
            schema=ASSESSMENT_SCHEMA,
            metadata={
                "session_id": session_state.session_id,
                "turn": session_state.turn,
                "awaiting_transfer": session_state.awaiting_transfer,
            },
        )
        return {"raw_assessment": raw}

    def validate_and_ground(state: TeachBackState) -> dict[str, Any]:
        explanation = state["explanation"]
        assessment = agent._validate_assessment(state["raw_assessment"], explanation)

        explicit_points = detect_explicit_points(explanation)
        assessment["harness_supported_points"] = sorted(explicit_points)
        deterministic_insufficient = looks_insufficient(explanation)
        deterministic_out_of_scope = looks_out_of_scope(explanation)
        if deterministic_out_of_scope:
            assessment["out_of_scope"] = True
            assessment["insufficient_input"] = False
        elif deterministic_insufficient:
            assessment["out_of_scope"] = False
            assessment["insufficient_input"] = True
        assessment["copied_source"] = bool(
            assessment["copied_source"] or agent.knowledge.is_probable_copy(explanation)
        )

        normalized_explanation = normalize_text(explanation)
        has_example_marker = matches_any(
            normalized_explanation,
            (r"\bvi du\b", r"\bchang han\b", r"\bgiong nhu\b"),
        )
        # K1-K4 đã được xác nhận đủ ở các lượt trước khi tới đây (đó là lý do
        # trạng thái là "mastered"/awaiting_transfer) — không bắt câu ví dụ
        # chuyển giao phải khớp lại đúng khuôn regex K2 một lần nữa, chỉ cần
        # còn tín hiệu liên quan (K2/K3/K4) và có nêu ví dụ, không lặp hiểu sai.
        if (
            bool(state.get("awaiting_transfer"))
            and has_example_marker
            and bool(explicit_points & {"K2", "K3", "K4"})
            and not assessment["misconceptions"]
        ):
            assessment["transfer_passed"] = True

        return {
            "assessment": assessment,
            "explicit_points": sorted(explicit_points),
        }

    def merge_coverage_and_regression(state: TeachBackState) -> dict[str, Any]:
        assessment = state["assessment"]
        explicit_points = set(state.get("explicit_points", []))
        covered_points = list(state.get("covered_points", []))
        unresolved_misconceptions = list(state.get("unresolved_misconceptions", []))

        verdict_by_point = {
            row["point_id"]: row["verdict"] for row in assessment["point_assessments"]
        }
        current_supported = {
            point_id for point_id, verdict in verdict_by_point.items() if verdict == "supported"
        } | explicit_points
        if assessment.get("copied_source"):
            # Một câu chép gần nguyên văn nguồn không phải bằng chứng đã hiểu —
            # đúng not_mastered_when trong knowledge JSON. Không cộng điểm mới
            # từ lượt này, chỉ giữ nguyên các điểm đã xác nhận từ trước.
            current_supported = set()
        covered = set(covered_points) | current_supported

        unresolved = set(unresolved_misconceptions)
        for previous_misconception in list(unresolved):
            conflicts = set(
                agent.knowledge.misconceptions[previous_misconception].get("conflicts_with", [])
            )
            if conflicts and conflicts <= current_supported:
                unresolved.remove(previous_misconception)
        unresolved.update(assessment["misconceptions"])
        for misconception in unresolved:
            for conflict in agent.knowledge.misconceptions[misconception].get("conflicts_with", []):
                covered.discard(conflict)

        required = agent.knowledge.required_point_ids
        missing = [point_id for point_id in required if point_id not in covered]
        first_misconception = next(iter(sorted(unresolved)), None)

        update: dict[str, Any] = {
            "covered": [p for p in POINT_PRIORITY if p in covered],
            "missing": missing,
            "unresolved": sorted(unresolved),
            "first_misconception": first_misconception,
            # cleared every turn; only retrieve_evidence (when it runs) sets it
            "retrieval": None,
        }
        if missing or unresolved:
            update["mastery_complete"] = False
            update["transfer_passed"] = False
            update["awaiting_transfer"] = False
        return update

    def decide_route(state: TeachBackState) -> dict[str, Any]:
        assessment = state["assessment"]
        unresolved = state.get("unresolved", [])
        missing = state.get("missing", [])
        first_misconception = state.get("first_misconception")
        awaiting_transfer = bool(state.get("awaiting_transfer", False))
        attempts_by_gap = dict(state.get("attempts_by_gap", {}))

        update: dict[str, Any] = {}
        if assessment["out_of_scope"]:
            status, action, target_gap = "out_of_scope", "OUT_OF_SCOPE", None
        elif assessment["copied_source"]:
            status = "copied_source"
            action = "ASK_REPHRASE"
            target_gap = assessment["recommended_gap"] or "K2"
        elif unresolved:
            status = "misconception"
            action = "SOCRATIC_CORRECTION"
            target_gap = agent.knowledge.misconception_gap(first_misconception or "M1")
        elif assessment["insufficient_input"]:
            status = "needs_recovery"
            action = "SHOW_RECOVERY"
            target_gap = assessment["recommended_gap"] or (missing[0] if missing else "K1")
        elif not missing:
            status = "mastered"
            target_gap = "K2"
            if awaiting_transfer and assessment["transfer_passed"]:
                action = "COMPLETE_SESSION"
                update["transfer_passed"] = True
                update["mastery_complete"] = True
                update["awaiting_transfer"] = False
            else:
                action = "ASK_TRANSFER"
                update["awaiting_transfer"] = True
        else:
            status = "partial"
            proposed_gap = assessment["recommended_gap"]
            target_gap = proposed_gap if proposed_gap in missing else missing[0]
            attempts = attempts_by_gap.get(target_gap, 0) + 1
            attempts_by_gap[target_gap] = attempts
            update["attempts_by_gap"] = attempts_by_gap
            if attempts >= 2:
                action = "SHOW_RECOVERY"
            elif target_gap == "K1":
                action = "ASK_MECHANISM"
            elif target_gap in {"K2", "K3"}:
                action = "ASK_CAUSE"
            else:
                action = "ASK_MITIGATION"

        update.update({"status": status, "action": action, "target_gap": target_gap})
        return update

    def retrieve_evidence(state: TeachBackState) -> dict[str, Any]:
        retrieval = agent.knowledge.retrieve_evidence(state["target_gap"])
        return {"retrieval": retrieval}

    def apply_static_fallback(state: TeachBackState) -> dict[str, Any]:
        fallback = agent._fallback_question(
            state["action"], state.get("target_gap"), state.get("first_misconception")
        )
        return {"agent_response": fallback}

    def draft_question(state: TeachBackState) -> dict[str, Any]:
        session_state = _session_state_from(state)
        target_gap = state.get("target_gap")
        misconception = state.get("first_misconception")
        rubric_excerpt: dict[str, Any] = {
            "task": agent.knowledge.data["concept"]["student_task"],
        }
        if target_gap and target_gap in agent.knowledge.points:
            point = agent.knowledge.points[target_gap]
            rubric_excerpt["ground_truth"] = point["ground_truth"]
            rubric_excerpt["accepted_signals"] = point["accepted_signals"]
        if misconception and misconception in agent.knowledge.misconceptions:
            rubric_excerpt["misconception_claim"] = agent.knowledge.misconceptions[
                misconception
            ]["claim"]
        payload = agent.provider.draft_question(
            target_gap=target_gap,
            action=state["action"],
            misconception=misconception,
            rubric_excerpt=rubric_excerpt,
            feedback=state.get("question_rejection_reason"),
            metadata={"session_id": session_state.session_id, "turn": session_state.turn},
        )
        return {"draft_question": str(payload.get("draft_question", "")).strip()}

    def validate_question(state: TeachBackState) -> dict[str, Any]:
        drafted = state.get("draft_question", "")
        if agent._question_is_safe(drafted):
            return {"agent_response": drafted}
        retry_count = int(state.get("question_retry_count", 0))
        if retry_count == 0:
            return {
                "question_rejection_reason": agent._question_rejection_reason(drafted),
                "question_retry_count": retry_count + 1,
            }
        fallback = agent._fallback_question(
            state["action"], state.get("target_gap"), state.get("first_misconception")
        )
        return {"agent_response": fallback}

    def finalize_and_persist(state: TeachBackState) -> dict[str, Any]:
        retrieval = state.get("retrieval")
        action = state["action"]
        target_gap = state.get("target_gap")
        assessment = state["assessment"]

        session_state = SessionState(
            session_id=state.get("session_id") or str(uuid.uuid4()),
            turn=int(state.get("turn", 0)),
            covered_points=list(state.get("covered", [])),
            unresolved_misconceptions=sorted(state.get("unresolved", [])),
            attempts_by_gap=dict(state.get("attempts_by_gap", {})),
            awaiting_transfer=bool(state.get("awaiting_transfer", False)),
            transfer_passed=bool(state.get("transfer_passed", False)),
            mastery_complete=bool(state.get("mastery_complete", False)),
        )

        recovery_card = None
        if action == "SHOW_RECOVERY" and retrieval:
            recovery_card = retrieval.get("recovery_card")

        evidence_source_ids = (
            [row["id"] for row in retrieval["sources"][:2]] if retrieval else []
        )
        retrieved_source_ids = (
            {row["id"] for row in retrieval["sources"]} if retrieval else set()
        )
        citations_valid = set(evidence_source_ids) <= retrieved_source_ids

        missing = state.get("missing", [])
        unresolved = state.get("unresolved", [])
        base_progress = sum(POINT_WEIGHTS[point] for point in session_state.covered_points)
        if session_state.mastery_complete:
            progress = 100
        elif not missing and not unresolved:
            progress = 90
        else:
            progress = min(base_progress, 70 if unresolved else 85)

        tool_trace: list[dict[str, Any]] = []
        if retrieval:
            tool_trace.append(
                {
                    "tool": "retrieve_evidence",
                    "input": {"knowledge_point_id": target_gap},
                    "output_source_ids": [row["id"] for row in retrieval["sources"]],
                }
            )
        tool_trace.append(
            {
                "tool": "save_learner_state",
                "input": {"session_id": session_state.session_id},
                "output": asdict(session_state),
            }
        )

        result = {
            "session_id": session_state.session_id,
            "turn": session_state.turn,
            "provider": agent.provider.name,
            "status": state["status"],
            "mastery_complete": session_state.mastery_complete,
            "progress": progress,
            "covered_points": session_state.covered_points,
            "missing_points": missing,
            "misconceptions": session_state.unresolved_misconceptions,
            "next_action": action,
            "target_gap": target_gap,
            "agent_response": state["agent_response"],
            "evidence_source_ids": evidence_source_ids,
            "citations_valid": citations_valid,
            "source_cards": retrieval["sources"] if retrieval else [],
            "recovery_card": recovery_card,
            "confidence": assessment["confidence"],
            "state": asdict(session_state),
            "tool_trace": tool_trace,
            "raw_assessment": assessment,
        }
        agent.logger.write(
            "agent_decision",
            {
                "session_id": session_state.session_id,
                "turn": session_state.turn,
                "provider": agent.provider.name,
                "student_explanation": state.get("explanation", ""),
                "decision": result,
            },
        )
        # Write the durable SessionState fields back to top-level state keys
        # too (not just inside "result"/"state"), since these are what the
        # SQLite checkpointer persists for the *next* turn on this thread to
        # read back via state.get(...). Without this, a checkpointed session
        # would never accumulate coverage/misconceptions/attempts across
        # turns even though the uncheckpointed TeachBackAgent.run_turn path
        # (which always resupplies the full previous_state itself) would
        # appear to work fine.
        return {
            "result": result,
            "session_id": session_state.session_id,
            "turn": session_state.turn,
            "covered_points": session_state.covered_points,
            "unresolved_misconceptions": session_state.unresolved_misconceptions,
            "attempts_by_gap": session_state.attempts_by_gap,
            "awaiting_transfer": session_state.awaiting_transfer,
            "transfer_passed": session_state.transfer_passed,
            "mastery_complete": session_state.mastery_complete,
        }

    graph = StateGraph(TeachBackState)
    graph.add_node("enforce_turn_limit", enforce_turn_limit)
    graph.add_node("graceful_session_limit_exit", graceful_session_limit_exit)
    graph.add_node("assess", assess)
    graph.add_node("validate_and_ground", validate_and_ground)
    graph.add_node("merge_coverage_and_regression", merge_coverage_and_regression)
    graph.add_node("decide_route", decide_route)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("apply_static_fallback", apply_static_fallback)
    graph.add_node("draft_question", draft_question)
    graph.add_node("validate_question", validate_question)
    graph.add_node("finalize_and_persist", finalize_and_persist)

    graph.add_edge(START, "enforce_turn_limit")
    graph.add_conditional_edges(
        "enforce_turn_limit",
        lambda s: "graceful_session_limit_exit" if s.get("session_limit_hit") else "assess",
        {"graceful_session_limit_exit": "graceful_session_limit_exit", "assess": "assess"},
    )
    graph.add_edge("graceful_session_limit_exit", END)
    graph.add_edge("assess", "validate_and_ground")
    graph.add_edge("validate_and_ground", "merge_coverage_and_regression")
    graph.add_edge("merge_coverage_and_regression", "decide_route")

    def route_after_decide(state: TeachBackState) -> str:
        # OUT_OF_SCOPE luôn dùng câu trả lời cố định để nhắc lại đúng chủ đề —
        # không giao cho AI tự soạn, vì trước đó nó không được cấp chỉ dẫn nào
        # về việc phải nói rõ input đang lạc đề (khác với SHOW_RECOVERY/
        # COMPLETE_SESSION, vốn đã đi qua đường này từ trước).
        if state.get("action") == "OUT_OF_SCOPE":
            return "apply_static_fallback"
        return "retrieve_evidence" if state.get("target_gap") else "draft_question"

    graph.add_conditional_edges(
        "decide_route",
        route_after_decide,
        {
            "retrieve_evidence": "retrieve_evidence",
            "draft_question": "draft_question",
            "apply_static_fallback": "apply_static_fallback",
        },
    )
    graph.add_conditional_edges(
        "retrieve_evidence",
        lambda s: (
            "apply_static_fallback"
            if s.get("action") in {"SHOW_RECOVERY", "COMPLETE_SESSION"}
            else "draft_question"
        ),
        {"apply_static_fallback": "apply_static_fallback", "draft_question": "draft_question"},
    )
    graph.add_edge("apply_static_fallback", "finalize_and_persist")
    graph.add_edge("draft_question", "validate_question")
    graph.add_conditional_edges(
        "validate_question",
        lambda s: "finalize_and_persist" if s.get("agent_response") else "draft_question",
        {"finalize_and_persist": "finalize_and_persist", "draft_question": "draft_question"},
    )
    graph.add_edge("finalize_and_persist", END)
    return graph


class GraphSessionRunner:
    """SQLite-checkpointed per-session runner used by the HTTP server.

    Persists session state across process restarts, keyed by ``session_id``
    as the LangGraph thread id.
    """

    def __init__(
        self,
        agent: TeachBackAgent,
        db_path: Path = CODEBASE_DIR / "state" / "sessions.sqlite3",
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._checkpointer = SqliteSaver(self._conn)
        self._checkpointer.setup()
        self._graph = build_graph(agent).compile(checkpointer=self._checkpointer)
        self._lock = threading.Lock()

    def run_turn(self, explanation: str, session_id: str, reset: bool = False) -> dict[str, Any]:
        config = {"configurable": {"thread_id": session_id}}
        with self._lock:
            if reset:
                self._checkpointer.delete_thread(session_id)
            output = self._graph.invoke(
                {"explanation": explanation.strip(), "session_id": session_id},
                config=config,
            )
        return output["result"]

    def get_state(self, session_id: str) -> dict[str, Any] | None:
        """Read back the last persisted turn result without calling the model.

        Returns the same ``result`` shape ``run_turn`` returns, taken from the
        checkpointed state, or ``None`` if this thread has no history yet.
        """
        config = {"configurable": {"thread_id": session_id}}
        with self._lock:
            snapshot = self._graph.get_state(config)
        if not snapshot or not snapshot.values:
            return None
        return snapshot.values.get("result")
