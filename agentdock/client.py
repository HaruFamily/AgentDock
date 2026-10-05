"""Used by the MCP process: find (or start) the floating app and talk to its broker."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from agentdock.paths import ROOT, data_dir, gui_command
from agentdock.broker import PROTOCOL


def owner_id() -> str:
    ident = os.environ.get("AGENTDOCK_CLIENT_ID") or f"{os.environ.get('AGENTDOCK_SOURCE', 'MCP')}:{os.getcwd()}"
    return hashlib.sha256(ident.encode()).hexdigest()


class BrokerClient:
    def __init__(self, endpoint: dict[str, Any], owner: str | None = None):
        self.endpoint = endpoint
        self.owner = owner or owner_id()

    def request(self, path: str, data: Any = None, timeout: float = 15.0) -> Any:
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.endpoint['port']}{path}", data=body, method="GET" if data is None else "POST",
            headers={"Authorization": f"Bearer {self.endpoint['token']}", "X-AgentDock-Owner": self.owner,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read()).get("error")
            except Exception:
                message = None
            raise RuntimeError(message or f"Local service returned {e.code}") from None


def _find() -> BrokerClient | None:
    registry = data_dir() / "endpoint.json"
    try:
        endpoint = json.loads(registry.read_text(encoding="utf-8"))
        if not (1 <= int(endpoint["port"]) <= 65535) or len(endpoint["token"]) != 64:
            return None
        client = BrokerClient(endpoint)
        health = client.request("/health", timeout=0.7)
        if health.get("ok") and health.get("protocol") == PROTOCOL:
            return client
    except Exception:
        return None  # registry may belong to a previous process
    return None


def ensure_app() -> BrokerClient:
    current = _find()
    if current:
        return current
    if os.environ.get("AGENTDOCK_NO_LAUNCH") == "1":
        raise RuntimeError("AgentDock 未啟動。請先開啟 AgentDock，或取消 AGENTDOCK_NO_LAUNCH。")
    env = {**os.environ, "AGENTDOCK_DATA_DIR": str(data_dir())}
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    # stdout must never be inherited: it is the MCP stdio channel.
    subprocess.Popen(gui_command("--background"), cwd=str(ROOT), env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, start_new_session=sys.platform != "win32")
    for _ in range(100):
        time.sleep(0.2)
        ready = _find()
        if ready:
            return ready
    raise RuntimeError("AgentDock 未能啟動。請手動雙擊 AgentDock.exe 檢查錯誤。")
