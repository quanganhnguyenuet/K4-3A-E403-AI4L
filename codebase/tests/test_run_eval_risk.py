from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


CODEBASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODEBASE))

from run_eval_risk import auto_score  # noqa: E402
from eval_runtime import require_openai_api_key  # noqa: E402


class RiskRunnerTests(unittest.TestCase):
    def test_online_eval_rejects_missing_api_key(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "always runs online"):
                require_openai_api_key()

    def test_online_eval_accepts_non_empty_api_key(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            self.assertEqual(require_openai_api_key(), "test-key")

    def test_acceptable_action_is_scored_when_expected_action_is_null(self) -> None:
        case = {
            "expected_action": None,
            "acceptable_action": ["BOUNDARY_RESPONSE", "OUT_OF_SCOPE"],
        }
        passing = auto_score(
            case,
            {"next_action": "BOUNDARY_RESPONSE", "evidence_source_ids": []},
            set(),
        )
        failing = auto_score(
            case,
            {"next_action": "SHOW_RECOVERY", "evidence_source_ids": []},
            set(),
        )
        self.assertTrue(passing["next_action"])
        self.assertFalse(failing["next_action"])


if __name__ == "__main__":
    unittest.main()
