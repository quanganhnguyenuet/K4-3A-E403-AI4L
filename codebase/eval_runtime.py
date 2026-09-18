"""Shared runtime guards for publishable model evaluations."""

from __future__ import annotations

import os


ONLINE_EVAL_PROVIDER = "openai"


def require_openai_api_key() -> str:
    """Return the configured key or reject an evaluation before artifacts are written."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Golden-set evaluation requires OPENAI_API_KEY and always runs online "
            "with OpenAI. Offline/automatic fallback is disabled."
        )
    return api_key
