"""Small HTTP API for the D3 Teach-back Agent.

Run from the repository root:

    python codebase/server.py --provider openai --port 8000

POST /api/teach
{
  "session_id": "optional-id",
  "student_explanation": "...",
  "reset": false
}
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from agent_core import AuditLogger, TeachBackAgent, provider_from_name
from agent_graph import GraphSessionRunner


def make_handler(agent: TeachBackAgent, sessions: GraphSessionRunner):
    class Handler(BaseHTTPRequestHandler):
        server_version = "D3TeachBack/1.0"

        def _headers(self, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            self._headers(status)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._headers(HTTPStatus.NO_CONTENT)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json({"ok": True, "provider": agent.provider.name})
                return
            if self.path.startswith("/api/session/"):
                session_id = self.path[len("/api/session/") :]
                if not session_id:
                    self._json(
                        {"error": "invalid_request", "message": "session_id required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                state = sessions.get_state(session_id)
                if state is None:
                    self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(state)
                return
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/teach":
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 100_000:
                    raise ValueError("Invalid request body size")
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                explanation = str(payload.get("student_explanation", "")).strip()
                session_id = str(payload.get("session_id") or uuid.uuid4())
                result = sessions.run_turn(
                    explanation, session_id, reset=bool(payload.get("reset"))
                )
                self._json(result)
            except ValueError as exc:
                self._json({"error": "invalid_request", "message": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:  # keep the demo API alive and return an auditable error
                self._json(
                    {"error": "agent_failure", "message": str(exc)},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[http] {self.address_string()} - {format % args}", file=sys.stderr)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the D3 Teach-back Agent API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--provider", choices=("auto", "openai", "offline"), default="auto")
    args = parser.parse_args()

    logger = AuditLogger()
    provider = provider_from_name(args.provider, logger=logger)
    agent = TeachBackAgent(provider=provider, logger=logger)
    sessions = GraphSessionRunner(agent)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(agent, sessions))
    print(
        f"D3 Teach-back API listening on http://{args.host}:{args.port} "
        f"with provider={provider.name}"
    )
    print("POST /api/teach or GET /health; Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

