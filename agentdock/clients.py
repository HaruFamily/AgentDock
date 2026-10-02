"""Is a client still running with the config it had before we changed it?

A client reads its MCP config at start-up. After AgentDock writes a config we remember the time; the Agent
shows "需重啟" while any process of that client is still running that was started *before* the write.
When none is (restarted, or closed — it will read the new config next time), the mark clears itself.
"""
from __future__ import annotations

import re
from typing import Iterable

try:
    import psutil
except ImportError:  # optional: without it the mark is cleared by clicking it
    psutil = None  # type: ignore[assignment]

# Process names are compared case-insensitively. Claude Desktop and Claude Code are both "claude.exe",
# so they are told apart by where the executable lives.
_DESKTOP_HINT = re.compile(r"windowsapps|anthropicclaude|claude_[a-z0-9]{6,}|\\claude\\app-", re.I)
PROCESS_NAMES: dict[str, tuple[str, ...]] = {
    "codex": ("codex.exe", "codex"),
    "claude-desktop": ("claude.exe", "claude"),
    "claude-code": ("claude.exe", "claude"),
    "opencode": ("opencode.exe", "opencode", "opencode-desktop.exe", "opencode-cli.exe"),
}


def available() -> bool:
    return psutil is not None


def _matches(kind: str, name: str, exe: str) -> bool:
    if name.lower() not in PROCESS_NAMES.get(kind, ()):
        return False
    if kind == "claude-desktop":
        return bool(_DESKTOP_HINT.search(exe or ""))
    if kind == "claude-code":
        return not _DESKTOP_HINT.search(exe or "")
    return True


_ALL_NAMES = {n for names in PROCESS_NAMES.values() for n in names}


def _processes() -> Iterable[tuple[str, str, float]]:
    # Only the name is read for every process; path and start time only for the few that match.
    for proc in psutil.process_iter(["name"]):  # type: ignore[union-attr]
        name = proc.info.get("name") or ""
        if name.lower() not in _ALL_NAMES:
            continue
        try:
            yield name, proc.exe() or "", proc.create_time()
        except Exception:  # process ended or access denied
            continue


def still_old(kinds: dict[str, set[str]], since: dict[str, float]) -> set[str]:
    """kinds: {kind: {agent ids}}, since: {agent id: unix time of our write}.
    Returns the agent ids whose client still has a process older than the write."""
    if psutil is None:
        return set(since)
    oldest: dict[str, float] = {}
    for name, exe, created in _processes():
        for kind in kinds:
            if _matches(kind, name, exe):
                oldest[kind] = min(oldest.get(kind, created), created)
    stale = set()
    for kind, ids in kinds.items():
        for pid in ids:
            if kind in oldest and oldest[kind] < since[pid]:
                stale.add(pid)
    return stale
