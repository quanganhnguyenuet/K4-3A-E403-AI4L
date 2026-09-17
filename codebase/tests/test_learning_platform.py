from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


CODEBASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODEBASE))

from learning_platform import (  # noqa: E402
    ConversationStore,
    LessonCatalog,
    OpenAITurnAssessor,
    TeachBackPlatform,
)


class LearningPlatformTests(unittest.TestCase):
    def make_platform(self) -> TeachBackPlatform:
        temp_dir = Path(tempfile.mkdtemp(prefix="teachback-platform-test-"))
        return TeachBackPlatform(
            catalog=LessonCatalog(),
            store=ConversationStore(temp_dir / "learning.sqlite3"),
        )

    def test_catalog_contains_multiple_transcript_backed_lessons(self) -> None:
        lessons = LessonCatalog().list_public()
        self.assertGreaterEqual(len(lessons), 5)
        self.assertIn("ai-problem-scoping", {lesson["id"] for lesson in lessons})
        self.assertTrue(all(len(lesson["points"]) == 4 for lesson in lessons))

    def test_session_persists_full_message_history(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        result = platform.send_message(
            session["id"],
            "LLM dự đoán token tiếp theo theo một phân bố xác suất.",
            provider="offline",
        )
        restored = platform.get_session(session["id"])
        self.assertIsNotNone(restored)
        self.assertEqual([m["role"] for m in restored["messages"]], ["assistant", "user", "assistant"])
        self.assertIn("K1", restored["covered_points"])
        self.assertEqual(result["progress"], 25)

    def test_sessions_are_bound_to_their_lesson(self) -> None:
        platform = self.make_platform()
        first = platform.create_session("ai-problem-scoping")
        second = platform.create_session("metrics-and-automation")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(platform.get_session(first["id"])["lesson_id"], "ai-problem-scoping")
        self.assertEqual(platform.get_session(second["id"])["lesson_id"], "metrics-and-automation")

    def test_offline_out_of_scope_does_not_change_progress(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        result = platform.send_message(
            session["id"], "AI có thể giúp tôi viết CV như thế nào?", provider="offline"
        )
        self.assertEqual(result["status"], "out_of_scope")
        self.assertEqual(result["progress"], 0)
        self.assertEqual(result["covered_points"], [])

    def test_api_key_is_not_written_to_product_database(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        fake_key = "sk-should-never-be-persisted"
        # Offline ignores the key, which is enough to verify that storage APIs
        # have no key field and message metadata does not accidentally capture it.
        platform.send_message(
            session["id"],
            "LLM dự đoán token tiếp theo theo xác suất.",
            provider="offline",
            api_key=fake_key,
        )
        raw = platform.store.path.read_bytes()
        self.assertNotIn(fake_key.encode(), raw)
        restored = platform.get_session(session["id"])
        self.assertNotIn(fake_key, json.dumps(restored, ensure_ascii=False))

    def test_openai_response_schema_uses_supported_subset(self) -> None:
        assessor = OpenAITurnAssessor("sk-test", "test-model")
        captured: dict = {}

        def fake_request(**kwargs):
            captured.update(kwargs)
            return {
                "supported_points": ["K1", "K1"],
                "out_of_scope": False,
                "insufficient": False,
                "misconceptions": [],
                "confidence": 0.8,
                "feedback": "Đã ghi nhận.",
                "next_question": "Bạn bổ sung gì?",
            }

        assessor._request_structured = fake_request  # type: ignore[method-assign]
        lesson = LessonCatalog().get("why-llm-hallucinates")
        result = assessor.assess(lesson, "LLM dự đoán token.", [], [])
        self.assertNotIn("uniqueItems", json.dumps(captured["schema"]))
        self.assertEqual(result["supported_points"], ["K1"])

    def test_first_prompt_routes_and_creates_history(self) -> None:
        platform = self.make_platform()
        result = platform.start_chat(
            "Mình muốn ôn North Star Metric và các chỉ số dẫn dắt.",
            provider="offline",
        )
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["lesson_id"], "metrics-and-automation")
        self.assertEqual(len(result["session"]["messages"]), 2)
        self.assertEqual(len(platform.list_sessions()), 1)
        self.assertEqual(result["tool_trace"][0]["tool"], "route_lesson")

    def test_ambiguous_first_prompt_does_not_create_empty_session(self) -> None:
        platform = self.make_platform()
        result = platform.start_chat(
            "Mình muốn ôn lại phần này, bạn giúp mình nhé.", provider="offline"
        )
        self.assertTrue(result["needs_clarification"])
        self.assertIsNone(result["session_id"])
        self.assertEqual(platform.list_sessions(), [])

    def test_history_can_be_cleared_explicitly(self) -> None:
        platform = self.make_platform()
        platform.create_session("why-llm-hallucinates")
        platform.create_session("ai-problem-scoping")
        self.assertEqual(platform.clear_history(), 2)
        self.assertEqual(platform.list_sessions(), [])

    def test_failed_first_turn_does_not_leave_an_empty_session(self) -> None:
        platform = self.make_platform()

        def fail_message(*args, **kwargs):
            raise RuntimeError("simulated provider failure")

        platform.send_message = fail_message  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            platform.start_chat(
                "Mình muốn ôn North Star Metric và chỉ số dẫn dắt.",
                provider="offline",
            )
        self.assertEqual(platform.list_sessions(), [])

    def test_role_request_is_a_coaching_turn_not_out_of_scope(self) -> None:
        platform = self.make_platform()
        result = platform.start_chat(
            "Chào bạn, bạn có thể làm học viên cho tôi ôn tập về LLM không?",
            provider="offline",
        )
        self.assertEqual(result["lesson_id"], "why-llm-hallucinates")
        self.assertEqual(result["intent"], "learning_request")
        self.assertEqual(result["status"], "coaching")
        self.assertEqual(result["progress"], 0)
        self.assertNotIn("ngoài phạm vi", result["agent_response"])

    def test_help_request_asks_for_topic_without_creating_junk_history(self) -> None:
        platform = self.make_platform()
        result = platform.start_chat(
            "Tôi không biết gì và tôi muốn học lại thì xem ở đâu?",
            provider="offline",
        )
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["intent"], "help")
        self.assertIn("Không sao", result["agent_response"])
        self.assertEqual(platform.list_sessions(), [])

    def test_natural_topic_wording_routes_without_a_canned_question(self) -> None:
        platform = self.make_platform()
        result = platform.start_chat(
            "Tôi muốn học về phạm vi sản phẩm AI.",
            provider="offline",
        )
        self.assertEqual(result["lesson_id"], "ai-problem-scoping")
        self.assertEqual(result["intent"], "learning_request")
        self.assertEqual(result["status"], "coaching")

    def test_help_inside_session_preserves_mastery_progress(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        learned = platform.send_message(
            session["id"],
            "LLM dự đoán token tiếp theo theo phân bố xác suất.",
            provider="offline",
        )
        result = platform.send_message(
            session["id"],
            "Mình chưa hiểu phần tiếp theo, cho mình một gợi ý được không?",
            provider="offline",
        )
        self.assertEqual(result["intent"], "help")
        self.assertNotEqual(result["status"], "out_of_scope")
        self.assertEqual(result["progress"], learned["progress"])
        self.assertEqual(result["covered_points"], learned["covered_points"])

    def test_explicit_topic_change_creates_and_returns_the_new_session(self) -> None:
        platform = self.make_platform()
        old_session = platform.create_session("metrics-and-automation")
        result = platform.send_message(
            old_session["id"],
            "Tôi muốn chuyển sang ôn vì sao LLM có thể bịa.",
            provider="offline",
        )
        self.assertTrue(result["session_switched"])
        self.assertNotEqual(result["session_id"], old_session["id"])
        self.assertEqual(result["lesson_id"], "why-llm-hallucinates")

    def test_free_form_explanation_is_rerouted_without_a_switch_phrase(self) -> None:
        platform = self.make_platform()
        old_session = platform.create_session("metrics-and-automation")
        result = platform.send_message(
            old_session["id"],
            "LLM tạo văn bản bằng cách dự đoán token tiếp theo theo phân bố xác suất.",
            provider="offline",
        )
        self.assertTrue(result["session_switched"])
        self.assertEqual(result["lesson_id"], "why-llm-hallucinates")
        self.assertEqual(result["intent"], "teachback_answer")
        self.assertEqual(result["progress"], 25)

    def test_source_request_returns_locator_bound_transcript_excerpt(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        result = platform.send_message(
            session["id"],
            "Cho mình xem trích dẫn transcript về cơ chế sinh token.",
            provider="offline",
        )
        transcript_sources = [
            source for source in result["source_cards"] if source["type"] == "transcript"
        ]
        self.assertEqual(result["intent"], "source_request")
        self.assertEqual(result["progress"], 0)
        self.assertTrue(result["citations_valid"])
        self.assertTrue(transcript_sources)
        self.assertTrue(all(source.get("quote") for source in transcript_sources))
        self.assertTrue(all(len(source["quote"]) <= 360 for source in transcript_sources))
        self.assertIn(transcript_sources[0]["id"], result["agent_response"])

    def test_repeated_misconception_escalates_to_direct_recovery(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        wrong = "Temperature bằng 0 thì luôn đúng và LLM không thể bịa."
        first = platform.send_message(session["id"], wrong, provider="offline")
        second = platform.send_message(session["id"], wrong, provider="offline")
        self.assertEqual(first["status"], "misconception")
        self.assertEqual(first["diagnosis"]["type"], "misconception")
        self.assertIn("không bổ sung tri thức", first["agent_response"])
        self.assertEqual(second["status"], "needs_recovery")
        self.assertEqual(second["message"]["metadata"]["failure_streak"], 2)
        self.assertEqual(second["message"]["metadata"]["target_point_id"], "K4")
        self.assertIn("gợi ý trực tiếp", second["agent_response"])
        self.assertEqual(second["progress"], 0)

    def test_correct_answer_has_allowlisted_sources_and_diagnosis(self) -> None:
        platform = self.make_platform()
        session = platform.create_session("why-llm-hallucinates")
        result = platform.send_message(
            session["id"],
            "LLM dự đoán token tiếp theo theo một phân bố xác suất.",
            provider="offline",
        )
        registered = {
            source["id"]
            for source in platform.catalog.get("why-llm-hallucinates")["sources"]
        }
        self.assertEqual(result["diagnosis"]["type"], "supported")
        self.assertTrue(result["source_cards"])
        self.assertTrue(result["citations_valid"])
        self.assertLessEqual(
            {source["id"] for source in result["source_cards"]}, registered
        )


if __name__ == "__main__":
    unittest.main()
