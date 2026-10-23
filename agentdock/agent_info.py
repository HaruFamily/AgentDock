"""What the settings page says about each Agent: its program and version, the model it uses by default and
the account it is signed in with. Only reads what is already on this computer; `--version` runs in the
background (CREATE_NO_WINDOW) the first time the page is shown."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from typing import Any, Callable

VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.]+)?")
QUOTA_KEYS = {"claude-code": "claude", "codex": "codex", "grok-cli": "grok"}


def find_program(kind: str) -> str | None:
    from agentdock.dispatch import claude, codex, grok, opencode
    finder = {"claude-code": claude.find_executable, "codex": codex.find_executable,
              "grok-cli": grok.find_executable, "opencode": opencode.find_executable}.get(kind)
    try:
        return finder() if finder else None
    except Exception:
        logging.info("find %s", kind, exc_info=True)
        return None


def program_version(path: str) -> str:
    """'2.1.288' from `<program> --version`; '' when it does not say."""
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    match = VERSION.search((done.stdout or "") + " " + (done.stderr or ""))
    return match.group(0) if match else ""


def model_text(options: dict[str, Any]) -> str:
    """'Opus 5.5 · 中' from the options the chat window learned from that Agent."""
    from agentdock.dispatch.adapter import EFFORT_LABELS
    defaults = options.get("defaults") or {}
    model = str(defaults.get("model") or "")
    models = {str(m.get("id")): m for m in options.get("models", []) if isinstance(m, dict)}
    if not model:
        return ""
    name = str(models.get(model, {}).get("label") or model)
    effort = defaults.get("effort") or ""
    if isinstance(effort, dict):
        effort = effort.get(model) or ""
    effort = EFFORT_LABELS.get(str(effort), str(effort)) if effort else str(options.get("effort_unset") or "")
    return " · ".join(x for x in (name, effort) if x)


def account_text(result: Any) -> tuple[str, bool]:
    """(text, is_problem) from the quota tool's last result for that Agent's provider."""
    if result is None or getattr(result, "missing", False):
        return "", False
    accounts = list(getattr(result, "accounts", []) or [])
    if accounts:
        a = accounts[0]
        text = " · ".join(x for x in (a.label, a.plan) if x)
        return text or "已登入", False
    if getattr(result, "error", None):
        return str(result.error), True
    return "", False


class AgentFacts:
    """Cached program versions plus live model/account lookups, for AgentsView.agent_info."""

    def __init__(self, options: Callable[[str], dict], quota: Callable[[str], Any] = lambda _k: None) -> None:
        self.options, self.quota = options, quota
        self.versions: dict[str, str | None] = {}   # kind -> version ('' found, no version; None not found)
        self._busy = False

    def refresh_versions(self, kinds: list[str], done: Callable[[], None]) -> None:
        if self._busy:
            return
        self._busy = True

        def run() -> None:
            found: dict[str, str | None] = {}
            for kind in kinds:
                path = find_program(kind)
                found[kind] = program_version(path) if path else None
            self.versions.update(found)
            self._busy = False
            done()
        threading.Thread(target=run, name="AgentDock versions", daemon=True).start()

    def __call__(self, kind: str) -> dict[str, Any]:
        info: dict[str, Any] = {}
        if kind in self.versions:
            info["program"] = self.versions[kind]
        try:
            text = model_text(self.options(kind) or {})
        except Exception:
            text = ""
        if text:
            info["model"] = text
        key = QUOTA_KEYS.get(kind)
        if key:
            text, problem = account_text(self.quota(key))
            if text:
                info["account"] = text
                info["account_problem"] = problem
        return info
