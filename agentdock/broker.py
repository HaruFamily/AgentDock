"""127.0.0.1 broker: MCP processes reach the single floating app through it.

Random bearer token, browser Origin rejected. Answers are only submitted from the UI, never over HTTP.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from agentdock import VERSION
from agentdock.files import atomic_json
from agentdock.qa.schema import AskInput
from agentdock.qa.store import QuestionStore

PROTOCOL = 3
MAX_BODY = 30 * 1024 * 1024
_UUID = r"[a-f0-9-]{36}"
_ROUTE = re.compile(rf"^/questions/({_UUID})(?:/(detach|attachments/({_UUID})))?$")


class _Create(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    input: dict[str, Any]


class Broker:
    def __init__(self, directory: Path, store: QuestionStore, on_show: Callable[[str | None], None] = lambda _id: None):
        self.directory = directory
        self.store = store
        self.on_show = on_show
        self.token = secrets.token_hex(32)
        self._server: ThreadingHTTPServer | None = None

    # -- request handling (pure, testable) ------------------------------------
    def handle(self, method: str, path: str, headers: dict[str, str], body: bytes) -> tuple[int, Any]:
        expected = f"Bearer {self.token}".encode()
        provided = headers.get("authorization", "").encode()
        if headers.get("origin") or not re.fullmatch(r"127\.0\.0\.1:\d+", headers.get("host", "")) \
                or not hmac.compare_digest(provided, expected):
            return 403, {"error": "Forbidden"}
        try:
            path = urlsplit(path).path
            if method == "GET" and path == "/health":
                return 200, {"ok": True, "protocol": PROTOCOL, "platform": "AgentDock", "version": VERSION}
            owner = headers.get("x-agentdock-owner", "")
            if not re.fullmatch(r"[a-f0-9]{64}", owner):
                raise ValueError("Invalid client identity")
            if method == "POST" and path == "/show":
                self.on_show(None)
                return 200, {"ok": True}
            if method == 'POST' and path == '/activity':
                event_id = self.store.activity.receive(owner, json.loads(body))
                return 200, {'id': event_id, 'status': 'received'}
            if method == "POST" and path == "/chat/events":
                raw = _Create.model_validate_json(body)
                event = self.store.receive(owner, raw.source, raw.input)
                return 200, {"id": event["id"], "status": "received"}
            if method == "POST" and path == "/questions":
                raw = _Create.model_validate_json(body)
                parsed = AskInput.model_validate(raw.input).model_dump()
                original = raw.input
                def prep(images: list[dict], names: list[dict]) -> list[dict]:
                    out = []
                    for n, image in enumerate(images):
                        if not image.get("data_url"):
                            raise ValueError("Images must be prepared by the MCP adapter")
                        name = (names[n] if n < len(names) else {}).get("name") or "image"
                        out.append({"name": name, "data_url": image["data_url"], "caption": image.get("caption", "")})
                    return out
                if parsed["questions"]:
                    items = []
                    for n, item in enumerate(parsed["questions"]):
                        src = original["questions"][n]
                        item["images"] = prep(item["images"], src.get("images", []))
                        for i, option in enumerate(item["options"]):
                            option["images"] = prep(option["images"], src["options"][i].get("images", []))
                        items.append(item)
                    base = {k: parsed[k] for k in ("request_key", "work_id", "work_title", "question")}
                    group = self.store.create_group(owner, raw.source, base, items)
                    for q in group:
                        self.store.touch(q["id"])
                    return 200, {"id": group[0]["id"], "group": [q["id"] for q in group]}
                parsed["images"] = prep(parsed["images"], original.get("images", []))
                for i, option in enumerate(parsed["options"]):
                    option["images"] = prep(option["images"], original["options"][i].get("images", []))
                parsed.pop("questions", None)
                q = self.store.create(owner, raw.source, parsed)
                self.store.touch(q["id"])
                return 200, {"id": q["id"]}
            match = _ROUTE.match(path)
            if not match:
                return 404, {"error": "Not found"}
            q = self.store.get(match.group(1), owner)
            if method == "GET" and not match.group(2):
                self.store.touch(q["id"])
                members = self.store.group_members(q["id"], owner) if q.get("group") else []
                for m in members:
                    self.store.touch(m["id"])
                answer = q.get("answer")
                chosen = set((answer or {}).get("attachment_ids", []))
                return 200, {"id": q["id"], "status": q["status"], "question": q["question"],
                             "group": [m["id"] for m in members] if members else None,
                             "options": [{"id": o["id"], "label": o["label"]} for o in q["options"]],
                             "answer": answer,
                             "attachments": [{**a, "path": str(self.store.named_path(a["id"], a["name"]))}
                                             for a in q["uploads"] if a["id"] in chosen]}
            if method == "POST" and match.group(2) == "detach":
                for m in (self.store.group_members(q["id"], owner) if q.get("group") else [q]):
                    self.store.detach(m["id"])
                return 200, {"ok": True}
            if method == "GET" and match.group(3):
                import base64
                meta, data = self.store.asset(q["id"], match.group(3), owner, answers_only=True)
                return 200, {**meta, "data": base64.b64encode(data).decode(),
                             "path": str(self.store.named_path(meta["id"], meta["name"]))}
            return 404, {"error": "Not found"}
        except KeyError as e:
            return 400, {"error": str(e.args[0]) if e.args else "Not found"}
        except Exception as e:  # validation and store errors go back to the agent as text
            return 400, {"error": str(e)}

    # -- server ---------------------------------------------------------------
    def start(self) -> dict[str, Any]:
        broker = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                pass

            def _serve(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    code, data = 413, {"error": "Request too large"}
                else:
                    body = self.rfile.read(length) if length else b""
                    headers = {k.lower(): v for k, v in self.headers.items()}
                    code, data = broker.handle(method, self.path, headers, body)
                payload = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                self._serve("GET")

            def do_POST(self) -> None:
                self._serve("POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="agentdock-broker", daemon=True).start()
        endpoint = {"port": self._server.server_address[1], "token": self.token, "pid": os.getpid(), "protocol": PROTOCOL}
        atomic_json(self.directory / "endpoint.json", endpoint)
        return endpoint

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
