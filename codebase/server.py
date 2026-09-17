"""HTTP server for the multi-lesson Teach-back Studio.

Run from the repository root::

    python codebase/server.py --port 8000

The same process serves the UI and JSON API. Open http://127.0.0.1:8000.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from lesson_engine import TeachBackWebEngine
from platform_runtime import ConversationStore, LessonCatalog, runtime_info


CODEBASE_DIR = Path(__file__).resolve().parent
UI_DIR = CODEBASE_DIR / "ui"


def make_handler(platform: TeachBackWebEngine, default_provider: str = "auto"):
    resolved_provider = (
        runtime_info()["default_provider"] if default_provider == "auto" else default_provider
    )

    class Handler(BaseHTTPRequestHandler):
        server_version = "TeachBackStudio/2.0"

        def _headers(
            self, status: int = 200, content_type: str = "application/json; charset=utf-8"
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache"
            )
            self.end_headers()

        def _json(self, payload: Any, status: int = 200) -> None:
            self._headers(status)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 100_000:
                raise ValueError("Kích thước request không hợp lệ")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body phải là JSON object")
            return payload

        def _serve_ui(self, relative_path: str) -> None:
            requested = (UI_DIR / relative_path).resolve()
            ui_root = UI_DIR.resolve()
            if requested != ui_root and ui_root not in requested.parents:
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return
            if not requested.is_file():
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
            if content_type.startswith("text/"):
                content_type += "; charset=utf-8"
            self._headers(HTTPStatus.OK, content_type)
            self.wfile.write(requested.read_bytes())

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._headers(HTTPStatus.NO_CONTENT)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            try:
                if path == "/health":
                    self._json(
                        {
                            "ok": True,
                            "service": "teach-back-studio",
                            "lessons": len(platform.catalog.list_public()),
                        }
                    )
                    return
                if path == "/api/runtime":
                    info = runtime_info()
                    info["default_provider"] = resolved_provider
                    self._json(info)
                    return
                if path == "/api/lessons":
                    self._json({"lessons": platform.catalog.list_public()})
                    return
                if path.startswith("/api/lessons/"):
                    lesson_id = path.removeprefix("/api/lessons/")
                    lesson = platform.catalog.get(lesson_id)
                    self._json(platform.catalog.public_lesson(lesson))
                    return
                if path == "/api/sessions":
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["30"])[0])
                    self._json({"sessions": platform.list_sessions(limit)})
                    return
                if path.startswith("/api/sessions/"):
                    session_id = path.removeprefix("/api/sessions/").strip("/")
                    if not session_id or "/" in session_id:
                        raise KeyError("session not found")
                    session = platform.get_session(session_id)
                    if session is None:
                        raise KeyError("session not found")
                    self._json(session)
                    return
                if path.startswith("/api/session/"):
                    session_id = path.removeprefix("/api/session/").strip("/")
                    session = platform.get_session(session_id)
                    if session is None:
                        raise KeyError("session not found")
                    self._json(session)
                    return
                if path in {"/", "/index.html"}:
                    self._serve_ui("index.html")
                    return
                if path.startswith("/assets/"):
                    self._serve_ui(path.lstrip("/"))
                    return
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            except KeyError as exc:
                self._json({"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._json({"error": "invalid_request", "message": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/chat":
                    result = platform.start_chat(
                        str(payload.get("content", "")),
                        provider=str(payload.get("provider", resolved_provider)),
                        model=str(payload.get("model", "") or "") or None,
                        api_key=str(payload.get("api_key", "") or "") or None,
                    )
                    self._json(result, HTTPStatus.CREATED if result.get("session_id") else HTTPStatus.OK)
                    return
                if path == "/api/sessions/clear":
                    deleted = platform.clear_history()
                    self._json({"ok": True, "deleted_sessions": deleted})
                    return
                if path == "/api/sessions":
                    lesson_id = str(payload.get("lesson_id") or platform.catalog.default_lesson_id)
                    self._json(platform.create_session(lesson_id), HTTPStatus.CREATED)
                    return
                if path.startswith("/api/sessions/") and path.endswith("/messages"):
                    session_id = (
                        path.removeprefix("/api/sessions/")
                        .removesuffix("/messages")
                        .strip("/")
                    )
                    if not session_id or "/" in session_id:
                        raise KeyError("session not found")
                    result = platform.send_message(
                        session_id,
                        str(payload.get("content", "")),
                        provider=str(payload.get("provider", resolved_provider)),
                        model=str(payload.get("model", "") or "") or None,
                        api_key=str(payload.get("api_key", "") or "") or None,
                    )
                    self._json(result)
                    return
                # Adapter for callers of the original single-topic endpoint.
                if path == "/api/teach":
                    session_id = str(payload.get("session_id", "")).strip() or None
                    session = platform.get_session(session_id) if session_id else None
                    if session is None:
                        session = platform.create_session(
                            platform.catalog.default_lesson_id, session_id=session_id
                        )
                    result = platform.send_message(
                        session["id"],
                        str(payload.get("student_explanation", "")),
                        provider=str(payload.get("provider", resolved_provider)),
                        model=str(payload.get("model", "") or "") or None,
                        api_key=str(payload.get("api_key", "") or "") or None,
                    )
                    self._json(result)
                    return
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            except KeyError as exc:
                self._json({"error": "not_found", "message": str(exc)}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": "invalid_request", "message": str(exc)}, HTTPStatus.BAD_REQUEST)
            except sqlite3.IntegrityError as exc:
                self._json({"error": "conflict", "message": str(exc)}, HTTPStatus.CONFLICT)
            except RuntimeError as exc:
                self._json({"error": "provider_failure", "message": str(exc)}, HTTPStatus.BAD_GATEWAY)
            except Exception as exc:
                print(f"[server] unexpected error: {exc!r}", file=sys.stderr)
                self._json(
                    {"error": "server_failure", "message": "Backend gặp lỗi không mong đợi"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[http] {self.address_string()} - {format % args}", file=sys.stderr)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the multi-lesson Teach-back Studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--catalog", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    # Kept so existing commands do not break; the UI selects runtime provider.
    parser.add_argument("--provider", choices=("auto", "openai", "offline"), default="auto")
    args = parser.parse_args()

    platform = TeachBackWebEngine(
        catalog=LessonCatalog(args.catalog) if args.catalog else None,
        store=ConversationStore(args.db) if args.db else None,
    )
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(platform, default_provider=args.provider)
    )
    print(f"Teach-back Studio listening on http://{args.host}:{args.port}")
    print("Provider and model can be selected in the UI; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
