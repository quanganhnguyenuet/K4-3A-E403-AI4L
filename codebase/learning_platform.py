"""Multi-lesson teach-back service used by the web application.

The evaluation harness in :mod:`agent_core` remains unchanged.  This module is
the product-facing layer: it loads a lesson catalog, keeps durable sessions and
messages, and can assess a turn either with transparent offline rules or with
the OpenAI Responses API.  API keys are accepted per request and are never
written to SQLite or logs.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent
DEFAULT_CATALOG_PATH = REPO_ROOT / "knowledge" / "lesson_catalog.json"
DEFAULT_DB_PATH = CODEBASE_DIR / "state" / "learning.sqlite3"
DEFAULT_MODEL = "gpt-5-mini"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower())
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    ascii_text = ascii_text.replace("đ", "d")
    return re.sub(r"\s+", " ", ascii_text).strip()


def contains_phrase(normalized_text: str, normalized_phrase: str) -> bool:
    """Match a normalized word or phrase without accidental substring hits."""
    if not normalized_phrase:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])",
            normalized_text,
        )
    )


class LessonCatalog:
    def __init__(self, path: Path | str = DEFAULT_CATALOG_PATH) -> None:
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        lessons = payload.get("lessons", [])
        if not isinstance(lessons, list) or not lessons:
            raise ValueError("lesson catalog must contain at least one lesson")
        self._lessons: dict[str, dict[str, Any]] = {}
        for lesson in lessons:
            self._validate_lesson(lesson)
            lesson_id = lesson["id"]
            if lesson_id in self._lessons:
                raise ValueError(f"duplicate lesson id: {lesson_id}")
            self._lessons[lesson_id] = lesson

    @staticmethod
    def _validate_lesson(lesson: dict[str, Any]) -> None:
        for field in ("id", "title", "description", "greeting", "task", "points", "sources"):
            if not lesson.get(field):
                raise ValueError(f"lesson is missing {field}")
        point_ids = [point["id"] for point in lesson["points"]]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError(f"duplicate point id in lesson {lesson['id']}")
        if sum(int(point.get("weight", 0)) for point in lesson["points"]) != 100:
            raise ValueError(f"point weights must total 100 in lesson {lesson['id']}")
        source_ids = {source["id"] for source in lesson["sources"]}
        unknown = {
            source_id
            for point in lesson["points"]
            for source_id in point.get("source_ids", [])
            if source_id not in source_ids
        }
        if unknown:
            raise ValueError(f"unknown sources in lesson {lesson['id']}: {sorted(unknown)}")

    @property
    def default_lesson_id(self) -> str:
        return next(iter(self._lessons))

    def get(self, lesson_id: str) -> dict[str, Any]:
        try:
            return self._lessons[lesson_id]
        except KeyError as exc:
            raise KeyError(f"unknown lesson: {lesson_id}") from exc

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_lesson(lesson) for lesson in self._lessons.values()]

    def all(self) -> list[dict[str, Any]]:
        return list(self._lessons.values())

    @staticmethod
    def public_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": lesson["id"],
            "track": lesson.get("track", "Course"),
            "title": lesson["title"],
            "description": lesson["description"],
            "greeting": lesson["greeting"],
            "task": lesson["task"],
            "starter_prompts": lesson.get("starter_prompts", []),
            "points": [
                {"id": point["id"], "label": point["label"], "weight": point["weight"]}
                for point in lesson["points"]
            ],
        }


class ConversationStore:
    """Application-owned session and message history.

    LangGraph checkpoints are execution details.  These tables are the durable
    product record used by the session list and transcript API.
    """

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._setup()

    def _setup(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS learning_sessions (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                progress INTEGER NOT NULL DEFAULT 0,
                covered_points TEXT NOT NULL DEFAULT '[]',
                misconceptions TEXT NOT NULL DEFAULT '[]',
                provider TEXT NOT NULL DEFAULT 'offline',
                model TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS learning_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES learning_sessions(id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_learning_sessions_updated ON learning_sessions(updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_learning_sessions_lesson_updated ON learning_sessions(lesson_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_learning_messages_session_id ON learning_messages(session_id, id)",
        )
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            for statement in statements:
                self._conn.execute(statement)
            self._conn.execute("PRAGMA optimize")
            self._conn.commit()

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "lesson_id": row["lesson_id"],
            "title": row["title"],
            "status": row["status"],
            "progress": row["progress"],
            "covered_points": json.loads(row["covered_points"]),
            "misconceptions": json.loads(row["misconceptions"]),
            "provider": row["provider"],
            "model": row["model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "metadata": json.loads(row["metadata"]),
            "created_at": row["created_at"],
        }

    def create_session(
        self,
        lesson: dict[str, Any],
        session_id: str | None = None,
        *,
        title: str | None = None,
        include_greeting: bool = True,
    ) -> dict[str, Any]:
        session_id = session_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", session_id):
            raise ValueError("session_id không hợp lệ")
        now = utc_now()
        title = (title or lesson["title"]).strip()[:120]
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO learning_sessions
                    (id, lesson_id, title, status, progress, covered_points,
                     misconceptions, provider, model, created_at, updated_at)
                VALUES (?, ?, ?, 'new', 0, '[]', '[]', 'offline', NULL, ?, ?)
                """,
                (session_id, lesson["id"], title, now, now),
            )
            if include_greeting:
                self._conn.execute(
                    """
                    INSERT INTO learning_messages(session_id, role, content, metadata, created_at)
                    VALUES (?, 'assistant', ?, ?, ?)
                    """,
                    (session_id, lesson["greeting"], json.dumps({"kind": "greeting"}), now),
                )
            self._conn.commit()
        return self.get_session(session_id, include_messages=True)  # type: ignore[return-value]

    def get_session(self, session_id: str, *, include_messages: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM learning_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            result = self._session_from_row(row)
            if include_messages:
                rows = self._conn.execute(
                    "SELECT * FROM learning_messages WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
                result["messages"] = [self._message_from_row(item) for item in rows]
            return result

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.*,
                       (SELECT content FROM learning_messages m
                        WHERE m.session_id = s.id ORDER BY m.id DESC LIMIT 1) AS preview
                FROM learning_sessions s
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        result = []
        for row in rows:
            session = self._session_from_row(row)
            session["preview"] = row["preview"] or ""
            result.append(session)
        return result

    def clear_sessions(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM learning_sessions").fetchone()
            count = int(row["count"])
            self._conn.execute("DELETE FROM learning_sessions")
            self._conn.commit()
        return count

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM learning_sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def recent_messages(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM learning_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id
                """,
                (session_id, max(1, min(limit, 20))),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def add_message(
        self, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO learning_messages(session_id, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            self._conn.execute(
                "UPDATE learning_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )
            self._conn.commit()
            message_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM learning_messages WHERE id = ?", (message_id,)
            ).fetchone()
        return self._message_from_row(row)

    def update_session(
        self,
        session_id: str,
        *,
        status: str,
        progress: int,
        covered_points: list[str],
        misconceptions: list[str],
        provider: str,
        model: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE learning_sessions
                SET status = ?, progress = ?, covered_points = ?, misconceptions = ?,
                    provider = ?, model = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    progress,
                    json.dumps(covered_points),
                    json.dumps(misconceptions, ensure_ascii=False),
                    provider,
                    model,
                    utc_now(),
                    session_id,
                ),
            )
            self._conn.commit()


class OpenAITurnAssessor:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = 90) -> None:
        if not api_key.strip():
            raise ValueError("Cần nhập API key khi chọn OpenAI")
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

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
            raise RuntimeError("OpenAI không trả về nội dung có thể đọc")
        return "\n".join(texts)

    def _request_structured(
        self,
        *,
        instructions: str,
        input_payload: dict[str, Any],
        schema: dict[str, Any],
        schema_name: str,
        max_output_tokens: int = 1000,
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": json.dumps(input_payload, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "vinuni-teachback-studio/2.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(error_text).get("error", {}).get("message", "")
            except json.JSONDecodeError:
                message = ""
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {message or 'request failed'}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Không kết nối được OpenAI API: {exc.reason}") from exc
        return json.loads(self._extract_output_text(raw))

    def route_lesson(self, lessons: list[dict[str, Any]], content: str) -> dict[str, Any]:
        lesson_ids = [lesson["id"] for lesson in lessons]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "matched": {"type": "boolean"},
                "lesson_id": {"type": "string", "enum": lesson_ids},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["matched", "lesson_id", "confidence", "reason"],
        }
        candidates = [
            {
                "id": lesson["id"],
                "title": lesson["title"],
                "description": lesson["description"],
                "task": lesson["task"],
                "knowledge_points": [point["label"] for point in lesson["points"]],
            }
            for lesson in lessons
        ]
        parsed = self._request_structured(
            instructions=(
                "Bạn là router cho một chatbot ôn tập. Chọn đúng một bài học phù hợp nhất với "
                "ý định và nội dung người dùng. matched=false nếu người dùng chỉ nói chung chung "
                "rằng muốn ôn tập nhưng chưa nêu chủ đề, hoặc nội dung không thuộc bất kỳ bài nào. "
                "Không đánh giá kiến thức ở bước này."
            ),
            input_payload={"available_lessons": candidates, "user_prompt": content},
            schema=schema,
            schema_name="teachback_lesson_router",
            max_output_tokens=300,
        )
        if parsed.get("lesson_id") not in lesson_ids:
            parsed["lesson_id"] = lesson_ids[0]
            parsed["matched"] = False
        return parsed

    def assess(
        self,
        lesson: dict[str, Any],
        content: str,
        covered_points: list[str],
        recent_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        point_ids = [point["id"] for point in lesson["points"]]
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "supported_points": {
                    "type": "array",
                    "items": {"type": "string", "enum": point_ids},
                },
                "out_of_scope": {"type": "boolean"},
                "insufficient": {"type": "boolean"},
                "misconceptions": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "feedback": {"type": "string"},
                "next_question": {"type": "string"},
                "intent": {
                    "type": "string",
                    "enum": [
                        "teachback_answer",
                        "learning_request",
                        "clarification_question",
                        "social",
                        "unrelated",
                    ],
                },
            },
            "required": [
                "supported_points",
                "out_of_scope",
                "insufficient",
                "misconceptions",
                "confidence",
                "feedback",
                "next_question",
                "intent",
            ],
        }
        rubric = [
            {
                "id": point["id"],
                "label": point["label"],
                "ground_truth": point["ground_truth"],
                "accepted_signals": point["signals"],
            }
            for point in lesson["points"]
        ]
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in recent_messages[-8:]
            if item["role"] in {"user", "assistant"}
        ]
        input_payload = {
            "lesson": {"title": lesson["title"], "task": lesson["task"]},
            "rubric": rubric,
            "already_covered": covered_points,
            "recent_conversation": history,
            "learner_message": content,
        }
        parsed = self._request_structured(
            instructions=(
                "Bạn là một học viên AI thân thiện trong phiên teach-back tiếng Việt. Trước hết "
                "hãy phân loại ý định của learner_message. learning_request gồm yêu cầu bắt đầu "
                "học, ôn lại, thiết lập vai trò hoặc nhờ bạn làm học viên. clarification_question "
                "là khi người dùng hỏi khái niệm, xin ví dụ hay xin gợi ý. social là chào hỏi. "
                "teachback_answer chỉ dùng khi người dùng thật sự đang giải thích kiến thức. "
                "unrelated chỉ dùng cho chủ đề rõ ràng không liên quan tới bất kỳ hoạt động học "
                "nào trong bài. Việc nói chưa biết, quên bài, muốn học lại, dùng từ đồng nghĩa, "
                "hoặc đặt câu hỏi không bao giờ là out_of_scope. Chỉ chấm rubric và công nhận "
                "supported_points cho teachback_answer có bằng chứng ngữ nghĩa rõ; ý bị thiếu "
                "không phải là hiểu sai. Với các intent còn lại, giữ supported_points rỗng và "
                "trả lời trực tiếp, ấm áp trong feedback. next_question là một câu hỏi tự nhiên "
                "giúp cuộc trò chuyện tiến lên. Không lặp lại máy móc tên bài hay câu task."
            ),
            input_payload=input_payload,
            schema=schema,
            schema_name="multi_lesson_teachback_turn",
        )
        parsed["supported_points"] = list(
            dict.fromkeys(
                point_id
                for point_id in parsed.get("supported_points", [])
                if point_id in point_ids
            )
        )
        parsed["misconceptions"] = [str(item)[:240] for item in parsed.get("misconceptions", [])][:3]
        parsed["intent"] = str(parsed.get("intent") or "teachback_answer")
        if parsed["intent"] != "teachback_answer":
            parsed["supported_points"] = []
            parsed["misconceptions"] = []
            parsed["out_of_scope"] = parsed["intent"] == "unrelated"
        return parsed


class TeachBackPlatform:
    OUTSIDE_PATTERNS = (
        r"\b(thoi tiet|du bao thoi tiet|bong da|nau an|mon an|bai tho|viet tho|viet cv|"
        r"chung khoan|gia bitcoin|du lich|dat phong|am nhac|phim anh)\b",
        r"\b(ai|chatgpt|model)\b.{0,35}\b(viet cv|lam tho|dat ve|dat phong|du bao thoi tiet)\b",
    )
    HELP_PATTERNS = (
        r"\b(khong biet|chua biet|khong hieu|chua hieu|khong nho|quen het|mat goc|bi roi|hoc tu dau|xem o dau|bat dau tu dau)\b",
    )
    LEARNING_REQUEST_PATTERNS = (
        r"\b(muon|can|giup|nho|hay|co the)\b.{0,45}\b(hoc|on|day|giai thich|lam hoc vien|dong vai)\b",
        r"\b(hoc|on)\s+(lai|ve|phan|chu de)\b",
        r"\b(lam hoc vien|dong vai hoc vien|bat dau hoc|bat dau on)\b",
    )
    QUESTION_PATTERNS = (
        r"\b(la gi|tai sao|vi sao|nhu the nao|the nao|khi nao|o dau|khac gi|co dung|vi du|goi y)\b",
    )
    SOCIAL_PATTERNS = (r"^(xin chao|chao|hello|hi|cam on|thanks)(\b|[!. ])",)
    ROUTING_STOP_WORDS = {
        "ai", "ban", "bai", "biet", "cach", "can", "cho", "co", "cua", "dau", "day",
        "duoc", "gi", "giup", "hieu", "hoc", "khi", "khong", "la", "lai", "lam", "minh", "mot",
        "muon", "nhu", "noi", "on", "phan", "tap", "the", "thi", "toi", "trong", "va", "ve",
        "voi", "xem",
    }

    def __init__(
        self,
        catalog: LessonCatalog | None = None,
        store: ConversationStore | None = None,
    ) -> None:
        self.catalog = catalog or LessonCatalog()
        self.store = store or ConversationStore()

    def create_session(self, lesson_id: str, session_id: str | None = None) -> dict[str, Any]:
        lesson = self.catalog.get(lesson_id)
        return self._decorate_session(self.store.create_session(lesson, session_id), lesson)

    def start_chat(
        self,
        content: str,
        *,
        provider: str = "offline",
        model: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Route a free-form first prompt, then create and run a lesson session."""
        content = str(content).strip()
        if not content:
            raise ValueError("Nội dung tin nhắn không được để trống")
        if len(content) > 12_000:
            raise ValueError("Tin nhắn vượt quá 12.000 ký tự")
        provider = provider.strip().lower()
        if provider not in {"offline", "openai"}:
            raise ValueError("provider phải là offline hoặc openai")
        selected_model = (model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()
        lessons = self.catalog.all()
        if provider == "openai":
            route = OpenAITurnAssessor(
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                model=selected_model,
            ).route_lesson(lessons, content)
        else:
            route = self._offline_route_lesson(content)

        if not route["matched"]:
            intent = self._classify_turn_intent(content)
            if intent == "help":
                clarification = (
                    "Không sao — mình có thể cùng bạn bắt đầu lại từ đầu. Mình chỉ cần biết "
                    "bạn đang muốn ôn phần nào. Bạn nhớ một từ khóa, ví dụ hoặc tình huống nào "
                    "liên quan đến phần đó không?"
                )
            elif intent in {"learning_request", "social"}:
                clarification = (
                    "Được chứ, mình sẵn sàng làm học viên để bạn dạy lại. Bạn muốn bắt đầu "
                    "với chủ đề nào? Chỉ cần mô tả bằng lời của bạn, không cần dùng đúng tên bài."
                )
            else:
                clarification = (
                    "Mình chưa nhận ra chủ đề bạn đang nhắc tới. Bạn nói thêm một chút về "
                    "khái niệm, ví dụ hoặc vấn đề bạn muốn ôn nhé."
                )
            return {
                "needs_clarification": True,
                "session_id": None,
                "status": "needs_topic",
                "intent": intent,
                "agent_response": clarification,
                "suggested_lessons": self.catalog.list_public(),
                "tool_trace": [
                    {
                        "tool": "route_lesson",
                        "output": {**route, "intent": intent, "created_session": False},
                    }
                ],
            }

        lesson = self.catalog.get(route["lesson_id"])
        session = self.store.create_session(
            lesson,
            title=self._chat_title(content),
            include_greeting=False,
        )
        try:
            result = self.send_message(
                session["id"],
                content,
                provider=provider,
                model=selected_model,
                api_key=api_key,
                allow_reroute=False,
            )
        except Exception:
            # Routing may succeed while the model call fails. Do not leave an
            # empty or half-created conversation in the user's history.
            self.store.delete_session(session["id"])
            raise
        result["needs_clarification"] = False
        result["routed_lesson"] = self.catalog.public_lesson(lesson)
        result["tool_trace"].insert(
            0,
            {
                "tool": "route_lesson",
                "output": {**route, "created_session": True},
            },
        )
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
            lesson = self.catalog.get(session["lesson_id"])
            session["lesson_title"] = lesson["title"]
        return sessions

    def clear_history(self) -> int:
        return self.store.clear_sessions()

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
        intent = self._classify_turn_intent(content)
        assessor: OpenAITurnAssessor | None = None
        if provider == "openai":
            assessor = OpenAITurnAssessor(
                api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
                model=selected_model or DEFAULT_MODEL,
            )

        if allow_reroute and intent != "social":
            if provider == "openai" and assessor is not None:
                route = assessor.route_lesson(self.catalog.all(), content)
            else:
                route = self._offline_route_lesson(content)
            if (
                route["matched"]
                and route["lesson_id"] != lesson["id"]
                and float(route.get("confidence", 0)) >= 0.55
            ):
                switched = self.start_chat(
                    content,
                    provider=provider,
                    model=selected_model,
                    api_key=api_key,
                )
                switched["session_switched"] = True
                return switched

        if provider == "offline" and intent != "teachback_answer":
            return self._respond_without_assessment(
                session,
                lesson,
                content,
                intent=intent,
                provider=provider,
                model=None,
            )

        recent = self.store.recent_messages(session_id)
        self.store.add_message(session_id, "user", content)
        if provider == "openai":
            assessment = assessor.assess(  # type: ignore[union-attr]
                lesson, content, session["covered_points"], recent
            )
        else:
            assessment = self._offline_assessment(lesson, content)

        assessed_intent = str(assessment.get("intent") or "teachback_answer")
        if assessed_intent != "teachback_answer":
            return self._respond_without_assessment(
                session,
                lesson,
                content,
                intent=assessed_intent,
                provider=provider,
                model=selected_model,
                assessment=assessment,
                user_already_saved=True,
            )

        current_covered = set(session["covered_points"])
        if not assessment["out_of_scope"] and not assessment["insufficient"]:
            current_covered.update(assessment["supported_points"])
        point_order = [point["id"] for point in lesson["points"]]
        covered = [point_id for point_id in point_order if point_id in current_covered]
        missing = [point_id for point_id in point_order if point_id not in current_covered]
        progress = sum(
            int(point["weight"]) for point in lesson["points"] if point["id"] in current_covered
        )

        if assessment["out_of_scope"]:
            status = "out_of_scope"
            response_text = (
                f"Mình hiểu ý bạn, nhưng phần này chưa có trong knowledge hiện tại nên mình "
                f"không muốn trả lời bằng cách đoán. {assessment.get('feedback', '').strip()} "
                f"Nếu muốn, mình có thể tiếp tục phần “{lesson['title']}” hoặc bạn mở một cuộc "
                "trò chuyện mới cho chủ đề khác."
            ).strip()
            target_point = None
        elif assessment["insufficient"]:
            status = "needs_recovery"
            target_point = self._point_by_id(lesson, missing[0] if missing else point_order[-1])
            response_text = (
                "Mình chưa bắt được ý bạn muốn giải thích — không sao, ta thu hẹp lại nhé. "
                f"{target_point['recovery']} {target_point['question']}"
            )
        elif assessment["misconceptions"]:
            status = "misconception"
            target_point = self._point_by_id(lesson, missing[0] if missing else point_order[-1])
            response_text = self._compose_response(assessment, target_point["question"])
        elif not missing:
            status = "mastered"
            target_point = None
            response_text = (
                assessment.get("feedback", "").strip()
                or "Bạn đã kết nối đủ các ý cốt lõi bằng lời của mình."
            )
            response_text += " Bạn đã hoàn thành bài teach-back này."
        else:
            status = "partial"
            target_point = self._point_by_id(lesson, missing[0])
            response_text = self._compose_response(assessment, target_point["question"])

        source_cards = self._sources_for_turn(lesson, assessment["supported_points"], target_point)
        metadata = {
            "status": status,
            "progress": progress,
            "covered_points": covered,
            "supported_points_this_turn": assessment["supported_points"],
            "misconceptions": assessment["misconceptions"],
            "source_cards": source_cards,
            "provider": provider,
            "model": selected_model,
            "confidence": assessment.get("confidence", 0.0),
            "intent": assessment.get("intent", "teachback_answer"),
        }
        assistant_message = self.store.add_message(
            session_id, "assistant", response_text, metadata
        )
        self.store.update_session(
            session_id,
            status=status,
            progress=progress,
            covered_points=covered,
            misconceptions=assessment["misconceptions"],
            provider=provider,
            model=selected_model,
        )
        tool_trace: list[dict[str, Any]] = []
        if source_cards:
            tool_trace.append(
                {
                    "tool": "retrieve_evidence",
                    "input": {"lesson_id": lesson["id"], "point_id": target_point["id"] if target_point else None},
                    "output_source_ids": [source["id"] for source in source_cards],
                }
            )
        tool_trace.append(
            {
                "tool": "save_session",
                "input": {"session_id": session_id},
                "output": {"status": status, "progress": progress},
            }
        )
        return {
            "session_id": session_id,
            "lesson_id": lesson["id"],
            "status": status,
            "intent": assessment.get("intent", "teachback_answer"),
            "progress": progress,
            "covered_points": covered,
            "missing_points": missing,
            "misconceptions": assessment["misconceptions"],
            "agent_response": response_text,
            "source_cards": source_cards,
            "provider": provider,
            "model": selected_model,
            "message": assistant_message,
            "tool_trace": tool_trace,
        }

    @staticmethod
    def _chat_title(content: str) -> str:
        compact = re.sub(r"\s+", " ", content).strip()
        return compact if len(compact) <= 58 else compact[:57].rstrip() + "…"

    def _classify_turn_intent(self, content: str) -> str:
        """Classify conversational intent before deciding whether to grade it."""
        text = normalize_text(content)
        if any(re.search(pattern, text) for pattern in self.OUTSIDE_PATTERNS):
            return "unrelated"
        if any(re.search(pattern, text) for pattern in self.HELP_PATTERNS):
            return "help"
        if any(re.search(pattern, text) for pattern in self.LEARNING_REQUEST_PATTERNS):
            return "learning_request"
        if any(re.search(pattern, text) for pattern in self.QUESTION_PATTERNS) or "?" in content:
            return "clarification_question"
        if any(re.search(pattern, text) for pattern in self.SOCIAL_PATTERNS):
            return "social"
        return "teachback_answer"

    def _meaningful_tokens(self, value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", normalize_text(value))
            if len(token) >= 3 and token not in self.ROUTING_STOP_WORDS
        }

    def _best_point_for_text(
        self,
        lesson: dict[str, Any],
        content: str,
        covered_points: list[str],
    ) -> dict[str, Any]:
        text = normalize_text(content)
        prompt_tokens = self._meaningful_tokens(content)
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for index, point in enumerate(lesson["points"]):
            score = 0
            for signal in point.get("signals", []):
                normalized = normalize_text(signal)
                if contains_phrase(text, normalized):
                    score += 5
            corpus = " ".join(
                [point["label"], point["ground_truth"], point["question"], point["recovery"]]
            )
            score += len(prompt_tokens & self._meaningful_tokens(corpus))
            if point["id"] not in covered_points:
                score += 1
            ranked.append((score, -index, point))
        return max(ranked, key=lambda item: (item[0], item[1]))[2]

    def _respond_without_assessment(
        self,
        session: dict[str, Any],
        lesson: dict[str, Any],
        content: str,
        *,
        intent: str,
        provider: str,
        model: str | None,
        assessment: dict[str, Any] | None = None,
        user_already_saved: bool = False,
    ) -> dict[str, Any]:
        """Respond to conversation control/help turns without changing mastery."""
        if not user_already_saved:
            self.store.add_message(session["id"], "user", content)

        covered = list(session["covered_points"])
        missing = [
            point["id"] for point in lesson["points"] if point["id"] not in covered
        ]
        target_point = self._best_point_for_text(lesson, content, covered)
        model_feedback = str((assessment or {}).get("feedback", "")).strip()
        model_question = str((assessment or {}).get("next_question", "")).strip()

        if model_feedback:
            response_text = self._compose_response(
                {"feedback": model_feedback, "next_question": model_question},
                target_point["question"],
            )
        elif intent == "help":
            response_text = (
                "Không sao, mình sẽ đi cùng bạn từ phần dễ nhất. "
                f"Gợi ý đầu tiên: {target_point['recovery']} "
                f"Bạn thử nói lại theo cách hiểu của mình nhé: {target_point['question']}"
            )
        elif intent == "learning_request":
            response_text = (
                f"Được chứ. Mình đã nhận ra bạn muốn ôn “{lesson['title']}”. "
                "Mình sẽ làm một học viên tò mò: bạn giải thích từng ý, còn mình sẽ hỏi tiếp "
                "và chỉ gợi ý khi cần. "
                f"Bắt đầu nhẹ nhé: {target_point['question']}"
            )
        elif intent == "clarification_question":
            response_text = (
                f"Có thể hiểu ngắn gọn như sau: {target_point['ground_truth']} "
                f"Để kiểm tra xem cách hiểu đã chắc chưa, {target_point['question'].lower()}"
            )
        elif intent == "social":
            response_text = (
                f"Chào bạn! Mình đang ở đây để cùng bạn ôn “{lesson['title']}”. "
                f"Khi sẵn sàng, mình bắt đầu bằng câu này nhé: {target_point['question']}"
            )
        else:
            response_text = (
                "Mình hiểu bạn đang muốn hỏi sang một nội dung khác. Phần đó chưa có trong "
                f"knowledge hiện tại nên mình không muốn đoán. Ta có thể tiếp tục “{lesson['title']}”, "
                "hoặc bạn mở cuộc trò chuyện mới và mô tả chủ đề muốn học."
            )

        status = "out_of_scope" if intent == "unrelated" else (
            session["status"] if session["status"] != "new" else "coaching"
        )
        source_cards = (
            []
            if intent in {"social", "unrelated"}
            else self._sources_for_turn(lesson, [], target_point)
        )
        metadata = {
            "status": status,
            "intent": intent,
            "progress": session["progress"],
            "covered_points": covered,
            "supported_points_this_turn": [],
            "misconceptions": session["misconceptions"],
            "source_cards": source_cards,
            "provider": provider,
            "model": model,
            "confidence": (assessment or {}).get("confidence", 1.0),
        }
        assistant_message = self.store.add_message(
            session["id"], "assistant", response_text, metadata
        )
        self.store.update_session(
            session["id"],
            status=status,
            progress=session["progress"],
            covered_points=covered,
            misconceptions=session["misconceptions"],
            provider=provider,
            model=model,
        )
        tool_trace: list[dict[str, Any]] = [
            {"tool": "classify_intent", "output": {"intent": intent, "graded": False}}
        ]
        if source_cards:
            tool_trace.append(
                {
                    "tool": "retrieve_evidence",
                    "input": {"lesson_id": lesson["id"], "point_id": target_point["id"]},
                    "output_source_ids": [source["id"] for source in source_cards],
                }
            )
        tool_trace.append(
            {
                "tool": "save_session",
                "input": {"session_id": session["id"]},
                "output": {"status": status, "progress": session["progress"]},
            }
        )
        return {
            "session_id": session["id"],
            "lesson_id": lesson["id"],
            "status": status,
            "intent": intent,
            "progress": session["progress"],
            "covered_points": covered,
            "missing_points": missing,
            "misconceptions": session["misconceptions"],
            "agent_response": response_text,
            "source_cards": source_cards,
            "provider": provider,
            "model": model,
            "message": assistant_message,
            "tool_trace": tool_trace,
        }

    def _offline_route_lesson(self, content: str) -> dict[str, Any]:
        text = normalize_text(content)
        prompt_tokens = self._meaningful_tokens(content)
        scored: list[tuple[int, str]] = []
        for lesson in self.catalog.all():
            score = 0
            for term in lesson.get("scope_terms", []):
                normalized = normalize_text(term)
                if contains_phrase(text, normalized):
                    score += 2 + min(len(normalized.split()), 3)
            for point in lesson["points"]:
                label = normalize_text(point["label"])
                if contains_phrase(text, label):
                    score += 4
                for signal in point.get("signals", []):
                    normalized = normalize_text(signal)
                    if contains_phrase(text, normalized):
                        score += 3
            semantic_corpus = " ".join(
                [lesson["title"], lesson["description"], lesson["task"]]
                + [point["label"] + " " + point["ground_truth"] for point in lesson["points"]]
            )
            score += min(len(prompt_tokens & self._meaningful_tokens(semantic_corpus)), 4)
            scored.append((score, lesson["id"]))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, lesson_id = scored[0]
        return {
            "matched": best_score > 0,
            "lesson_id": lesson_id,
            "confidence": min(0.95, 0.35 + best_score * 0.05) if best_score else 0.0,
            "reason": "keyword_and_rubric_match" if best_score else "no_lesson_signal",
        }

    @staticmethod
    def _point_by_id(lesson: dict[str, Any], point_id: str) -> dict[str, Any]:
        return next(point for point in lesson["points"] if point["id"] == point_id)

    @staticmethod
    def _compose_response(assessment: dict[str, Any], fallback_question: str) -> str:
        feedback = str(assessment.get("feedback", "")).strip()
        question = str(assessment.get("next_question", "")).strip() or fallback_question
        if feedback and question and question not in feedback:
            return f"{feedback} {question}".strip()
        return feedback or question

    def _offline_assessment(self, lesson: dict[str, Any], content: str) -> dict[str, Any]:
        text = normalize_text(content)
        word_count = len(text.split())
        supported = []
        for point in lesson["points"]:
            normalized_signals = [normalize_text(signal) for signal in point.get("signals", [])]
            if any(contains_phrase(text, signal) for signal in normalized_signals):
                supported.append(point["id"])
        explicit_outside = any(re.search(pattern, text) for pattern in self.OUTSIDE_PATTERNS)
        out_of_scope = bool(explicit_outside and not supported)
        insufficient = bool(
            not out_of_scope
            and (
                word_count <= 3
                or text in {"khong biet", "chua ro", "khong nho"}
                or not supported
            )
        )
        first_missing = next(
            (point for point in lesson["points"] if point["id"] not in supported), None
        )
        feedback = ""
        next_question = ""
        if out_of_scope:
            feedback = "Mình chưa thấy nội dung liên quan tới mục tiêu của bài học."
        elif insufficient:
            feedback = "Câu trả lời còn quá ngắn để đối chiếu với rubric."
        elif supported:
            labels = [
                point["label"] for point in lesson["points"] if point["id"] in supported
            ]
            feedback = "Mình đã ghi nhận: " + ", ".join(labels) + "."
        else:
            feedback = "Mình chưa tìm thấy bằng chứng đủ rõ cho một ý trong rubric."
        if first_missing:
            next_question = first_missing["question"]
        return {
            "supported_points": supported,
            "out_of_scope": out_of_scope,
            "insufficient": insufficient,
            "misconceptions": [],
            "confidence": 0.55,
            "feedback": feedback,
            "next_question": next_question,
            "intent": "unrelated" if out_of_scope else "teachback_answer",
        }

    def _sources_for_turn(
        self,
        lesson: dict[str, Any],
        supported_points: list[str],
        target_point: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        source_ids: list[str] = []
        for point in lesson["points"]:
            if point["id"] in supported_points:
                source_ids.extend(point.get("source_ids", [])[:1])
        if not source_ids and target_point:
            source_ids.extend(target_point.get("source_ids", [])[:2])
        source_map = {source["id"]: source for source in lesson["sources"]}
        unique_ids = list(dict.fromkeys(source_ids))[:3]
        return [source_map[source_id] for source_id in unique_ids if source_id in source_map]

    def _decorate_session(
        self, session: dict[str, Any], lesson: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(session)
        result["lesson"] = self.catalog.public_lesson(lesson)
        return result


def runtime_info() -> dict[str, Any]:
    return {
        "providers": ["offline", "openai"],
        "default_provider": "openai" if os.getenv("OPENAI_API_KEY") else "offline",
        "default_model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        "server_has_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "api_key_policy": "API key gửi theo request và không được lưu vào session, message hoặc log.",
    }
