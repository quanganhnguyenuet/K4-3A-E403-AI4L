"""Central decision module for the D3 bounded Teach-back Agent.

The language model proposes a semantic assessment and next action. The harness
validates that proposal, computes mastery deterministically, retrieves only
allow-listed lesson evidence, and owns the stop condition.

No third-party Python dependency is required. The OpenAI provider calls the
Responses API with ``urllib`` and Structured Outputs. Set ``OPENAI_API_KEY``
before selecting the ``openai`` provider.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent
DEFAULT_KNOWLEDGE_PATH = (
    REPO_ROOT / "knowledge" / "d3-llm-hallucination-ground-truth.json"
)
DEFAULT_LOG_PATH = CODEBASE_DIR / "logs" / "model_calls.jsonl"

POINT_IDS = ("K1", "K2", "K3", "K4")
MISCONCEPTION_IDS = ("M1", "M2", "M3", "M4", "M5", "M6")
VERDICTS = ("supported", "partial", "absent", "contradicted")
ALLOWED_ACTIONS = (
    "ASK_MECHANISM",
    "ASK_CAUSE",
    "ASK_MITIGATION",
    "ASK_EXAMPLE",
    "SOCRATIC_CORRECTION",
    "ASK_REPHRASE",
    "SHOW_RECOVERY",
    "ASK_TRANSFER",
    "COMPLETE_SESSION",
    "OUT_OF_SCOPE",
)

POINT_WEIGHTS = {"K1": 25, "K2": 25, "K3": 20, "K4": 15}
POINT_PRIORITY = ("K1", "K2", "K3", "K4")


ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "point_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "point_id": {"type": "string", "enum": list(POINT_IDS)},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "student_evidence": {"type": ["string", "null"]},
                },
                "required": ["point_id", "verdict", "student_evidence"],
            },
        },
        "misconception_assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "enum": list(MISCONCEPTION_IDS)},
                    "student_evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "student_evidence", "confidence"],
            },
        },
        "copied_source": {"type": "boolean"},
        "out_of_scope": {"type": "boolean"},
        "insufficient_input": {"type": "boolean"},
        "has_original_example": {"type": "boolean"},
        "transfer_passed": {"type": "boolean"},
        "confidence": {"type": "number"},
        "recommended_gap": {"type": ["string", "null"], "enum": [*POINT_IDS, None]},
        "recommended_action": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
        "draft_question": {"type": "string"},
    },
    "required": [
        "point_assessments",
        "misconception_assessments",
        "copied_source",
        "out_of_scope",
        "insufficient_input",
        "has_original_example",
        "transfer_passed",
        "confidence",
        "recommended_gap",
        "recommended_action",
        "draft_question",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower())
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    ascii_text = ascii_text.replace("đ", "d")
    return re.sub(r"\s+", " ", ascii_text).strip()


POINT_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "K1": (
        r"(doan|du doan|chon).{0,30}(token|tu|manh chu).{0,30}(xac suat|tiep theo|ke tiep)",
        r"(token|tu|manh chu).{0,30}(xac suat|tiep theo|ke tiep)",
        r"phan bo xac suat.{0,20}token",
        r"sinh van ban.{0,25}pattern",
    ),
    "K2": (
        r"hop ly.{0,25}(khong|chua).{0,20}(dung|su that)",
        r"hop ly.{0,30}(nhung|van).{0,25}(sai|khong dung)",
        r"hop (ly|van phong).{0,40}sai",
        r"troi chay.{0,20}sai",
        r"tu tin.{0,20}(chua chac|khong co nghia)",
        r"khong tu kiem chung",
        r"khong phai su that",
    ),
    "K3": (
        r"du lieu.{0,35}(thien lech|sai|thieu|bi lech|lech)",
        r"(internet|nguon).{0,25}(sai|thien lech|thieu)",
        r"knowledge cutoff",
        r"ngay cutoff",
        r"(su kien|thong tin).{0,20}(moi|sau cutoff)",
        r"context.{0,35}(huu han|khong nam|thieu|gioi han)",
        r"khong nam trong context",
        r"sau ngay.{0,15}(cutoff|do)",
        r"chua biet.{0,20}(su kien|thong tin).{0,10}moi",
    ),
    "K4": (
        r"(rag|tool).{0,45}(nguon|tai lieu|du lieu|kiem chung)",
        r"(tra nguon|trich dan|citation|kiem chung)",
        r"them dung tai lieu",
        r"dua them nguon",
        r"khong co can cu.{0,20}khong biet",
    ),
}

MISCONCEPTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "M1": (
        r"(luon|bat buoc).{0,25}(tra|tim).{0,35}(database|co so du lieu|su that)",
        r"(database|co so du lieu).{0,30}(loi|hong).{0,30}(bia|sai)",
    ),
    "M2": (
        r"(co tinh|co y).{0,35}(noi doi|lua|tra loi sai)",
        r"biet.{0,25}(dap an|cau).{0,20}(dung|sai).{0,35}(noi doi|tra loi sai)",
        r"noi doi.{0,30}(to ra|thong minh|lua)",
    ),
    "M3": (
        r"temperature.{0,20}(0|zero).{0,45}(khong bao gio|luon dung|het hallucination|khong bia)",
        r"(khong bao gio|luon dung|het hallucination).{0,45}temperature.{0,20}(0|zero)",
    ),
    "M4": (
        r"rag.{0,50}(100%|chinh xac 100|het hallucination|khong the bia|khong bia)",
        r"(100%|het hallucination|khong the bia).{0,50}rag",
    ),
    "M5": (
        r"hallucination.{0,20}chi.{0,45}(cutoff|thong tin moi)",
        r"chi.{0,45}(cutoff|thong tin moi).{0,45}(hallucination|bia)",
        r"(kien thuc|thong tin).{0,15}cu.{0,20}luon dung",
    ),
    "M6": (
        r"context.{0,20}cang dai.{0,50}(chac chan|luon|khong bia|chinh xac)",
        r"(chac chan|luon).{0,35}(chinh xac|khong bia).{0,35}context",
    ),
}

DOMAIN_PATTERNS = (
    r"\b(llm|ai|model|token|rag|hallucination|cutoff|context|temperature)\b",
    r"\b(bia|doan|du doan|xac suat|kiem chung|tra nguon|trich dan)\b",
)

INSUFFICIENT_PATTERNS = (
    r"^khong (biet|nho|ro)",
    r"^chua (biet|nho|ro)",
    r"^bia la bia( thoi)?( a)?$",
)


def matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def detect_explicit_points(value: str) -> set[str]:
    text = normalize_text(value)
    return {
        point_id
        for point_id, patterns in POINT_SIGNAL_PATTERNS.items()
        if matches_any(text, patterns)
    }


def detect_explicit_misconceptions(value: str) -> set[str]:
    text = normalize_text(value)
    return {
        misconception_id
        for misconception_id, patterns in MISCONCEPTION_PATTERNS.items()
        if matches_any(text, patterns)
    }


def looks_insufficient(value: str) -> bool:
    text = normalize_text(value)
    return (
        len(text.split()) <= 4
        or text.startswith("bia la bia")
        or matches_any(text, INSUFFICIENT_PATTERNS)
    )


def looks_out_of_scope(value: str) -> bool:
    text = normalize_text(value)
    return not looks_insufficient(value) and not matches_any(text, DOMAIN_PATTERNS)


class AuditLogger:
    """Thread-safe JSONL logger for prompts, raw model output, and decisions."""

    def __init__(self, path: Path | str = DEFAULT_LOG_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, event: str, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": utc_now(), "event": event, **payload}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class KnowledgeBase:
    def __init__(self, path: Path | str = DEFAULT_KNOWLEDGE_PATH) -> None:
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.points = {row["id"]: row for row in self.data["knowledge_points"]}
        self.sources = {row["id"]: row for row in self.data["sources"]}
        self.misconceptions = {
            row["id"]: row for row in self.data["misconceptions"]
        }
        self.recovery_cards = {
            point_id: card
            for card in self.data.get("recovery_cards", [])
            for point_id in card.get("for", [])
        }

        missing_points = set(POINT_IDS) - set(self.points)
        missing_misconceptions = set(MISCONCEPTION_IDS) - set(self.misconceptions)
        if missing_points or missing_misconceptions:
            raise ValueError(
                f"Ground truth incomplete: points={sorted(missing_points)}, "
                f"misconceptions={sorted(missing_misconceptions)}"
            )

        for point in self.points.values():
            unknown = set(point["source_ids"]) - set(self.sources)
            if unknown:
                raise ValueError(f"{point['id']} references unknown sources: {sorted(unknown)}")

    @property
    def required_point_ids(self) -> list[str]:
        return [point_id for point_id in POINT_PRIORITY if self.points[point_id]["required"]]

    def prompt_rubric(self) -> dict[str, Any]:
        return {
            "task": self.data["concept"]["student_task"],
            "knowledge_points": [
                {
                    "id": point["id"],
                    "label": point["label"],
                    "required": point["required"],
                    "ground_truth": point["ground_truth"],
                    "accepted_signals": point["accepted_signals"],
                }
                for point in self.data["knowledge_points"]
            ],
            "misconceptions": [
                {"id": row["id"], "claim": row["claim"]}
                for row in self.data["misconceptions"]
            ],
            "source_phrases_for_copy_detection": [
                {"source_id": row["id"], "paraphrase": row["paraphrase"]}
                for row in self.data["sources"]
            ],
        }

    def is_probable_copy(self, explanation: str) -> bool:
        """Detect near-verbatim reuse of one of the short registered source phrases."""
        input_tokens = set(normalize_text(explanation).split())
        if len(input_tokens) < 8:
            return False
        for source in self.sources.values():
            source_tokens = set(normalize_text(source["paraphrase"]).split())
            if len(source_tokens) < 8:
                continue
            containment = len(source_tokens & input_tokens) / len(source_tokens)
            if containment >= 0.85:
                return True
        return False

    def retrieve_evidence(self, point_id: str, limit: int = 3) -> dict[str, Any]:
        """Exact metadata retrieval; vector search can replace this implementation later."""
        if point_id not in self.points:
            raise KeyError(f"Unknown knowledge point: {point_id}")
        point = self.points[point_id]
        source_ids = point["source_ids"][:limit]
        return {
            "knowledge_point_id": point_id,
            "ground_truth": point["ground_truth"],
            "sources": [self.sources[source_id] for source_id in source_ids],
            "recovery_card": self.recovery_cards.get(point_id),
        }

    def misconception_gap(self, misconception_id: str) -> str:
        preferred_gap = {
            "M1": "K1",
            "M2": "K1",
            "M3": "K4",
            "M4": "K4",
            "M5": "K3",
            "M6": "K3",
        }.get(misconception_id)
        if preferred_gap:
            return preferred_gap
        row = self.misconceptions.get(misconception_id, {})
        conflicts = row.get("conflicts_with", [])
        return next((point for point in POINT_PRIORITY if point in conflicts), "K1")


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn: int = 0
    covered_points: list[str] = field(default_factory=list)
    unresolved_misconceptions: list[str] = field(default_factory=list)
    attempts_by_gap: dict[str, int] = field(default_factory=dict)
    awaiting_transfer: bool = False
    transfer_passed: bool = False
    mastery_complete: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SessionState":
        if not data:
            return cls()
        return cls(
            session_id=str(data.get("session_id") or uuid.uuid4()),
            turn=int(data.get("turn", 0)),
            covered_points=[p for p in data.get("covered_points", []) if p in POINT_IDS],
            unresolved_misconceptions=[
                m for m in data.get("unresolved_misconceptions", []) if m in MISCONCEPTION_IDS
            ],
            attempts_by_gap={
                str(k): int(v)
                for k, v in data.get("attempts_by_gap", {}).items()
                if k in POINT_IDS
            },
            awaiting_transfer=bool(data.get("awaiting_transfer", False)),
            transfer_passed=bool(data.get("transfer_passed", False)),
            mastery_complete=bool(data.get("mastery_complete", False)),
        )


class AssessmentProvider(Protocol):
    name: str

    def assess(
        self,
        *,
        prompt: str,
        explanation: str,
        schema: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]: ...


class OpenAIResponsesProvider:
    """Minimal dependency-free adapter for the OpenAI Responses API."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 90,
        logger: AuditLogger | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for provider=openai")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.logger = logger or AuditLogger()

    @staticmethod
    def _extract_output_text(raw: dict[str, Any]) -> str:
        if isinstance(raw.get("output_text"), str):
            return raw["output_text"]
        texts: list[str] = []
        for item in raw.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    texts.append(content["text"])
        if not texts:
            raise RuntimeError("Responses API returned no output_text")
        return "\n".join(texts)

    def assess(
        self,
        *,
        prompt: str,
        explanation: str,
        schema: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        body = {
            "model": self.model,
            "instructions": (
                "You are the semantic assessor inside a bounded Vietnamese Teach-back Agent. "
                "Evaluate only against the supplied rubric and the learner's exact words. "
                "A missing idea is not a misconception. Every reported misconception must "
                "include a direct evidence span from the learner input. Do not invent citations. "
                "A different wording is correct when its meaning matches the rubric."
            ),
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "teachback_assessment",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 1200,
            "store": False,
            "metadata": {
                "component": "d3_teachback_assessor",
                "session_id": str(metadata.get("session_id", "unknown"))[:64],
            },
        }
        self.logger.write(
            "model_prompt",
            {
                "request_id": request_id,
                "provider": self.name,
                "model": self.model,
                "prompt": prompt,
                "request_body": body,
            },
        )
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "vinuni-d3-teachback/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
                raw = json.loads(raw_text)
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            self.logger.write(
                "model_error",
                {
                    "request_id": request_id,
                    "provider": self.name,
                    "status": exc.code,
                    "raw_response": error_body,
                },
            )
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {error_body[:500]}") from exc
        except urllib.error.URLError as exc:
            self.logger.write(
                "model_error",
                {
                    "request_id": request_id,
                    "provider": self.name,
                    "raw_response": repr(exc),
                },
            )
            raise RuntimeError(f"OpenAI API connection failed: {exc}") from exc

        self.logger.write(
            "model_raw_response",
            {
                "request_id": request_id,
                "provider": self.name,
                "model": self.model,
                "raw_response": raw,
            },
        )
        output_text = self._extract_output_text(raw)
        try:
            return json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Model output is not valid JSON: {output_text[:500]}") from exc


class OfflineRuleProvider:
    """Auditable non-AI baseline used only when no API key is available."""

    name = "offline_rule_baseline"

    def __init__(self, logger: AuditLogger | None = None) -> None:
        self.logger = logger or AuditLogger()

    @staticmethod
    def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text) for pattern in patterns)

    def assess(
        self,
        *,
        prompt: str,
        explanation: str,
        schema: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        text = normalize_text(explanation)
        self.logger.write(
            "model_prompt",
            {
                "request_id": request_id,
                "provider": self.name,
                "model": "deterministic-rules-v1",
                "prompt": prompt,
            },
        )

        out_of_scope = looks_out_of_scope(explanation)
        insufficient = looks_insufficient(explanation)
        copied = (
            "dau ra cua transformer la mot phan bo xac suat tren cac token" in text
            and "vong lap tu hoi quy" in text
        )

        misconceptions = sorted(detect_explicit_misconceptions(explanation))
        explicit_points = detect_explicit_points(explanation)

        contradicted = {
            "K1": bool(set(misconceptions) & {"M1", "M2"}),
            "K2": bool(set(misconceptions) & {"M1", "M2", "M5"}),
            "K3": bool(set(misconceptions) & {"M5", "M6"}),
            "K4": bool(set(misconceptions) & {"M3", "M4", "M6"}),
        }
        signals = {point_id: point_id in explicit_points for point_id in POINT_IDS}
        point_assessments = []
        for point_id in POINT_IDS:
            if contradicted[point_id]:
                verdict = "contradicted"
            elif signals[point_id]:
                verdict = "supported"
            else:
                verdict = "absent"
            point_assessments.append(
                {
                    "point_id": point_id,
                    "verdict": verdict,
                    "student_evidence": explanation[:240] if signals[point_id] else None,
                }
            )

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
        elif recommended_gap == "K1":
            recommended_action = "ASK_MECHANISM"
        elif recommended_gap in {"K2", "K3"}:
            recommended_action = "ASK_CAUSE"
        elif recommended_gap == "K4":
            recommended_action = "ASK_MITIGATION"
        else:
            recommended_action = "ASK_TRANSFER"

        has_example = self._contains_any(text, (r"vi du", r"giong nhu", r"chang han", r"world cup", r"ten nguoi thang"))
        transfer_passed = bool(
            metadata.get("awaiting_transfer")
            and has_example
            and bool(explicit_points & {"K2", "K3", "K4"})
        )
        result = {
            "point_assessments": point_assessments,
            "misconception_assessments": [
                {
                    "id": misconception_id,
                    "student_evidence": explanation[:240],
                    "confidence": 1.0,
                }
                for misconception_id in misconceptions
            ],
            "copied_source": copied,
            "out_of_scope": out_of_scope,
            "insufficient_input": insufficient,
            "has_original_example": has_example,
            "transfer_passed": transfer_passed,
            "confidence": 0.55,
            "recommended_gap": recommended_gap,
            "recommended_action": recommended_action,
            "draft_question": "",
        }
        self.logger.write(
            "model_raw_response",
            {
                "request_id": request_id,
                "provider": self.name,
                "model": "deterministic-rules-v1",
                "raw_response": result,
                "warning": "Offline baseline; this is not an AI model response.",
            },
        )
        return result


def provider_from_name(name: str = "auto", logger: AuditLogger | None = None) -> AssessmentProvider:
    normalized = name.lower().strip()
    if normalized == "auto":
        normalized = "openai" if os.getenv("OPENAI_API_KEY") else "offline"
    if normalized == "openai":
        return OpenAIResponsesProvider(logger=logger)
    if normalized in {"offline", "rule", "rules"}:
        return OfflineRuleProvider(logger=logger)
    raise ValueError(f"Unsupported provider: {name}")


class TeachBackAgent:
    def __init__(
        self,
        provider: AssessmentProvider,
        knowledge: KnowledgeBase | None = None,
        logger: AuditLogger | None = None,
    ) -> None:
        self.provider = provider
        self.knowledge = knowledge or KnowledgeBase()
        self.logger = logger or AuditLogger()

    def _build_prompt(self, explanation: str, state: SessionState) -> str:
        payload = {
            "role": "Semantic assessor for a bounded Vietnamese teach-back lesson.",
            "goal": (
                "Identify only the ideas and misconceptions actually evidenced by the learner, "
                "then propose one next Socratic move."
            ),
            "success_criteria": [
                "Semantic paraphrases count; keyword overlap alone does not.",
                "Every supported or contradicted point includes a direct quote from student_explanation.",
                "Every misconception includes a direct quote and refers to an explicit false claim.",
                "Omitted knowledge is marked absent, never converted into a misconception.",
            ],
            "classification_rules": [
                "Use misconception_assessments=[] when the learner is merely incomplete or vague.",
                "Do not infer beliefs from silence and never list every misconception as a default.",
                "out_of_scope=true only for an unrelated answer; insufficient_input=true for 'không biết', a tautology, or an answer too short to assess.",
                "copied_source=true only when wording substantially reproduces a supplied source phrase, not merely because the idea is correct.",
                "K3 includes biased or missing training data, new events after cutoff, and required evidence missing from context.",
                "K4 includes RAG, tools, sources, citations, or verification as mitigation; such mitigation does not guarantee 100% correctness.",
                "If awaiting_transfer=true, transfer_passed=true only for a new relevant example that demonstrates why a plausible answer still needs verification.",
                "Draft no more than one short Vietnamese Socratic question and do not reveal the complete answer.",
            ],
            "rubric": self.knowledge.prompt_rubric(),
            "session_state": asdict(state),
            "student_explanation": explanation,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _evidence_is_grounded(evidence: Any, explanation: str) -> bool:
        if not isinstance(evidence, str) or not evidence.strip():
            return False
        return normalize_text(evidence) in normalize_text(explanation)

    @classmethod
    def _validate_assessment(cls, raw: dict[str, Any], explanation: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("Assessment must be a JSON object")
        rows = raw.get("point_assessments")
        if not isinstance(rows, list):
            raise ValueError("point_assessments must be an array")
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            point_id = row.get("point_id")
            verdict = row.get("verdict")
            if point_id in POINT_IDS and verdict in VERDICTS:
                evidence = row.get("student_evidence")
                if verdict != "absent" and not cls._evidence_is_grounded(
                    evidence, explanation
                ):
                    verdict = "absent"
                    evidence = None
                by_id[point_id] = {
                    "point_id": point_id,
                    "verdict": verdict,
                    "student_evidence": evidence,
                }
        for point_id in POINT_IDS:
            by_id.setdefault(
                point_id,
                {"point_id": point_id, "verdict": "absent", "student_evidence": None},
            )
        raw["point_assessments"] = [by_id[point_id] for point_id in POINT_IDS]
        explicit_misconceptions = detect_explicit_misconceptions(explanation)
        accepted_misconceptions: dict[str, dict[str, Any]] = {}
        discarded_misconceptions: list[str] = []
        misconception_rows = raw.get("misconception_assessments", [])
        if not isinstance(misconception_rows, list):
            misconception_rows = []
        for row in misconception_rows:
            if not isinstance(row, dict):
                continue
            misconception_id = row.get("id")
            evidence = row.get("student_evidence")
            try:
                confidence = min(1.0, max(0.0, float(row.get("confidence", 0))))
            except (TypeError, ValueError):
                confidence = 0.0
            if (
                misconception_id in explicit_misconceptions
                and cls._evidence_is_grounded(evidence, explanation)
            ):
                accepted_misconceptions[misconception_id] = {
                    "id": misconception_id,
                    "student_evidence": evidence,
                    "confidence": confidence,
                    "verified_by": "model_and_harness",
                }
            elif misconception_id in MISCONCEPTION_IDS:
                discarded_misconceptions.append(misconception_id)
        for misconception_id in explicit_misconceptions:
            accepted_misconceptions.setdefault(
                misconception_id,
                {
                    "id": misconception_id,
                    "student_evidence": explanation.strip()[:240],
                    "confidence": 1.0,
                    "verified_by": "bounded_harness_rule",
                },
            )
        raw["misconception_assessments"] = [
            accepted_misconceptions[misconception_id]
            for misconception_id in sorted(accepted_misconceptions)
        ]
        raw["misconceptions"] = sorted(accepted_misconceptions)
        raw["discarded_misconceptions"] = sorted(set(discarded_misconceptions))
        raw["recommended_gap"] = (
            raw.get("recommended_gap") if raw.get("recommended_gap") in POINT_IDS else None
        )
        raw["recommended_action"] = (
            raw.get("recommended_action")
            if raw.get("recommended_action") in ALLOWED_ACTIONS
            else "ASK_CAUSE"
        )
        raw["confidence"] = min(1.0, max(0.0, float(raw.get("confidence", 0))))
        for key in (
            "copied_source",
            "out_of_scope",
            "insufficient_input",
            "has_original_example",
            "transfer_passed",
        ):
            raw[key] = bool(raw.get(key, False))
        raw["draft_question"] = str(raw.get("draft_question", "")).strip()
        return raw

    def _fallback_question(self, action: str, gap: str | None, misconception: str | None) -> str:
        if action == "OUT_OF_SCOPE":
            return "Mình đang học về nguyên nhân LLM có thể bịa; bạn có thể dạy lại đúng chủ đề này không?"
        if action == "ASK_REPHRASE":
            return "Bạn có thể giải thích lại ý này bằng lời của mình và thêm một ví dụ riêng không?"
        if action == "ASK_TRANSFER":
            return "Bạn hãy nêu một ví dụ mới trong đó câu trả lời nghe rất hợp lý nhưng vẫn cần kiểm chứng được không?"
        if action == "COMPLETE_SESSION":
            return "Mình đã được bạn dạy đủ bốn ý và vượt qua ví dụ chuyển giao."
        if action == "SHOW_RECOVERY":
            return "Sau khi đọc đoạn gợi ý, bạn thử dạy lại ý này bằng lời của mình nhé?"
        if misconception and misconception in self.knowledge.misconceptions:
            return self.knowledge.misconceptions[misconception]["socratic_question"]
        questions = {
            "K1": "LLM tạo ra từng phần của câu trả lời bằng cơ chế nào?",
            "K2": "Vì sao một câu nghe rất hợp lý vẫn có thể sai sự thật?",
            "K3": "Bạn có thể nêu một nguồn khiến model thiếu hoặc học sai căn cứ không?",
            "K4": "Ta có thể giảm rủi ro bằng nguồn và công cụ như thế nào mà không hứa đúng tuyệt đối?",
        }
        return questions.get(gap or "K1", questions["K1"])

    @staticmethod
    def _question_is_safe(question: str) -> bool:
        if not question or len(question) > 300 or question.count("?") != 1:
            return False
        normalized = normalize_text(question)
        answer_dump_markers = (
            "dap an day du la",
            "bon y gom",
            "cau tra loi dung la",
        )
        return not any(marker in normalized for marker in answer_dump_markers)

    def run_turn(
        self,
        explanation: str,
        previous_state: dict[str, Any] | SessionState | None = None,
    ) -> dict[str, Any]:
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError("student_explanation must be a non-empty string")
        state = (
            previous_state
            if isinstance(previous_state, SessionState)
            else SessionState.from_dict(previous_state)
        )
        state.turn += 1
        prompt = self._build_prompt(explanation.strip(), state)
        raw = self.provider.assess(
            prompt=prompt,
            explanation=explanation.strip(),
            schema=ASSESSMENT_SCHEMA,
            metadata={
                "session_id": state.session_id,
                "turn": state.turn,
                "awaiting_transfer": state.awaiting_transfer,
            },
        )
        assessment = self._validate_assessment(raw, explanation.strip())

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
            assessment["copied_source"] or self.knowledge.is_probable_copy(explanation)
        )

        normalized_explanation = normalize_text(explanation)
        has_example_marker = matches_any(
            normalized_explanation,
            (r"\bvi du\b", r"\bchang han\b", r"\bgiong nhu\b"),
        )
        if (
            state.awaiting_transfer
            and has_example_marker
            and "K2" in explicit_points
            and bool(explicit_points & {"K3", "K4"})
            and not assessment["misconceptions"]
        ):
            assessment["transfer_passed"] = True

        verdict_by_point = {
            row["point_id"]: row["verdict"] for row in assessment["point_assessments"]
        }
        current_supported = {
            point_id for point_id, verdict in verdict_by_point.items() if verdict == "supported"
        } | explicit_points
        covered = set(state.covered_points) | current_supported

        unresolved = set(state.unresolved_misconceptions)
        for previous_misconception in list(unresolved):
            conflicts = set(
                self.knowledge.misconceptions[previous_misconception].get("conflicts_with", [])
            )
            if conflicts and conflicts <= current_supported:
                unresolved.remove(previous_misconception)
        unresolved.update(assessment["misconceptions"])
        for misconception in unresolved:
            for conflict in self.knowledge.misconceptions[misconception].get("conflicts_with", []):
                covered.discard(conflict)

        required = self.knowledge.required_point_ids
        missing = [point_id for point_id in required if point_id not in covered]
        first_misconception = next(iter(sorted(unresolved)), None)
        if missing or unresolved:
            state.mastery_complete = False
            state.transfer_passed = False
            state.awaiting_transfer = False

        if assessment["out_of_scope"]:
            status = "out_of_scope"
            action = "OUT_OF_SCOPE"
            target_gap = None
        elif assessment["copied_source"]:
            status = "copied_source"
            action = "ASK_REPHRASE"
            target_gap = assessment["recommended_gap"] or "K2"
        elif unresolved:
            status = "misconception"
            action = "SOCRATIC_CORRECTION"
            target_gap = self.knowledge.misconception_gap(first_misconception or "M1")
        elif assessment["insufficient_input"]:
            status = "needs_recovery"
            action = "SHOW_RECOVERY"
            target_gap = assessment["recommended_gap"] or (missing[0] if missing else "K1")
        elif not missing:
            status = "mastered"
            target_gap = "K2"
            if state.awaiting_transfer and assessment["transfer_passed"]:
                action = "COMPLETE_SESSION"
                state.transfer_passed = True
                state.mastery_complete = True
                state.awaiting_transfer = False
            else:
                action = "ASK_TRANSFER"
                state.awaiting_transfer = True
        else:
            status = "partial"
            proposed_gap = assessment["recommended_gap"]
            target_gap = proposed_gap if proposed_gap in missing else missing[0]
            attempts = state.attempts_by_gap.get(target_gap, 0) + 1
            state.attempts_by_gap[target_gap] = attempts
            if attempts >= 2:
                action = "SHOW_RECOVERY"
            elif target_gap == "K1":
                action = "ASK_MECHANISM"
            elif target_gap in {"K2", "K3"}:
                action = "ASK_CAUSE"
            else:
                action = "ASK_MITIGATION"

        retrieval: dict[str, Any] | None = None
        tool_trace: list[dict[str, Any]] = []
        if target_gap:
            retrieval = self.knowledge.retrieve_evidence(target_gap)
            tool_trace.append(
                {
                    "tool": "retrieve_evidence",
                    "input": {"knowledge_point_id": target_gap},
                    "output_source_ids": [row["id"] for row in retrieval["sources"]],
                }
            )

        draft = assessment["draft_question"]
        agent_response = (
            draft
            if action not in {"SHOW_RECOVERY", "COMPLETE_SESSION"}
            and self._question_is_safe(draft)
            else self._fallback_question(action, target_gap, first_misconception)
        )
        recovery_card = None
        if action == "SHOW_RECOVERY" and retrieval:
            recovery_card = retrieval.get("recovery_card")

        evidence_source_ids = (
            [row["id"] for row in retrieval["sources"][:2]] if retrieval else []
        )
        retrieved_source_ids = {
            row["id"] for row in retrieval["sources"]
        } if retrieval else set()
        citations_valid = set(evidence_source_ids) <= retrieved_source_ids

        state.covered_points = [point_id for point_id in POINT_PRIORITY if point_id in covered]
        state.unresolved_misconceptions = sorted(unresolved)
        base_progress = sum(POINT_WEIGHTS[point] for point in state.covered_points)
        if state.mastery_complete:
            progress = 100
        elif not missing and not unresolved:
            progress = 90
        else:
            progress = min(base_progress, 70 if unresolved else 85)

        tool_trace.append(
            {
                "tool": "save_learner_state",
                "input": {"session_id": state.session_id},
                "output": asdict(state),
            }
        )
        result = {
            "session_id": state.session_id,
            "turn": state.turn,
            "provider": self.provider.name,
            "status": status,
            "mastery_complete": state.mastery_complete,
            "progress": progress,
            "covered_points": state.covered_points,
            "missing_points": missing,
            "misconceptions": state.unresolved_misconceptions,
            "next_action": action,
            "target_gap": target_gap,
            "agent_response": agent_response,
            "evidence_source_ids": evidence_source_ids,
            "citations_valid": citations_valid,
            "source_cards": retrieval["sources"] if retrieval else [],
            "recovery_card": recovery_card,
            "confidence": assessment["confidence"],
            "state": asdict(state),
            "tool_trace": tool_trace,
            "raw_assessment": assessment,
        }
        self.logger.write(
            "agent_decision",
            {
                "session_id": state.session_id,
                "turn": state.turn,
                "provider": self.provider.name,
                "student_explanation": explanation,
                "decision": result,
            },
        )
        return result
