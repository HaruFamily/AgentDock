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


def venv_dir() -> Path:
    """runtime\\venv (prepared by AgentDock.exe, everything stays in this folder); .venv for plain `uv sync` setups."""
    portable = ROOT / "runtime" / "venv"
    return portable if (portable / "pyvenv.cfg").exists() or not (ROOT / ".venv").exists() else ROOT / ".venv"


def local_uvx() -> str | None:
    """uvx.exe that AgentDock.exe downloaded into runtime\\uv (so other computers need no uv installed)."""
    exe = ROOT / "runtime" / "uv" / "uvx.exe"
    return str(exe) if exe.is_file() else None


def is_gui_exe(path: Path) -> bool:
    """True when a Windows .exe is built for the GUI subsystem (opens no console window)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        pe = int.from_bytes(head[0x3C:0x40], "little")
        return head[pe:pe + 4] == b"PE\0\0" and int.from_bytes(head[pe + 92:pe + 94], "little") == 2
    except (OSError, ValueError):
        return False


def _base_home() -> Path | None:
    """The interpreter folder the .venv was made from (pyvenv.cfg `home`)."""
    try:
        for line in (venv_dir() / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() == "home":
                return Path(value.strip())
    except OSError:
        pass
    return None


def gui_interpreter() -> list[str]:
    """[pythonw, bootstrap] that runs with no console and the venv's packages (see gui_command)."""
    venv_w = venv_dir() / "Scripts" / "pythonw.exe"
    if venv_w.is_file() and is_gui_exe(venv_w):
        return [str(venv_w), str(ROOT / "AgentDock.pyw")]
    home = _base_home()
    if home and (home / "pythonw.exe").is_file() and is_gui_exe(home / "pythonw.exe"):
        return [str(home / "pythonw.exe"), str(ROOT / "AgentDock.pyw")]
    return [gui_python(), str(ROOT / "AgentDock.pyw")]


def module_command(module: str, *args: str) -> list[str]:
    """Run another GUI module (e.g. TokenGauge's floating window) the same console-free way."""
    return [*gui_interpreter(), "--module", module, *args]


def gui_command(*args: str) -> list[str]:
    """How to start the floating app without any console window — the same result on every computer.

    Some uv/Python combinations (seen with Python 3.14) put a *console* launcher at .venv\\Scripts\\pythonw.exe,
    which opens a cmd window for as long as AgentDock runs. Then the base interpreter's real pythonw.exe runs
    AgentDock.pyw, which loads the .venv packages itself."""
    launcher = ROOT / "AgentDock.exe"
    if sys.platform == "win32" and launcher.is_file():
        return [str(launcher), *args]  # also re-prepares runtime\\ when uv.lock changed
    venv_w = venv_dir() / "Scripts" / "pythonw.exe"
    if venv_w.is_file() and is_gui_exe(venv_w):
        return [str(venv_w), "-m", "agentdock", *args]
    home = _base_home()
    if home and (home / "pythonw.exe").is_file() and is_gui_exe(home / "pythonw.exe"):
        return [str(home / "pythonw.exe"), str(ROOT / "AgentDock.pyw"), *args]
    return [gui_python(), "-m", "agentdock", *args]


def console_python() -> str:
    """python.exe (never pythonw) so MCP stdio pipes always work."""
    venv = venv_dir() / "Scripts" / "python.exe"
    if sys.platform == "win32" and venv.is_file():
        return str(venv)  # also when the app itself runs on the base interpreter (see gui_command)
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
