from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        server: FakeStream = self.server.fake  # type: ignore[attr-defined]
        server.requests.append({"path": self.path, "body": body})

        if not body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": server.content},
                                "finish_reason": "stop",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ).encode()
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for reasoning_piece in server.reasoning_pieces:
            chunk = {"choices": [{"index": 0, "delta": {"reasoning_content": reasoning_piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        for content_piece in server.content_pieces:
            chunk = {"choices": [{"index": 0, "delta": {"content": content_piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class FakeStream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fake = self
        self.requests: list[dict[str, Any]] = []
        self.content = '{"kind":"guess","value":"灯塔"}'
        self.content_pieces: list[str] = []
        self.reasoning_pieces: list[str] = []
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "FakeStream":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.shutdown()
        self.server_close()
