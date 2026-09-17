"""Product-facing multi-lesson adapter for the teach-back agent.

Teaching and grading decisions come exclusively from ``agent_core`` and
``agent_graph``. This adapter connects that domain engine to the catalog and
durable conversation store used by the web application.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from agent_core import (
    AuditLogger,
    DOMAIN_PATTERNS,
    KnowledgeBase,
    OfflineRuleProvider,
    OpenAIResponsesProvider,
    TeachBackAgent,
    build_generic_misconception_detector,
    build_generic_point_detector,
    detect_explicit_misconceptions,
    detect_explicit_points,
    normalize_text,
)
from platform_runtime import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_LOG_PATH,
    TRANSCRIPT_DIR,
    ConversationStore,
    LessonCatalog,
    OpenAILessonRouter,
    contains_phrase,
)
from turn_policy import classify_turn_intent

D3_LESSON_ID = "why-llm-hallucinates"


def lesson_to_ground_truth(lesson: dict[str, Any]) -> dict[str, Any]:
    """Adapt one ``lesson_catalog.json`` lesson into the ground-truth JSON
    shape ``agent_core.KnowledgeBase`` expects."""
    knowledge_points = [
        {
            "id": point["id"],
            "label": point["label"],
            "required": True,
            "weight": int(point.get("weight", 0)) or None,
            "ground_truth": point["ground_truth"],
            "accepted_signals": point.get("signals", []),
            "source_ids": point.get("source_ids", []),
            "question": point.get("question", ""),
            "recovery": point.get("recovery", ""),
        }
        for point in lesson["points"]
    ]
    misconceptions = [
        {
            "id": row["id"],
            "claim": row["claim"],
            "explanation": row.get("explanation", ""),
            "socratic_question": row.get("socratic_question", ""),
            "conflicts_with": row.get("conflicts_with", []),
            "signals": row.get("signals", []),
        }
        for row in lesson.get("misconceptions", [])
    ]
    recovery_cards = [
        {
            "id": f"recovery-{point['id']}",
            "for": [point["id"]],
            "text": point.get("recovery", ""),
            "source_ids": point.get("source_ids", []),
        }
        for point in lesson["points"]
    ]
    return {
        "concept": {"student_task": lesson.get("task", "")},
        "knowledge_points": knowledge_points,
        "misconceptions": misconceptions,
        "sources": lesson.get("sources", []),
        "recovery_cards": recovery_cards,
    }


def build_domain_patterns(lesson: dict[str, Any]) -> tuple[str, ...]:
    """Word-boundary regex built from a lesson's own ``scope_terms``, used as
    the out-of-scope guard for lessons without a hand-written pattern table."""
    terms = [normalize_text(term) for term in lesson.get("scope_terms", []) if term.strip()]
    escaped = sorted({re.escape(term) for term in terms if term}, key=len, reverse=True)
    if not escaped:
        return ()
    return (r"\b(" + "|".join(escaped) + r")\b",)


class GenericOfflineProvider:
    """Signal-matching offline baseline for a lesson with no dedicated
    semantic regex table (every lesson except the original D3 one)."""

    name = "offline_rule_baseline"

    def __init__(self, knowledge: KnowledgeBase, logger: AuditLogger | None = None) -> None:
        self.knowledge = knowledge
        self.logger = logger or AuditLogger()
        self._detect_points = build_generic_point_detector(knowledge)
        self._detect_misconceptions = build_generic_misconception_detector(knowledge)

    def assess(
        self, *, prompt: str, explanation: str, schema: dict[str, Any], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        intent = classify_turn_intent(explanation)
        out_of_scope = intent in {"out_of_scope", "authority_attack", "change_topic"}
        insufficient = intent == "request_help" or len(explanation.split()) <= 4
        copied = self.knowledge.is_probable_copy(explanation)
        explicit_points = self._detect_points(explanation)
        misconceptions = sorted(self._detect_misconceptions(explanation))
        point_assessments = [
            {
                "point_id": point_id,
                "verdict": "supported" if point_id in explicit_points else "absent",
                "student_evidence": explanation[:240] if point_id in explicit_points else None,
            }
            for point_id in self.knowledge.point_ids
        ]
        absent = [row["point_id"] for row in point_assessments if row["verdict"] != "supported"]
        recommended_gap = absent[0] if absent else None
        if misconceptions:
            recommended_action = "SOCRATIC_CORRECTION"
        elif out_of_scope:
            recommended_action = "OUT_OF_SCOPE"
        elif copied:
            recommended_action = "ASK_REPHRASE"
        elif insufficient:
            recommended_action = "SHOW_RECOVERY"
        elif recommended_gap:
            recommended_action = "ASK_CAUSE"
        else:
            recommended_action = "ASK_TRANSFER"
        transfer_passed = bool(
            metadata.get("awaiting_transfer")
            and not insufficient
            and not out_of_scope
            and explicit_points
        )
        result = {
            "point_assessments": point_assessments,
            "misconception_assessments": [
                {"id": m, "student_evidence": explanation[:240], "confidence": 1.0}
                for m in misconceptions
            ],
            "copied_source": copied,
            "out_of_scope": out_of_scope,
            "insufficient_input": insufficient,
            "has_original_example": False,
            "transfer_passed": transfer_passed,
            "confidence": 0.55,
            "recommended_gap": recommended_gap,
            "recommended_action": recommended_action,
            "draft_question": "",
            "intent": intent,
        }
        self.logger.write(
            "model_raw_response",
            {
                "provider": self.name,
                "model": "deterministic-rules-v1",
                "raw_response": result,
                "warning": "Offline baseline; this is not an AI model response.",
            },
        )
        return result

    def draft_question(self, **_: Any) -> dict[str, Any]:
        return {"draft_question": ""}


class TeachBackWebEngine:
    """Web API facade backed by one ``TeachBackAgent`` per lesson."""

    ROUTING_STOP_WORDS = {
        "ban", "minh", "toi", "em", "anh", "chi", "cho", "voi", "mot",
        "nhung", "nhu", "the", "nao", "muon", "hoc", "on", "tap", "lai",
        "ve", "la", "gi", "duoc", "khong", "hay", "can", "phan",
    }

    def __init__(
        self,
        catalog: LessonCatalog | None = None,
        store: ConversationStore | None = None,
        model_log_path: Path | str = DEFAULT_MODEL_LOG_PATH,
    ) -> None:
        self.catalog = catalog or LessonCatalog()
        self.store = store or ConversationStore()
        self.model_log_path = Path(model_log_path)
        self._lesson_bundles: dict[str, dict[str, Any]] = {
            lesson["id"]: self._build_lesson_bundle(lesson) for lesson in self.catalog.all()
        }

    @staticmethod
    def _build_lesson_bundle(lesson: dict[str, Any]) -> dict[str, Any]:
        is_d3 = lesson["id"] == D3_LESSON_ID
        point_ids = tuple(point["id"] for point in lesson["points"])
        misconception_ids = tuple(row["id"] for row in lesson.get("misconceptions", []))
        knowledge = KnowledgeBase(
            data=lesson_to_ground_truth(lesson),
            point_ids=point_ids,
            misconception_ids=misconception_ids,
        )
        return {
            "knowledge": knowledge,
            "detect_points": detect_explicit_points if is_d3 else build_generic_point_detector(knowledge),
            "detect_misconceptions": (
                detect_explicit_misconceptions if is_d3 else build_generic_misconception_detector(knowledge)
            ),
            "out_of_scope_patterns": DOMAIN_PATTERNS if is_d3 else build_domain_patterns(lesson),
            "is_d3": is_d3,
        }

    def _agent_for(
        self,
        lesson_id: str,
        *,
        provider: str,
        model: str | None,
        api_key: str | None,
        logger: AuditLogger,
    ) -> TeachBackAgent:
        bundle = self._lesson_bundles[lesson_id]
        if provider == "openai":
            provider_impl = OpenAIResponsesProvider(
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                model=model or DEFAULT_MODEL,
                logger=logger,
            )
        elif bundle["is_d3"]:
            provider_impl = OfflineRuleProvider(logger=logger)
        else:
            provider_impl = GenericOfflineProvider(bundle["knowledge"], logger=logger)
        return TeachBackAgent(
            provider=provider_impl,
            knowledge=bundle["knowledge"],
            logger=logger,
            detect_points=bundle["detect_points"],
            detect_misconceptions=bundle["detect_misconceptions"],
            out_of_scope_patterns=bundle["out_of_scope_patterns"],
        )

    def create_session(
        self, lesson_id: str, session_id: str | None = None
    ) -> dict[str, Any]:
        lesson = self.catalog.get(lesson_id)
        session = self.store.create_session(lesson, session_id=session_id)
        return self._decorate_session(session, lesson)

    def start_chat(
        self,
        content: str,
        *,
        provider: str = "offline",
        model: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung tin nhắn không được để trống")
        if len(content) > 12_000:
            raise ValueError("Tin nhắn vượt quá 12.000 ký tự")
        provider = provider.strip().lower()
        if provider not in {"offline", "openai"}:
            raise ValueError("provider phải là offline hoặc openai")
        selected_model = (
            (model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
            if provider == "openai"
            else None
        )
        if provider == "openai":
            router = OpenAILessonRouter(
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                model=selected_model or DEFAULT_MODEL,
                log_path=self.model_log_path,
            )
            route = router.route_lesson(self.catalog.all(), content)
        else:
            route = self._offline_route_lesson(content)

        intent = str(route.get("intent") or classify_turn_intent(content))
        tool_trace = [{"tool": "route_lesson", "input": {"content": content}, "output": route}]
        if not route["matched"]:
            reply = str(route.get("reply", "")).strip()
            if not reply:
                if intent == "request_help":
                    reply = (
                        "Không sao. Bạn muốn ôn phần nào trong các bài hiện có—cách LLM hoạt động, "
                        "xác định bài toán AI, hay đo lường giá trị sản phẩm?"
                    )
                elif intent == "out_of_scope":
                    reply = (
                        "Nội dung này nằm ngoài các bài ôn hiện có. Bạn có thể chọn một chủ đề "
                        "trong danh sách bài học để mình cùng ôn tiếp nhé."
                    )
                else:
                    reply = (
                        "Bạn cho mình thêm một khái niệm hoặc ví dụ cụ thể về phần muốn ôn nhé; "
                        "mình sẽ chọn đúng bài cho bạn."
                    )
            return {
                "session_id": None,
                "lesson_id": None,
                "session": None,
                "needs_clarification": True,
                "status": "out_of_scope" if intent == "out_of_scope" else "needs_clarification",
                "intent": intent,
                "agent_response": reply,
                "provider": provider,
                "model": selected_model,
                "tool_trace": tool_trace,
            }

        lesson = self.catalog.get(route["lesson_id"])
        session = self.store.create_session(
            lesson, title=self._chat_title(content), include_greeting=False
        )
        try:
            result = self.send_message(
                session["id"], content, provider=provider, model=selected_model,
                api_key=api_key, allow_reroute=False,
            )
        except Exception:
            self.store.delete_session(session["id"])
            raise
        result["needs_clarification"] = False
        result["tool_trace"] = tool_trace + result.get("tool_trace", [])
        result["session"] = self.get_session(session["id"])
        return result

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session = self.store.get_session(session_id, include_messages=True)
        if session is None:
            return None
        return self._decorate_session(session, self.catalog.get(session["lesson_id"]))

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        sessions = self.store.list_sessions(limit)
        for session in sessions:
            session["lesson_title"] = self.catalog.get(session["lesson_id"])["title"]
        return sessions

    def clear_history(self) -> int:
        return self.store.clear_sessions()

    @staticmethod
    def _chat_title(content: str) -> str:
        compact = re.sub(r"\s+", " ", content).strip()
        return compact if len(compact) <= 58 else compact[:57].rstrip() + "…"

    def _meaningful_tokens(self, value: str) -> set[str]:
        return {
            token for token in re.findall(r"[a-z0-9]+", normalize_text(value))
            if len(token) >= 3 and token not in self.ROUTING_STOP_WORDS
        }

    def _offline_route_lesson(self, content: str) -> dict[str, Any]:
        text = normalize_text(content)
        prompt_tokens = self._meaningful_tokens(content)
        scored: list[tuple[int, str]] = []
        has_specific_lesson_signal = False
        for lesson in self.catalog.all():
            score = 0
            for term in lesson.get("scope_terms", []):
                normalized = normalize_text(term)
                if contains_phrase(text, normalized):
                    has_specific_lesson_signal = True
                    score += 2 + min(len(normalized.split()), 3)
            for point in lesson["points"]:
                if contains_phrase(text, normalize_text(point["label"])):
                    has_specific_lesson_signal = True
                    score += 4
                for signal in point.get("signals", []):
                    if contains_phrase(text, normalize_text(signal)):
                        has_specific_lesson_signal = True
                        score += 3
            corpus = " ".join(
                [lesson["title"], lesson["description"], lesson["task"]]
                + [point["label"] + " " + point["ground_truth"] for point in lesson["points"]]
            )
            score += min(len(prompt_tokens & self._meaningful_tokens(corpus)), 4)
            scored.append((score, lesson["id"]))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, lesson_id = scored[0]
        intent = classify_turn_intent(content)
        matched = best_score >= 2
        if intent == "request_help" and not has_specific_lesson_signal:
            matched = False
        return {
            # A single generic token overlap is not enough to create a session;
            # require either a specific scope/signal hit or two semantic clues.
            "matched": matched,
            "lesson_id": lesson_id,
            "confidence": min(0.95, 0.35 + best_score * 0.05) if best_score else 0.0,
            "reason": "keyword_and_rubric_match" if best_score else "no_lesson_signal",
            "intent": intent,
            "reply": "",
        }

    def _best_point_for_text(
        self, lesson: dict[str, Any], content: str, covered_points: list[str]
    ) -> dict[str, Any]:
        text = normalize_text(content)
        prompt_tokens = self._meaningful_tokens(content)
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, point in enumerate(lesson["points"]):
            score = sum(
                5 for signal in point.get("signals", [])
                if contains_phrase(text, normalize_text(signal))
            )
            corpus = " ".join(
                [point["label"], point["ground_truth"], point["question"], point["recovery"]]
            )
            score += len(prompt_tokens & self._meaningful_tokens(corpus))
            if point["id"] not in covered_points:
                score += 1
            ranked.append((score, -index, point))
        return max(ranked, key=lambda item: (item[0], item[1]))[2]

    def _respond_without_assessment(
        self, session: dict[str, Any], lesson: dict[str, Any], content: str, *,
        intent: str, provider: str, model: str | None,
    ) -> dict[str, Any]:
        self.store.add_message(session["id"], "user", content)
        covered = list(session["covered_points"])
        missing = [point["id"] for point in lesson["points"] if point["id"] not in covered]
        target = self._best_point_for_text(lesson, content, covered)
        source_cards: list[dict[str, Any]] = []
        next_action: str | None = None
        if intent == "source_request":
            source_cards = self._sources_for_turn(
                lesson, [], target, include_transcript_quotes=True
            )
            ids = ", ".join(source["id"] for source in source_cards)
            response_text = (
                f"Đây là phần nguồn sát nhất với ý bạn đang hỏi: [{ids}]. "
                "Bạn có thể đối chiếu các trích đoạn bên dưới."
            )
        elif intent == "session_setup":
            response_text = (
                f"Được chứ. Mình sẽ cùng bạn ôn “{lesson['title']}”. "
                f"Bạn thử bắt đầu bằng cách giải thích theo lời mình nhé: {target['question']}"
            )
        elif intent == "clarification_question":
            response_text = (
                f"Có thể hiểu ngắn gọn thế này: {target['ground_truth']} "
                f"Bạn thử nối ý đó với câu hỏi này nhé: {target['question']}"
            )
        elif intent == "social":
            response_text = (
                f"Chào bạn! Mình đang sẵn sàng cùng bạn ôn “{lesson['title']}”. "
                f"Khi tiện, bạn bắt đầu từ ý này nhé: {target['question']}"
            )
        else:  # unmatched change_topic within an existing session
            response_text = (
                "Mình hiểu bạn muốn đổi chủ đề. Bạn nói thêm một khái niệm hoặc ví dụ của "
                "phần mới để mình chuyển đúng bài nhé."
            )
        metadata = {
            "status": "coaching", "progress": session["progress"],
            "covered_points": covered, "supported_points_this_turn": [],
            "misconceptions": session["misconceptions"], "source_cards": source_cards,
            "provider": provider, "model": model, "confidence": 1.0,
            "intent": intent, "next_action": next_action,
            "attempts_by_gap": session.get("attempts_by_gap", {}),
            "recovery_stage": session.get("recovery_stage", "none"),
            "target_point_id": target["id"], "diagnosis": {"type": "conversation_control"},
            "citations_valid": True,
        }
        message = self.store.add_message(session["id"], "assistant", response_text, metadata)
        self.store.update_session(
            session["id"], status="coaching", progress=session["progress"],
            covered_points=covered, misconceptions=session["misconceptions"],
            provider=provider, model=model,
            attempts_by_gap=session.get("attempts_by_gap", {}),
            last_target_gap=session.get("last_target_gap"),
            recovery_stage=session.get("recovery_stage", "none"),
            turn=session.get("turn", 0),
            awaiting_transfer=session.get("awaiting_transfer", False),
            transfer_passed=session.get("transfer_passed", False),
            mastery_complete=session.get("mastery_complete", False),
        )
        return {
            "session_id": session["id"], "lesson_id": lesson["id"],
            "status": "coaching", "next_action": next_action, "intent": intent,
            "progress": session["progress"], "covered_points": covered,
            "missing_points": missing, "misconceptions": session["misconceptions"],
            "diagnosis": {"type": "conversation_control"},
            "agent_response": response_text, "source_cards": source_cards,
            "citations_valid": True, "provider": provider, "model": model,
            "message": message, "tool_trace": [],
            "state": {
                "attempts_by_gap": session.get("attempts_by_gap", {}),
                "last_target_gap": session.get("last_target_gap"),
                "recovery_stage": session.get("recovery_stage", "none"),
                "awaiting_transfer": session.get("awaiting_transfer", False),
                "transfer_passed": session.get("transfer_passed", False),
                "mastery_complete": session.get("mastery_complete", False),
            },
        }

    def _sources_for_turn(
        self, lesson: dict[str, Any], supported_points: list[str],
        target_point: dict[str, Any] | None, *, include_transcript_quotes: bool = False,
    ) -> list[dict[str, Any]]:
        source_map = {source["id"]: source for source in lesson["sources"]}
        source_ids: list[str] = []
        if target_point:
            target_ids = list(target_point.get("source_ids", []))
            if include_transcript_quotes:
                target_ids.sort(key=lambda source_id: source_map.get(source_id, {}).get("type") != "transcript")
            source_ids.extend(target_ids[:3])
        for point in lesson["points"]:
            if point["id"] in supported_points:
                source_ids.extend(point.get("source_ids", [])[:1])
        sources = [dict(source_map[source_id]) for source_id in list(dict.fromkeys(source_ids))[:3]
                   if source_id in source_map]
        if include_transcript_quotes:
            for source in sources:
                quote = self._transcript_excerpt(source)
                if quote:
                    source["quote"] = quote
        return sources

    @staticmethod
    def _transcript_excerpt(source: dict[str, Any], max_chars: int = 360) -> str | None:
        if source.get("type") != "transcript":
            return None
        filename = Path(str(source.get("file", ""))).name
        locator = str(source.get("locator", "")).strip()
        if not filename or not re.fullmatch(r"T\d{2}-\d{3}", locator):
            return None
        candidate = (TRANSCRIPT_DIR / filename).resolve()
        if candidate.parent != TRANSCRIPT_DIR.resolve() or not candidate.is_file():
            return None
        marker = f"**[{locator}]**"
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.startswith(marker):
                excerpt = line[len(marker):].strip()
                return excerpt if len(excerpt) <= max_chars else excerpt[: max_chars - 1].rstrip() + "…"
        return None

    def _decorate_session(self, session: dict[str, Any], lesson: dict[str, Any]) -> dict[str, Any]:
        result = dict(session)
        result["lesson"] = self.catalog.public_lesson(lesson)
        return result

    def send_message(
        self,
        session_id: str,
        content: str,
        *,
        provider: str = "offline",
        model: str | None = None,
        api_key: str | None = None,
        allow_reroute: bool = True,
    ) -> dict[str, Any]:
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung tin nhắn không được để trống")
        if len(content) > 12_000:
            raise ValueError("Tin nhắn vượt quá 12.000 ký tự")
        session = self.store.get_session(session_id)
        if session is None:
            raise KeyError("session not found")
        lesson = self.catalog.get(session["lesson_id"])
        provider = provider.strip().lower()
        if provider not in {"offline", "openai"}:
            raise ValueError("provider phải là offline hoặc openai")

        selected_model = (
            (model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
            if provider == "openai"
            else None
        )
        intent = classify_turn_intent(content)
        router: OpenAILessonRouter | None = None
        if provider == "openai":
            router = OpenAILessonRouter(
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                model=selected_model or DEFAULT_MODEL,
                log_path=self.model_log_path,
            )

        # Conversational turns stay in the current session; only substantive
        # content or an explicit topic change may trigger rerouting.
        if allow_reroute and intent in {"teachback_answer", "change_topic", "session_setup"}:
            route = (
                router.route_lesson(self.catalog.all(), content)
                if router is not None
                else self._offline_route_lesson(content)
            )
            if (
                route["matched"]
                and route["lesson_id"] != lesson["id"]
                and float(route.get("confidence", 0)) >= 0.55
            ):
                switched = self.start_chat(
                    content, provider=provider, model=selected_model, api_key=api_key
                )
                switched["session_switched"] = True
                return switched

        # agent_core/agent_graph already has first-class, better-grounded
        # handling for teachback_answer/request_help/authority_attack/
        # out_of_scope (deterministic recommended_gap + escalation ladder).
        # Product-specific conversation controls do not alter mastery.
        if intent in {"source_request", "social", "session_setup", "clarification_question", "change_topic"}:
            return self._respond_without_assessment(
                session, lesson, content, intent=intent, provider=provider, model=selected_model
            )

        recent_messages = self.store.recent_messages(session_id)
        previous_agent_response = next(
            (
                str(message.get("content", ""))
                for message in reversed(recent_messages)
                if message.get("role") == "assistant"
            ),
            "",
        )
        self.store.add_message(session_id, "user", content)
        logger = AuditLogger(self.model_log_path)
        agent = self._agent_for(
            lesson["id"], provider=provider, model=selected_model, api_key=api_key, logger=logger
        )

        previous_state = {
            "session_id": session_id,
            "turn": session.get("turn", 0),
            "covered_points": session["covered_points"],
            "unresolved_misconceptions": session["misconceptions"],
            "attempts_by_gap": session.get("attempts_by_gap", {}),
            "last_target_gap": session.get("last_target_gap"),
            "recovery_stage": session.get("recovery_stage", "none"),
            "awaiting_transfer": session.get("awaiting_transfer", False),
            "transfer_passed": session.get("transfer_passed", False),
            "mastery_complete": session.get("mastery_complete", False),
            "last_agent_response": previous_agent_response,
        }
        result = agent.run_turn(content, previous_state)
        state = result["state"]
        assessed_intent = result["raw_assessment"].get("intent", "teachback_answer")

        metadata = {
            "status": result["status"],
            "progress": result["progress"],
            "covered_points": result["covered_points"],
            "supported_points_this_turn": result.get("grounded_claims", []),
            "misconceptions": result["misconceptions"],
            "source_cards": result["source_cards"],
            "provider": provider,
            "model": selected_model,
            "confidence": result.get("confidence", 0.0),
            "intent": assessed_intent,
            "next_action": result["next_action"],
            "attempts_by_gap": state["attempts_by_gap"],
            "recovery_stage": state["recovery_stage"],
            "target_point_id": result.get("target_gap"),
            "diagnosis": result["diagnosis"],
            "citations_valid": result["citations_valid"],
        }
        assistant_message = self.store.add_message(
            session_id, "assistant", result["agent_response"], metadata
        )
        self.store.update_session(
            session_id,
            status=result["status"],
            progress=result["progress"],
            covered_points=state["covered_points"],
            misconceptions=state["unresolved_misconceptions"],
            provider=provider,
            model=selected_model,
            attempts_by_gap=state["attempts_by_gap"],
            last_target_gap=state["last_target_gap"],
            recovery_stage=state["recovery_stage"],
            turn=state["turn"],
            awaiting_transfer=state["awaiting_transfer"],
            transfer_passed=state["transfer_passed"],
            mastery_complete=state["mastery_complete"],
        )
        tool_trace = list(result["tool_trace"])
        tool_trace.append(
            {
                "tool": "save_session",
                "input": {"session_id": session_id},
                "output": {"status": result["status"], "progress": result["progress"]},
            }
        )
        return {
            "session_id": session_id,
            "lesson_id": lesson["id"],
            "status": result["status"],
            "next_action": result["next_action"],
            "intent": assessed_intent,
            "progress": result["progress"],
            "covered_points": result["covered_points"],
            "missing_points": result["missing_points"],
            "misconceptions": result["misconceptions"],
            "diagnosis": result["diagnosis"],
            "agent_response": result["agent_response"],
            "source_cards": result["source_cards"],
            "citations_valid": result["citations_valid"],
            "provider": provider,
            "model": selected_model,
            "message": assistant_message,
            "tool_trace": tool_trace,
            "state": {
                "attempts_by_gap": state["attempts_by_gap"],
                "last_target_gap": state["last_target_gap"],
                "recovery_stage": state["recovery_stage"],
                "awaiting_transfer": state["awaiting_transfer"],
                "transfer_passed": state["transfer_passed"],
                "mastery_complete": state["mastery_complete"],
            },
        }
