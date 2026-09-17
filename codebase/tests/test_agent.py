from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


CODEBASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODEBASE))

from agent_core import (  # noqa: E402
    AuditLogger,
    KnowledgeBase,
    OfflineRuleProvider,
    OpenAIResponsesProvider,
    TeachBackAgent,
)


class TeachBackAgentTests(unittest.TestCase):
    def make_agent(self) -> TeachBackAgent:
        temp_dir = Path(tempfile.mkdtemp(prefix="d3-agent-test-"))
        logger = AuditLogger(temp_dir / "audit.jsonl")
        return TeachBackAgent(
            provider=OfflineRuleProvider(logger=logger),
            knowledge=KnowledgeBase(),
            logger=logger,
        )

    def test_ground_truth_sources_resolve(self) -> None:
        knowledge = KnowledgeBase()
        for point_id in ("K1", "K2", "K3", "K4"):
            result = knowledge.retrieve_evidence(point_id)
            self.assertTrue(result["sources"])
            self.assertTrue(all(row["id"] in knowledge.sources for row in result["sources"]))

    def test_question_drafter_receives_learner_wording_and_natural_style_rules(self) -> None:
        provider = OpenAIResponsesProvider(api_key="sk-test", model="test-model")
        captured: dict = {}

        def fake_request(**kwargs):
            captured.update(kwargs)
            return {"question": "Bạn đang vướng ở chỗ phân biệt usage và outcome; thử nghĩ xem người học thật sự cần đạt kết quả gì?"}

        provider._request_structured = fake_request  # type: ignore[method-assign]
        result = provider.draft_question(
            target_gap="K1",
            action="NARROW_QUESTION",
            misconception=None,
            rubric_excerpt={
                "task": "Chọn metric phù hợp",
                "learner_message": "Mình vẫn chưa hình dung metric giá trị là gì",
                "previous_agent_response": "Metric nào cho thấy người dùng nhận được giá trị?",
                "support_stage": "narrowed_question",
                "ground_truth": "Outcome phản ánh giá trị thật.",
                "accepted_signals": ["outcome"],
            },
            feedback=None,
            metadata={"session_id": "style-test", "turn": 2},
        )

        self.assertIn("learner_message: Mình vẫn chưa hình dung", captured["input_text"])
        self.assertIn("previous_agent_response: Metric nào", captured["input_text"])
        self.assertIn("one or two concise sentences", captured["instructions"])
        self.assertEqual(result["draft_question"].count("?"), 1)

    def test_recovery_drafter_allows_a_natural_explanation_without_a_question(self) -> None:
        provider = OpenAIResponsesProvider(api_key="sk-test", model="test-model")
        captured: dict = {}

        def fake_request(**kwargs):
            captured.update(kwargs)
            return {"question": "Mình nói thẳng ý cốt lõi nhé: model dự đoán token kế tiếp theo xác suất, chứ không mặc định tra cứu sự thật."}

        provider._request_structured = fake_request  # type: ignore[method-assign]
        result = provider.draft_question(
            target_gap="K1",
            action="SHOW_RECOVERY",
            misconception=None,
            rubric_excerpt={
                "task": "Giải thích cách LLM hoạt động",
                "learner_message": "Mình bí rồi, bạn trả lời giúp mình",
                "ground_truth": "LLM dự đoán token tiếp theo theo xác suất.",
                "recovery_text": "Bắt đầu từ token và xác suất.",
                "example_starting_points": ["dự đoán token tiếp theo"],
                "source_ids": ["D1-S10"],
            },
            feedback=None,
            metadata={"session_id": "recovery-style-test", "turn": 4},
        )

        self.assertIn("a question is not required", captured["instructions"])
        self.assertEqual(result["draft_question"].count("?"), 0)

    def test_contextual_writer_handles_redirect_and_recovery_before_static_fallback(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="d3-context-writer-test-"))
        logger = AuditLogger(temp_dir / "audit.jsonl")

        class ContextualProvider(OfflineRuleProvider):
            def __init__(self):
                super().__init__(logger=logger)
                self.actions: list[str] = []

            def draft_question(self, **kwargs):
                action = kwargs["action"]
                self.actions.append(action)
                if action == "OUT_OF_SCOPE":
                    return {
                        "draft_question": "Chuyện thời tiết nằm ngoài buổi ôn này; mình quay lại cách LLM tạo câu trả lời nhé."
                    }
                if action == "SHOW_RECOVERY":
                    return {
                        "draft_question": "Ý cốt lõi là LLM dự đoán token tiếp theo theo xác suất và lặp lại quá trình, chứ không mặc định tra cứu sự thật."
                    }
                return {"draft_question": "Bạn thử nói rõ thêm một ý được không?"}

        provider = ContextualProvider()
        agent = TeachBackAgent(
            provider=provider,
            knowledge=KnowledgeBase(),
            logger=logger,
        )
        redirect = agent.run_turn("Dự báo thời tiết ngày mai giúp mình")
        recovery = agent.run_turn(
            "Tôi không biết nữa, bạn có thể trả lời tôi",
            {"last_target_gap": "K1"},
        )

        self.assertEqual(provider.actions, ["OUT_OF_SCOPE", "SHOW_RECOVERY"])
        self.assertIn("Chuyện thời tiết", redirect["agent_response"])
        self.assertIn("dự đoán token tiếp theo", recovery["agent_response"])

    def test_complete_explanation_routes_to_transfer(self) -> None:
        result = self.make_agent().run_turn(
            "LLM dự đoán token theo xác suất; câu hợp lý chưa chắc đúng vì dữ liệu có thể lệch. "
            "RAG và kiểm chứng nguồn giúp giảm rủi ro nhưng không bảo đảm tuyệt đối."
        )
        self.assertEqual(result["status"], "mastered")
        self.assertEqual(result["next_action"], "ASK_TRANSFER")
        self.assertEqual(result["progress"], 90)
        self.assertFalse(result["mastery_complete"])

    def test_misconception_cannot_be_mastered(self) -> None:
        result = self.make_agent().run_turn(
            "Chỉ cần đặt temperature bằng 0 thì model luôn đúng và không bao giờ bịa."
        )
        self.assertEqual(result["status"], "misconception")
        self.assertIn("M3", result["misconceptions"])
        self.assertFalse(result["mastery_complete"])
        self.assertEqual(result["diagnosis"]["type"], "misconception")
        self.assertTrue(result["diagnosis"]["entries"][0]["why_wrong"])
        self.assertIn("kiểm tra lại mệnh đề", result["agent_response"])

    def test_repeated_misconception_switches_to_recovery_card(self) -> None:
        result = self.make_agent().run_turn(
            "Gắn RAG vào là chính xác 100% và không thể bịa.",
            {
                "session_id": "unit-repeated-misconception",
                "covered_points": ["K1", "K2", "K3"],
                "unresolved_misconceptions": ["M4"],
                "attempts_by_gap": {"K4": 3},
            },
        )
        self.assertEqual(result["next_action"], "SHOW_RECOVERY")
        self.assertIsNotNone(result["recovery_card"])
        self.assertIn("Mình nói thẳng đáp án nhé", result["agent_response"])
        self.assertTrue(result["evidence_source_ids"])

    def test_direct_answer_request_exits_an_active_misconception_loop(self) -> None:
        result = self.make_agent().run_turn(
            "Em chịu rồi, cho em đáp án được không?",
            {
                "session_id": "unit-direct-answer",
                "covered_points": ["K1", "K2", "K3"],
                "unresolved_misconceptions": ["M4"],
                "attempts_by_gap": {"K4": 1},
                "last_target_gap": "K4",
            },
        )

        self.assertEqual(result["next_action"], "SHOW_RECOVERY")
        self.assertIn("Mình nói thẳng đáp án nhé", result["agent_response"])
        self.assertIn("không tạo bảo đảm đúng tuyệt đối", result["agent_response"])

    def test_natural_can_you_answer_me_phrase_shows_the_answer(self) -> None:
        result = self.make_agent().run_turn(
            "Tôi không biết nữa, bạn có thể trả lời tôi",
            {
                "session_id": "unit-natural-direct-answer",
                "last_target_gap": "K1",
                "attempts_by_gap": {"K1": 1},
            },
        )

        self.assertEqual(result["raw_assessment"]["intent"], "request_help")
        self.assertEqual(result["next_action"], "SHOW_RECOVERY")
        self.assertIn("Mình nói thẳng đáp án nhé", result["agent_response"])
        self.assertIn("dự đoán token tiếp theo", result["agent_response"])

    def test_second_and_third_failed_attempts_do_not_leak_full_recovery(self) -> None:
        agent = self.make_agent()
        second = agent.run_turn(
            "Gắn RAG vào là chính xác 100% và không thể bịa.",
            {"attempts_by_gap": {"K4": 1}},
        )
        third = agent.run_turn(
            "Gắn RAG vào là chính xác 100% và không thể bịa.",
            {"attempts_by_gap": {"K4": 2}},
        )
        self.assertEqual(second["next_action"], "NARROW_QUESTION")
        self.assertEqual(second["state"]["recovery_stage"], "narrowed_question")
        self.assertEqual(third["next_action"], "CONTROLLED_HINT")
        self.assertEqual(third["state"]["recovery_stage"], "controlled_hint")

    def test_generic_absolute_overclaim_is_mapped_to_mitigation_misconception(self) -> None:
        result = self.make_agent().run_turn(
            "Fine-tune đúng trên dữ liệu domain thì model luôn đúng và không bao giờ hallucinate."
        )
        self.assertIn("M4", result["misconceptions"])
        self.assertEqual(result["target_gap"], "K4")

    def test_explicitly_rejecting_rag_absolutism_resolves_misconception(self) -> None:
        result = self.make_agent().run_turn(
            "RAG chỉ bổ sung nguồn để giảm rủi ro; nếu truy xuất sai thì vẫn có thể bịa "
            "nên không bảo đảm chính xác 100%.",
            {
                "session_id": "unit-resolve-misconception",
                "covered_points": ["K1", "K2", "K3"],
                "unresolved_misconceptions": ["M4"],
                "attempts_by_gap": {"K4": 1},
            },
        )
        self.assertEqual(result["misconceptions"], [])
        self.assertEqual(result["status"], "mastered")
        self.assertEqual(result["next_action"], "ASK_TRANSFER")

    def test_citations_are_subset_of_retrieved_sources(self) -> None:
        result = self.make_agent().run_turn("LLM bịa vì nó dự đoán token tiếp theo theo xác suất.")
        retrieved = set(result["tool_trace"][0]["output_source_ids"])
        self.assertLessEqual(set(result["evidence_source_ids"]), retrieved)
        self.assertTrue(result["citations_valid"])

    def test_missing_ideas_are_not_misconceptions(self) -> None:
        result = self.make_agent().run_turn(
            "LLM bịa vì nó dự đoán token tiếp theo theo xác suất."
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["misconceptions"], [])
        self.assertIn("K1", result["covered_points"])

    def test_harness_discards_ungrounded_misconception_labels(self) -> None:
        explanation = "LLM bịa vì nó dự đoán token tiếp theo theo xác suất."
        raw = {
            "point_assessments": [
                {"point_id": point_id, "verdict": "absent", "student_evidence": None}
                for point_id in ("K1", "K2", "K3", "K4")
            ],
            "misconception_assessments": [
                {"id": misconception_id, "student_evidence": explanation, "confidence": 0.9}
                for misconception_id in ("M1", "M2", "M3", "M4", "M5", "M6")
            ],
            "copied_source": False,
            "out_of_scope": False,
            "insufficient_input": False,
            "has_original_example": False,
            "transfer_passed": False,
            "confidence": 0.9,
            "recommended_gap": "K2",
            "recommended_action": "ASK_CAUSE",
            "draft_question": "Vì sao câu hợp lý vẫn có thể sai?",
        }
        validated = self.make_agent()._validate_assessment(raw, explanation)
        self.assertEqual(validated["misconceptions"], [])
        self.assertEqual(
            validated["discarded_misconceptions"],
            ["M1", "M2", "M3", "M4", "M5", "M6"],
        )

    def test_out_of_scope_is_not_treated_as_forgotten(self) -> None:
        result = self.make_agent().run_turn(
            "Em thích học bằng video hơn và hôm nay mạng hơi chậm."
        )
        self.assertEqual(result["status"], "out_of_scope")
        self.assertEqual(result["next_action"], "OUT_OF_SCOPE")

    def test_probable_source_copy_requests_rephrase(self) -> None:
        result = self.make_agent().run_turn(
            "Đầu ra của Transformer là một phân bố xác suất trên các token. "
            "Mỗi token được nối vào ngữ cảnh rồi model tiếp tục dự đoán token kế tiếp "
            "trong một vòng lặp tự hồi quy."
        )
        self.assertEqual(result["status"], "copied_source")
        self.assertEqual(result["next_action"], "ASK_REPHRASE")

    def test_transfer_pass_is_the_only_path_to_complete(self) -> None:
        result = self.make_agent().run_turn(
            "Ví dụ mới: model được hỏi người vừa đoạt giải sáng nay và nêu một cái tên "
            "nghe rất hợp lý nhưng có thể sai vì chưa có nguồn, nên vẫn phải kiểm chứng.",
            {
                "session_id": "unit-transfer",
                "covered_points": ["K1", "K2", "K3", "K4"],
                "awaiting_transfer": True,
            },
        )
        self.assertEqual(result["next_action"], "COMPLETE_SESSION")
        self.assertTrue(result["mastery_complete"])
        self.assertEqual(result["progress"], 100)

    def test_new_misconception_revokes_conflicting_mastery(self) -> None:
        result = self.make_agent().run_turn(
            "LLM đoán token theo xác suất, nhưng context càng dài thì nó luôn chính xác "
            "và chắc chắn không bịa nữa.",
            {
                "session_id": "unit-regression",
                "covered_points": ["K1", "K2", "K3", "K4"],
                "awaiting_transfer": True,
                "transfer_passed": True,
                "mastery_complete": True,
            },
        )
        self.assertEqual(result["status"], "misconception")
        self.assertEqual(result["misconceptions"], ["M6"])
        self.assertEqual(result["covered_points"], ["K1", "K2"])
        self.assertFalse(result["mastery_complete"])


if __name__ == "__main__":
    unittest.main()
