"""Shared parts of the clients that AgentDock drives as child processes (Claude Code, Codex).

An adapter speaks the client's own protocol and reports in the OpenCode V2 shape that the rest of
AgentDock already understands:

- live events: `{'type': 'session.text.delta', 'data': {'sessionID': ..., ...}}` through `emit`
- stored history: `{'type': 'user'|'assistant'|'idle', 'id', ...}` messages, oldest first

Every method is called on a worker thread and may block. Errors are `ClientError` with a message
that can be shown as is.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from agentdock.dispatch.opencode import NO_WINDOW

PAGE = 60
MAX_OUTPUT = 4000


class ClientError(RuntimeError):
    """A message that can be shown to the user as is."""


class Adapter:
    client = ''
    name = ''

    def __init__(self) -> None:
        self.emit: Callable[[str, dict], None] = lambda _sid, _event: None

    def event(self, sid: str, kind: str, event_id: str | None = None, **data: Any) -> None:
        event = {'type': kind, 'data': {'sessionID': sid, **data}}
        if event_id:
            event['id'] = event_id
        self.emit(sid, event)

    # The interface the dispatcher uses.
    def new_session(self, directory: str) -> str:
        raise NotImplementedError

    def prompt(self, row: dict, text: str) -> None:
        raise NotImplementedError

    def interrupt(self, row: dict) -> None:
        raise NotImplementedError

    def reply_permission(self, row: dict, request_id: str, decision: str) -> None:
        raise NotImplementedError

    def reply_form(self, row: dict, form_id: str, answer: dict | None) -> None:
        raise NotImplementedError

    def history(self, row: dict, cursor: str | None) -> tuple[list[dict], str | None]:
        raise NotImplementedError

    def delete(self, row: dict) -> None:
        """Delete the session in the client itself (its saved history)."""
        raise ClientError(f'{self.name} 不支援刪除。')

    def running(self, sid: str) -> bool:
        return False

    def close(self) -> None:
        pass


def page(messages: list[dict], cursor: str | None, size: int = PAGE) -> tuple[list[dict], str | None]:
    """The page that ends at `cursor` (an index), oldest first, and the cursor for the page before it."""
    try:
        end = min(int(cursor), len(messages)) if cursor else len(messages)
    except ValueError:
        end = len(messages)
    start = max(0, end - size)
    return messages[start:end], (str(start) if start > 0 else None)


def text_content(text: str) -> list[dict]:
    return [{'type': 'text', 'text': text[:MAX_OUTPUT]}] if text else []


def first_file(candidates: Iterable[Path | str | None]) -> str | None:
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def npm_dir() -> list[Path]:
    """Folders where npm puts global shims (the one on PATH first)."""
    dirs = []
    for name in ('npm', 'npm.cmd'):
        found = shutil.which(name)
        if found:
            dirs.append(Path(found).parent)
    appdata = os.environ.get('APPDATA')
    if appdata:
        dirs.append(Path(appdata) / 'npm')
    return dirs


_spawn_queue: 'queue.Queue[tuple[Callable[[], Any], dict]]' = queue.Queue()
_spawn_thread: threading.Thread | None = None
_spawn_lock = threading.Lock()


def _spawner() -> None:
    while True:
        make, box = _spawn_queue.get()
        try:
            box['value'] = make()
        except BaseException as e:  # handed back to the caller
            box['error'] = e
        box['done'].set()


def _spawn(make: Callable[[], Any]) -> Any:
    """Start child processes from one long-lived thread.

    Some CLIs (grok) ask the OS to stop them when the thread that started them ends; the worker
    threads that send messages are short-lived.
    """
    global _spawn_thread
    with _spawn_lock:
        if _spawn_thread is None or not _spawn_thread.is_alive():
            _spawn_thread = threading.Thread(target=_spawner, name='AgentDock spawner', daemon=True)
            _spawn_thread.start()
    box: dict = {'done': threading.Event()}
    _spawn_queue.put((make, box))
    box['done'].wait()
    if 'error' in box:
        raise box['error']
    return box['value']


class ChildProcess:
    """A client process with JSON lines on stdin/stdout; stderr kept as a short tail for errors."""

    def __init__(self, args: list[str], cwd: str | None, env: dict[str, str],
                 on_line: Callable[[dict], None], on_exit: Callable[[int | None, str], None], name: str):
        try:
            self.proc = _spawn(lambda: subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                        creationflags=NO_WINDOW))
        except OSError as e:
            raise ClientError(f'無法啟動 {name}：{e}') from e
        self.name = name
        self._write_lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=20)
        self._on_line, self._on_exit = on_line, on_exit
        threading.Thread(target=self._read, name=f'AgentDock {name}', daemon=True).start()
        threading.Thread(target=self._read_err, name=f'AgentDock {name} stderr', daemon=True).start()

    def send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
        with self._write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise ClientError(f'{self.name} 已結束。') from e

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stderr_tail(self) -> str:
        lines = [l for l in self._stderr if l.strip()]
        return '\n'.join(lines[-5:])

    def stop(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass

    def _read(self) -> None:
        for raw in self.proc.stdout:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('{'):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            try:
                self._on_line(message)
            except Exception:  # one bad message must not stop the stream
                logging.exception('%s message', self.name)
        try:
            code = self.proc.wait(5)
        except subprocess.TimeoutExpired:
            code = None
        self._on_exit(code, self.stderr_tail())

    def _read_err(self) -> None:
        for raw in self.proc.stderr:
            text = raw.decode('utf-8', 'replace').rstrip()
            if text:
                self._stderr.append(_strip_ansi(text)[:500])


def _strip_ansi(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)


def child_env(extra: dict[str, str] | None = None, drop: Iterable[str] = ()) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in set(drop)}
    env.update(extra or {})
    if sys.platform == 'win32':
        env.setdefault('PYTHONIOENCODING', 'utf-8')
    return env
