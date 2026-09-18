"""Infrastructure shared by the web lesson engine and HTTP server.

This module deliberately contains no teaching or grading policy.  Lesson
decisions live in :mod:`agent_core` and :mod:`agent_graph`; this file only
loads the lesson catalog, persists conversations, and routes a new chat to a
lesson.
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

from agent_core import INTENTS


CODEBASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODEBASE_DIR.parent


def _load_local_env() -> None:
    """Load local development settings without overriding real environment variables."""
    env_file = CODEBASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_local_env()
DEFAULT_CATALOG_PATH = REPO_ROOT / "knowledge" / "lesson_catalog.json"
DEFAULT_DB_PATH = CODEBASE_DIR / "state" / "learning.sqlite3"
TRANSCRIPT_DIR = REPO_ROOT / "data" / "transcript"
DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_MODEL_LOG_PATH = CODEBASE_DIR / "logs" / "model_calls.jsonl"
MODEL_LOG_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.lower())
    ascii_text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", ascii_text.replace("đ", "d")).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text))


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

    def all(self) -> list[dict[str, Any]]:
        return list(self._lessons.values())

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_lesson(lesson) for lesson in self._lessons.values()]

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
                {
                    "id": point["id"], "label": point["label"], "weight": point["weight"],
                    "sub_concepts": point.get("sub_concepts", []),
                }
                for point in lesson["points"]
            ],
        }


class ConversationStore:
    """Durable product history, separate from agent execution state."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_sessions (
                    id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', progress INTEGER NOT NULL DEFAULT 0,
                    covered_points TEXT NOT NULL DEFAULT '[]', misconceptions TEXT NOT NULL DEFAULT '[]',
                    attempts_by_gap TEXT NOT NULL DEFAULT '{}', last_target_gap TEXT,
                    recovery_stage TEXT NOT NULL DEFAULT 'none', turn INTEGER NOT NULL DEFAULT 0,
                    awaiting_transfer INTEGER NOT NULL DEFAULT 0,
                    transfer_passed INTEGER NOT NULL DEFAULT 0,
                    mastery_complete INTEGER NOT NULL DEFAULT 0,
                    provider TEXT NOT NULL DEFAULT 'offline', model TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES learning_sessions(id) ON DELETE CASCADE
                )
                """
            )
            existing = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(learning_sessions)").fetchall()
            }
            migrations = {
                "attempts_by_gap": "ALTER TABLE learning_sessions ADD COLUMN attempts_by_gap TEXT NOT NULL DEFAULT '{}'",
                "last_target_gap": "ALTER TABLE learning_sessions ADD COLUMN last_target_gap TEXT",
                "recovery_stage": "ALTER TABLE learning_sessions ADD COLUMN recovery_stage TEXT NOT NULL DEFAULT 'none'",
                "turn": "ALTER TABLE learning_sessions ADD COLUMN turn INTEGER NOT NULL DEFAULT 0",
                "awaiting_transfer": "ALTER TABLE learning_sessions ADD COLUMN awaiting_transfer INTEGER NOT NULL DEFAULT 0",
                "transfer_passed": "ALTER TABLE learning_sessions ADD COLUMN transfer_passed INTEGER NOT NULL DEFAULT 0",
                "mastery_complete": "ALTER TABLE learning_sessions ADD COLUMN mastery_complete INTEGER NOT NULL DEFAULT 0",
            }
            for name, statement in migrations.items():
                if name not in existing:
                    self._conn.execute(statement)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_sessions_updated ON learning_sessions(updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_messages_session_id ON learning_messages(session_id, id)"
            )
            self._conn.commit()

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "lesson_id": row["lesson_id"], "title": row["title"],
            "status": row["status"], "progress": row["progress"],
            "covered_points": json.loads(row["covered_points"]),
            "misconceptions": json.loads(row["misconceptions"]),
            "attempts_by_gap": json.loads(row["attempts_by_gap"]),
            "last_target_gap": row["last_target_gap"], "recovery_stage": row["recovery_stage"],
            "turn": row["turn"], "awaiting_transfer": bool(row["awaiting_transfer"]),
            "transfer_passed": bool(row["transfer_passed"]),
            "mastery_complete": bool(row["mastery_complete"]),
            "provider": row["provider"], "model": row["model"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "role": row["role"], "content": row["content"],
                "metadata": json.loads(row["metadata"]), "created_at": row["created_at"]}

    def create_session(self, lesson: dict[str, Any], session_id: str | None = None, *,
                       title: str | None = None, include_greeting: bool = True) -> dict[str, Any]:
        session_id = session_id or str(uuid.uuid4())
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", session_id):
            raise ValueError("session_id không hợp lệ")
        now = utc_now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO learning_sessions
                   (id, lesson_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)""",
                (session_id, lesson["id"], (title or lesson["title"]).strip()[:120], now, now),
            )
            if include_greeting:
                self._conn.execute(
                    """INSERT INTO learning_messages(session_id, role, content, metadata, created_at)
                       VALUES (?, 'assistant', ?, ?, ?)""",
                    (session_id, lesson["greeting"], json.dumps({"kind": "greeting"}), now),
                )
            self._conn.commit()
        return self.get_session(session_id, include_messages=True)  # type: ignore[return-value]

    def get_session(self, session_id: str, *, include_messages: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM learning_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            result = self._session_from_row(row)
            if include_messages:
                rows = self._conn.execute(
                    "SELECT * FROM learning_messages WHERE session_id = ? ORDER BY id", (session_id,)
                ).fetchall()
                result["messages"] = [self._message_from_row(item) for item in rows]
            return result

    def list_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT s.*, (SELECT content FROM learning_messages m WHERE m.session_id = s.id
                   ORDER BY m.id DESC LIMIT 1) AS preview FROM learning_sessions s
                   ORDER BY s.updated_at DESC LIMIT ?""", (safe_limit,),
            ).fetchall()
        result = []
        for row in rows:
            session = self._session_from_row(row)
            session["preview"] = row["preview"] or ""
            result.append(session)
        return result

    def clear_sessions(self) -> int:
        with self._lock:
            count = int(self._conn.execute("SELECT COUNT(*) FROM learning_sessions").fetchone()[0])
            self._conn.execute("DELETE FROM learning_sessions")
            self._conn.commit()
        return count

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM learning_sessions WHERE id = ?", (session_id,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def recent_messages(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM (SELECT * FROM learning_messages WHERE session_id = ?
                   ORDER BY id DESC LIMIT ?) ORDER BY id""", (session_id, max(1, min(limit, 20))),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def add_message(self, session_id: str, role: str, content: str,
                    metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO learning_messages(session_id, role, content, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            self._conn.execute("UPDATE learning_sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM learning_messages WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._message_from_row(row)

    def update_session(self, session_id: str, *, status: str, progress: int,
                       covered_points: list[str], misconceptions: list[str], provider: str,
                       model: str | None, attempts_by_gap: dict[str, int] | None = None,
                       last_target_gap: str | None = None, recovery_stage: str | None = None,
                       turn: int | None = None, awaiting_transfer: bool | None = None,
                       transfer_passed: bool | None = None,
                       mastery_complete: bool | None = None) -> None:
        with self._lock:
            current = self._conn.execute("SELECT * FROM learning_sessions WHERE id = ?", (session_id,)).fetchone()
            if current is None:
                raise KeyError("session not found")
            self._conn.execute(
                """UPDATE learning_sessions SET status=?, progress=?, covered_points=?, misconceptions=?,
                   provider=?, model=?, attempts_by_gap=?, last_target_gap=?, recovery_stage=?, turn=?,
                   awaiting_transfer=?, transfer_passed=?, mastery_complete=?, updated_at=? WHERE id=?""",
                (status, progress, json.dumps(covered_points), json.dumps(misconceptions, ensure_ascii=False),
                 provider, model,
                 json.dumps(attempts_by_gap if attempts_by_gap is not None else json.loads(current["attempts_by_gap"])),
                 last_target_gap,
                 recovery_stage if recovery_stage is not None else current["recovery_stage"],
                 turn if turn is not None else current["turn"],
                 int(awaiting_transfer if awaiting_transfer is not None else current["awaiting_transfer"]),
                 int(transfer_passed if transfer_passed is not None else current["transfer_passed"]),
                 int(mastery_complete if mastery_complete is not None else current["mastery_complete"]),
                 utc_now(), session_id),
            )
            self._conn.commit()


class OpenAILessonRouter:
    """Semantic lesson router.  It never grades a learner answer."""

    def __init__(self, api_key: str, model: str, timeout_seconds: int = 90,
                 log_path: Path | str = DEFAULT_MODEL_LOG_PATH) -> None:
        if not api_key.strip():
            raise ValueError("Cần nhập API key khi chọn OpenAI")
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.log_path = Path(log_path)

    def _write_log(self, event: str, payload: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": utc_now(), "event": event, **payload}
        with MODEL_LOG_LOCK, self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _extract_output_text(raw: dict[str, Any]) -> str:
        if isinstance(raw.get("output_text"), str):
            return raw["output_text"]
        texts = [content["text"] for item in raw.get("output", []) if item.get("type") == "message"
                 for content in item.get("content", [])
                 if content.get("type") == "output_text" and isinstance(content.get("text"), str)]
        if not texts:
            raise RuntimeError("OpenAI không trả về nội dung có thể đọc")
        return "\n".join(texts)

    def _request_structured(self, *, instructions: str, input_payload: dict[str, Any],
                            schema: dict[str, Any], schema_name: str,
                            max_output_tokens: int = 600) -> dict[str, Any]:
        body = {"model": self.model, "instructions": instructions,
                "input": json.dumps(input_payload, ensure_ascii=False),
                "text": {"format": {"type": "json_schema", "name": schema_name,
                                      "strict": True, "schema": schema}},
                "max_output_tokens": max_output_tokens, "store": False}
        request_id = str(uuid.uuid4())
        self._write_log("model_prompt", {"request_id": request_id, "provider": "openai",
                        "model": self.model, "schema_name": schema_name,
                        "instructions": instructions, "input": input_payload})
        request = urllib.request.Request(
            f"{self.base_url}/responses", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "User-Agent": "vinuni-teachback-studio/2.0"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            self._write_log("model_error", {"request_id": request_id, "status": exc.code,
                                             "raw_response": error_text})
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: request failed") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Không kết nối được OpenAI API: {exc.reason}") from exc
        self._write_log("model_raw_response", {"request_id": request_id, "provider": "openai",
                                                "model": self.model, "raw_response": raw})
        return json.loads(self._extract_output_text(raw))

    def route_lesson(self, lessons: list[dict[str, Any]], content: str) -> dict[str, Any]:
        lesson_ids = [lesson["id"] for lesson in lessons]
        schema = {"type": "object", "additionalProperties": False,
                  "properties": {
                      "matched": {"type": "boolean"},
                      "lesson_id": {"type": "string", "enum": lesson_ids},
                      "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                      "reason": {"type": "string"},
                      "intent": {"type": "string", "enum": list(INTENTS)}},
                  "required": ["matched", "lesson_id", "confidence", "reason", "intent"]}
        candidates = [{"id": lesson["id"], "title": lesson["title"],
                       "description": lesson["description"], "task": lesson["task"],
                       "knowledge_points": [point["label"] for point in lesson["points"]]}
                      for lesson in lessons]
        parsed = self._request_structured(
            instructions=(
                "Bạn định tuyến một chatbot ôn tập bằng tiếng Việt. Chọn bài phù hợp nhất và phân "
                "loại intent. Không viết phản hồi cho người dùng; chỉ trả dữ liệu định tuyến."
            ), input_payload={"available_lessons": candidates, "user_prompt": content},
            schema=schema, schema_name="teachback_lesson_router")
        if parsed.get("lesson_id") not in lesson_ids:
            parsed.update({"lesson_id": lesson_ids[0], "matched": False})
        if parsed.get("intent") not in INTENTS:
            parsed["intent"] = "teachback_answer" if parsed.get("matched") else "out_of_scope"
        return parsed

    def respond_to_unmatched_prompt(
        self, lessons: list[dict[str, Any]], content: str, intent: str, reason: str
    ) -> str:
        """Write the first-turn response after routing found no lesson.

        Kept separate from ``route_lesson`` so routing stays a structured
        classification call and every unmatched OpenAI chat has a dedicated
        response-generation call.
        """
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"reply": {"type": "string", "minLength": 1}},
            "required": ["reply"],
        }
        lesson_choices = [
            {"title": lesson["title"], "description": lesson["description"]}
            for lesson in lessons
        ]
        parsed = self._request_structured(
            instructions=(
                "Bạn là trợ lý của ứng dụng ôn tập AI bằng tiếng Việt. Viết đúng 1-2 câu ngắn "
                "để phản hồi lời mở đầu chưa khớp bài học nào. Nếu ngoài phạm vi, nói lịch sự rằng "
                "bạn chỉ hỗ trợ các bài ôn AI và mời chọn một bài liên quan; không trả lời nội dung "
                "ngoài phạm vi. Nếu người dùng muốn được giúp nhưng chưa nêu chủ đề, hỏi họ muốn ôn "
                "gì. Nếu yêu cầu mơ hồ, hỏi một chi tiết cần làm rõ. Phản hồi tự nhiên, cụ thể theo "
                "lời người dùng và không dùng câu khuôn."
            ),
            input_payload={
                "available_lessons": lesson_choices,
                "user_prompt": content,
                "intent": intent,
                "routing_reason": reason,
            },
            schema=schema,
            schema_name="teachback_unmatched_chat_reply",
            max_output_tokens=180,
        )
        reply = str(parsed.get("reply", "")).strip()
        if not reply:
            raise RuntimeError("OpenAI không tạo được phản hồi cho lời mở đầu")
        return reply

    def write_lesson_opening(self, lesson: dict[str, Any], learner_prompt: str) -> str:
        """Generate the conversational lead-in for a newly created lesson session."""
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"opening": {"type": "string", "minLength": 20}},
            "required": ["opening"],
        }
        parsed = self._request_structured(
            instructions=(
                "Bạn mở đầu một phiên ôn tập teach-back bằng tiếng Việt. Viết 2-4 câu ngắn, "
                "ấm áp và tự nhiên dựa trên bài học cùng lời mở đầu của người học. Hãy chào hoặc "
                "xác nhận phiên học, giải thích ngắn rằng người học sẽ trình bày theo cách hiểu của "
                "mình còn bạn sẽ đồng hành làm rõ. Tuyệt đối không đặt câu hỏi kiến thức, không yêu "
                "cầu giải thích một khái niệm cụ thể, không dùng dấu hỏi chấm và không chấm điểm."
            ),
            input_payload={
                "lesson_title": lesson["title"],
                "lesson_description": lesson["description"],
                "lesson_goal": lesson["task"],
                "learner_opening": learner_prompt,
            },
            schema=schema,
            schema_name="teachback_lesson_opening",
            max_output_tokens=220,
        )
        opening = str(parsed.get("opening", "")).strip()
        if not opening or "?" in opening:
            raise RuntimeError("OpenAI tạo lời mở đầu không hợp lệ")
        return opening


def runtime_info() -> dict[str, Any]:
    return {"providers": ["offline", "openai"],
            "default_provider": "openai" if os.getenv("OPENAI_API_KEY") else "offline",
            "default_model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            "server_has_api_key": bool(os.getenv("OPENAI_API_KEY")),
            "api_key_policy": "API key gửi theo request và không được lưu vào session, message hoặc log."}
