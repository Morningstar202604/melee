"""假的 OpenAI 兼容服务器：只实现本项目用到的两个端点。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # 静音
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        server: FakeOpenAI = self.server.fake  # type: ignore[attr-defined]
        server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "body": body,
            }
        )

        if server.status != 200:
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": {"message": f"upstream failed; auth={self.headers.get('Authorization', '')}"}}
                ).encode()
            )
            return

        reply = server.replies.pop(0) if server.replies else {"content": '{"kind":"guess","value":"灯塔"}'}
        message: dict[str, Any] = {"role": "assistant", "content": reply["content"]}
        if reply.get("reasoning"):
            message["reasoning_content"] = reply["reasoning"]

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                }
            ).encode()
        )


class FakeOpenAI(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fake = self
        self.requests: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.status = 200
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> "FakeOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.shutdown()
        self.server_close()
