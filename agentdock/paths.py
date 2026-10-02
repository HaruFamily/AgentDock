"""Fixed locations. Everything personal lives in data/ beside the code (not committed)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    path = Path(os.environ.get("AGENTDOCK_DATA_DIR") or ROOT / "data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def console_python() -> str:
    """python.exe (never pythonw) so MCP stdio pipes always work."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def gui_python() -> str:
    """pythonw.exe on Windows so the floating app never opens a console."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else str(exe)
