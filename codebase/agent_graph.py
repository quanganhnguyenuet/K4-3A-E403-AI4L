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
    CODEBASE_DIR,
    SessionState,
    TeachBackAgent,
    looks_insufficient,
    looks_out_of_scope,
    matches_any,
    normalize_text,
)
MAX_TURNS_PER_SESSION = 20


def recovery_action(attempt: int, *, misconception: bool = False) -> tuple[str, str]:
    """Map a failed attempt count to the next support level."""
    if attempt <= 1:
        return (
            "SOCRATIC_CORRECTION" if misconception else "SOCRATIC_QUESTION",
            "socratic_question",
        )
    if attempt == 2:
        return "NARROW_QUESTION", "narrowed_question"
    if attempt == 3:
        return "CONTROLLED_HINT", "controlled_hint"
    return "SHOW_RECOVERY", "knowledge_recovery"
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
    last_target_gap: str | None
    recovery_stage: str
    awaiting_transfer: bool
    transfer_passed: bool
    mastery_complete: bool
    last_agent_response: str
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
        last_target_gap=state.get("last_target_gap"),
        recovery_stage=str(state.get("recovery_stage", "none")),
        awaiting_transfer=bool(state.get("awaiting_transfer", False)),
        transfer_passed=bool(state.get("transfer_passed", False)),
        mastery_complete=bool(state.get("mastery_complete", False)),
        last_agent_response=str(state.get("last_agent_response", "")),
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
            "progress": sum(agent.knowledge.point_weights[p] for p in session_state.covered_points),
            "covered_points": session_state.covered_points,
            "missing_points": [
                p for p in agent.knowledge.point_ids if p not in session_state.covered_points
            ],
            "misconceptions": session_state.unresolved_misconceptions,
            "next_action": "SHOW_RECOVERY",
            "target_gap": None,
            "agent_response": SESSION_LIMIT_MESSAGE,
            "evidence_source_ids": [],
            "citations_valid": True,
            "source_cards": [],
            "recovery_card": None,
            "diagnosis": {
                "type": "session_limit",
                "reasons": ["Phiên đã vượt giới hạn số lượt an toàn."],
            },
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
            schema=agent.assessment_schema,
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

        intent = assessment.get("intent", "teachback_answer")

        explicit_points = agent._detect_points(explanation)
        assessment["harness_supported_points"] = sorted(explicit_points)
        # The deterministic keyword detector (explicit_points) is a fixed phrase list per
        # point, so it misses natural paraphrasing. The model's own point_assessments are a
        # second, independent on-topic signal that's just as trustworthy here: _validate_
        # assessment already downgraded any "supported"/"contradicted" verdict back to
        # "absent" unless its evidence is a real quote grounded in this explanation, so a
        # surviving non-absent verdict means the model engaged with a specific lesson point,
        # not just a generic "sounds relevant" guess.
        grounded_llm_points = {
            row["point_id"]
            for row in assessment["point_assessments"]
            if row["verdict"] in {"supported", "contradicted"}
        }
        # Each of these is the model's own, already-semantic read that the turn belongs to
        # THIS lesson, so a keyword-absence guess must not overrule them: a detected point,
        # a grounded misconception (you can't hold a lesson's misconception while talking
        # about something unrelated), or "the learner is stuck / said too little" — being
        # stuck on the lesson is not the same as being off it.
        model_says_thin = intent == "request_help" or bool(assessment.get("insufficient_input"))
        has_domain_signal = (
            bool(explicit_points)
            or bool(grounded_llm_points)
            or bool(assessment["misconceptions"])
            or model_says_thin
        )
        deterministic_insufficient = model_says_thin or looks_insufficient(explanation)
        deterministic_out_of_scope = intent in {
            "out_of_scope",
            "authority_attack",
            "change_topic",
        } or looks_out_of_scope(
            explanation,
            domain_patterns=agent.out_of_scope_patterns,
            has_domain_signal=has_domain_signal,
        )
        if deterministic_out_of_scope:
            assessment["out_of_scope"] = True
            assessment["insufficient_input"] = False
        elif deterministic_insufficient:
            assessment["out_of_scope"] = False
            assessment["insufficient_input"] = True
        elif matches_any(normalize_text(explanation), agent.out_of_scope_patterns):
            # A positive lesson-domain-vocabulary hit is strong evidence the
            # turn IS on-topic. The model can still over-flag out_of_scope on
            # a vague-but-on-topic opener (e.g. "tôi muốn ôn về LLM") that
            # explains nothing yet — that is a weak/incomplete answer, not an
            # unrelated one, so never trust the model's out_of_scope=true
            # here without a deterministic signal backing it.
            assessment["out_of_scope"] = False
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
        # A "contradicted" verdict survived grounding, so the learner really did say
        # something that runs against this point. The regex net (explicit_points) only sees
        # that a rubric keyword appears, not whether it was affirmed or dismissed — without
        # this guard "khỏi cần khảo sát người dùng" would earn the user-research point, and
        # a point already covered would stay covered while being contradicted out loud.
        contradicted_points = {
            point_id for point_id, verdict in verdict_by_point.items() if verdict == "contradicted"
        }
        # A turn whose content is a wrong claim is not a demonstration of mastery, so the
        # keyword net must not hand out points for rubric words that appear inside it.
        keyword_points = set() if assessment["misconceptions"] else explicit_points
        current_supported = ({
            point_id for point_id, verdict in verdict_by_point.items() if verdict == "supported"
        } | keyword_points) - contradicted_points
        if (
            assessment.get("intent") != "teachback_answer"
            or assessment.get("copied_source")
            or assessment.get("insufficient_input")
        ):
            # Một câu chép gần nguyên văn nguồn không phải bằng chứng đã hiểu —
            # và một input không đủ nội dung cũng không phải bằng chứng. Không
            # cộng điểm mới từ lượt này, chỉ giữ các điểm đã xác nhận từ trước.
            current_supported = set()
        covered = (set(covered_points) | current_supported) - contradicted_points

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
                if conflict not in current_supported:
                    covered.discard(conflict)

        required = agent.knowledge.required_point_ids
        missing = [point_id for point_id in required if point_id not in covered]
        first_misconception = next(iter(sorted(unresolved)), None)
        attempts_by_gap = dict(state.get("attempts_by_gap", {}))
        for point_id in current_supported:
            attempts_by_gap.pop(point_id, None)

        update: dict[str, Any] = {
            "covered": [p for p in agent.knowledge.point_ids if p in covered],
            "missing": missing,
            "unresolved": sorted(unresolved),
            "first_misconception": first_misconception,
            # cleared every turn; only retrieve_evidence (when it runs) sets it
            "retrieval": None,
            "attempts_by_gap": attempts_by_gap,
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
        intent = assessment.get("intent", "teachback_answer")
        recovery_stage = "none"
        if intent == "authority_attack":
            status, action, target_gap = "authority_attack", "BOUNDARY_RESPONSE", None
        elif intent in {"out_of_scope", "change_topic"} or assessment["out_of_scope"]:
            status, action, target_gap = "out_of_scope", "OUT_OF_SCOPE", None
        elif intent in {"clarification_question", "source_request", "social", "session_setup"}:
            status = "coaching"
            target_gap = assessment["recommended_gap"] or (missing[0] if missing else "K2")
            if intent == "source_request":
                action = "ANSWER_WITH_SOURCES"
            elif intent == "clarification_question":
                action = "ANSWER_CLARIFICATION"
            else:
                action = "START_COACHING"
        elif assessment["copied_source"]:
            status = "copied_source"
            action = "ASK_REPHRASE"
            target_gap = assessment["recommended_gap"] or "K2"
        elif unresolved:
            status = "misconception"
            target_gap = agent.knowledge.misconception_gap(first_misconception or "M1")
            attempts = attempts_by_gap.get(target_gap, 0) + 1
            attempts_by_gap[target_gap] = attempts
            update["attempts_by_gap"] = attempts_by_gap
            action, recovery_stage = recovery_action(attempts, misconception=True)
        elif assessment["insufficient_input"]:
            status = "needs_recovery"
            previous_gap = state.get("last_target_gap")
            target_gap = (
                previous_gap
                if previous_gap in missing
                else assessment["recommended_gap"] or (missing[0] if missing else "K1")
            )
            attempts = attempts_by_gap.get(target_gap, 0) + 1
            attempts_by_gap[target_gap] = attempts
            update["attempts_by_gap"] = attempts_by_gap
            action, recovery_stage = recovery_action(attempts)
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
            if attempts == 2:
                action, recovery_stage = "NARROW_QUESTION", "narrowed_question"
            elif attempts == 3:
                action, recovery_stage = "CONTROLLED_HINT", "controlled_hint"
            elif attempts >= 4:
                action, recovery_stage = "SHOW_RECOVERY", "knowledge_recovery"
            elif target_gap == "K1":
                action = "ASK_MECHANISM"
                recovery_stage = "socratic_question"
            elif target_gap in {"K2", "K3"}:
                action = "ASK_CAUSE"
                recovery_stage = "socratic_question"
            else:
                action = "ASK_MITIGATION"
                recovery_stage = "socratic_question"

        persisted_last_target = target_gap
        if intent not in {"teachback_answer", "request_help"}:
            persisted_last_target = state.get("last_target_gap")
            recovery_stage = str(state.get("recovery_stage", "none"))
        update.update(
            {
                "status": status,
                "action": action,
                "target_gap": target_gap,
                "last_target_gap": persisted_last_target,
                "recovery_stage": recovery_stage,
            }
        )
        return update

    def retrieve_evidence(state: TeachBackState) -> dict[str, Any]:
        retrieval = agent.knowledge.retrieve_evidence(state["target_gap"])
        return {"retrieval": retrieval}

    def draft_question(state: TeachBackState) -> dict[str, Any]:
        session_state = _session_state_from(state)
        target_gap = state.get("target_gap")
        misconception = state.get("first_misconception")
        rubric_excerpt: dict[str, Any] = {
            "task": agent.knowledge.data["concept"]["student_task"],
            # Give the wording layer enough context to react to the learner
            # instead of emitting a generic rubric-shaped question.
            "learner_message": state.get("explanation", "")[:500],
            "previous_agent_response": state.get("last_agent_response", "")[:800],
            "support_stage": state.get("recovery_stage", "none"),
        }
        if target_gap and target_gap in agent.knowledge.points:
            point = agent.knowledge.points[target_gap]
            rubric_excerpt["ground_truth"] = point["ground_truth"]
            rubric_excerpt["accepted_signals"] = point["accepted_signals"]
            rubric_excerpt["recovery_text"] = agent._point_recovery_text(target_gap)
            rubric_excerpt["example_starting_points"] = list(
                point.get("accepted_signals", [])[:3]
            )
        if misconception and misconception in agent.knowledge.misconceptions:
            rubric_excerpt["misconception_claim"] = agent.knowledge.misconceptions[
                misconception
            ]["claim"]
        retrieval = state.get("retrieval") or {}
        rubric_excerpt["source_ids"] = [
            row["id"] for row in retrieval.get("sources", [])[:2]
        ]
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
        if agent._response_is_safe(drafted, state.get("action", "")):
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
            last_target_gap=state.get("last_target_gap"),
            recovery_stage=str(state.get("recovery_stage", "none")),
            awaiting_transfer=bool(state.get("awaiting_transfer", False)),
            transfer_passed=bool(state.get("transfer_passed", False)),
            mastery_complete=bool(state.get("mastery_complete", False)),
            last_agent_response=state.get("agent_response", ""),
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
        supported_this_turn = sorted(
            set(state.get("explicit_points", []))
            | {
                row["point_id"]
                for row in assessment["point_assessments"]
                if row["verdict"] == "supported"
            }
        )
        grounded_claims = [
            {
                "point_id": point_id,
                "source_ids": agent.knowledge.points[point_id]["source_ids"][:2],
            }
            for point_id in supported_this_turn
            if point_id in session_state.covered_points
        ]

        if state.get("unresolved"):
            diagnosis_entries = []
            for misconception_id in state["unresolved"]:
                row = agent.knowledge.misconceptions[misconception_id]
                gap = agent.knowledge.misconception_gap(misconception_id)
                diagnosis_entries.append(
                    {
                        "id": misconception_id,
                        "claim": row["claim"],
                        "why_wrong": agent.knowledge.points[gap]["ground_truth"],
                        "conflicts_with": row.get("conflicts_with", []),
                    }
                )
            diagnosis = {"type": "misconception", "entries": diagnosis_entries}
        elif assessment.get("out_of_scope"):
            diagnosis = {
                "type": "out_of_scope",
                "reasons": ["Input không cung cấp nội dung thuộc rubric K1-K4 của bài học."],
            }
        elif assessment.get("copied_source"):
            diagnosis = {
                "type": "copied_source",
                "reasons": ["Nội dung quá gần nguồn nên chưa chứng minh được hiểu bằng lời riêng."],
            }
        elif assessment.get("insufficient_input"):
            diagnosis = {
                "type": "insufficient",
                "reasons": ["Input chưa đủ bằng chứng để xác nhận knowledge point."],
            }
        else:
            diagnosis = {
                "type": "supported",
                "supported_points": supported_this_turn,
            }

        missing = state.get("missing", [])
        unresolved = state.get("unresolved", [])
        base_progress = sum(
            agent.knowledge.point_weights[point] for point in session_state.covered_points
        )
        # Progress represents mastered knowledge weight only. Conversation
        # stages such as a transfer question must not artificially cap it.
        progress = 100 if session_state.mastery_complete else base_progress

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
            "grounded_claims": grounded_claims,
            "recovery_card": recovery_card,
            "diagnosis": diagnosis,
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
            "last_target_gap": session_state.last_target_gap,
            "recovery_stage": session_state.recovery_stage,
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
        # The harness owns the action, grounding and mastery decision; the
        # model owns wording for every normal OpenAI response. Static text is
        # now only the retry/failure path (and the offline provider path).
        return "retrieve_evidence" if state.get("target_gap") else "draft_question"

    graph.add_conditional_edges(
        "decide_route",
        route_after_decide,
        {
            "retrieve_evidence": "retrieve_evidence",
            "draft_question": "draft_question",
        },
    )
    graph.add_edge("retrieve_evidence", "draft_question")
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
