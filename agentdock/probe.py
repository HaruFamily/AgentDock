"""Start an MCP server exactly as an Agent's config says and do the MCP handshake (initialize + tools/list).

Used by "測試" in the MCP library: tells "the server itself fails" apart from "the client never reloaded".
Results are also appended to data/mcp-test.log so they can be read later.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any


def launch_spec(config: dict[str, Any]) -> tuple[list[str], dict[str, str]] | None:
    """Agent config entry (Codex / Claude / OpenCode shape) -> (argv, extra env). None for remote servers."""
    if not isinstance(config, dict) or config.get("url"):
        return None
    command = config.get("command")
    if isinstance(command, list):  # OpenCode
        argv = [str(x) for x in command]
    elif command:
        argv = [str(command), *[str(a) for a in config.get("args", [])]]
    else:
        return None
    env = config.get("env") or config.get("environment") or {}
    return argv, {str(k): str(v) for k, v in env.items()}


def probe(config: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    spec = launch_spec(config)
    if spec is None:
        return {"ok": None, "summary": "遠端或無指令，略過"}
    argv, extra = spec
    start = time.monotonic()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={**os.environ, **extra}, creationflags=flags)
    except OSError as e:
        return {"ok": False, "summary": f"無法啟動：{e}", "argv": argv}
    lines: queue.Queue = queue.Queue()
    err: list[bytes] = []
    threading.Thread(target=lambda: [lines.put(l) for l in iter(proc.stdout.readline, b"")] + [lines.put(None)],
                     daemon=True).start()
    threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()

    def send(msg: dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def wait_for(msg_id: int) -> dict[str, Any] | None:
        while True:
            left = timeout - (time.monotonic() - start)
            if left <= 0:
                return None
            try:
                line = lines.get(timeout=left)
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                data = json.loads(line)
            except ValueError:
                continue  # stray output on stdout
            if data.get("id") == msg_id:
                return data

    result: dict[str, Any] = {"argv": argv}
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "AgentDock", "version": "test"}}})
        init = wait_for(1)
        if init and "result" in init:
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = wait_for(2)
            n = len((tools or {}).get("result", {}).get("tools", []))
            result.update(ok=bool(tools and "result" in tools), tools=n)
        else:
            result["ok"] = False
    except OSError:
        result["ok"] = False
    finally:
        seconds = round(time.monotonic() - start, 1)
        try:
            proc.kill()
            proc.wait(5)
        except Exception:
            pass
    time.sleep(0.2)
    stderr = b"".join(err).decode("utf-8", "replace").strip()
    result["seconds"] = seconds
    result["stderr"] = stderr[-3000:]
    if result["ok"]:
        result["summary"] = f"正常（{seconds} 秒，{result.get('tools', 0)} 個工具）"
    elif seconds >= timeout:
        result["summary"] = f"逾時（{int(timeout)} 秒內沒有回應）"
    else:
        last = stderr.splitlines()[-1] if stderr else f"結束碼 {proc.returncode}"
        result["summary"] = f"啟動失敗：{last}"
    return result
