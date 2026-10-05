"""Put AgentDock-managed CLI tools on the user's PATH.

Hooks such as rtk's call the tool by name (`rtk hook claude`) and the commands they rewrite to
(`rtk git status`) run in the agent's shell, so the executable has to be on PATH — an absolute
path in the hook alone is not enough. AgentDock keeps one stable folder, data/bin, holding a copy
of each such tool, and (after the user confirms) adds that folder to the *user* PATH (HKCU).

Versioned downloads stay in data/mcp/<key>/<version>/; data/bin only holds the current copy,
so updating never has to touch a running version folder.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bin_dir(data: Path) -> Path:
    return data / "bin"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(p.strip().strip('"')))) if p.strip() else ""


def _registry_path(scope: str) -> str:
    """Raw PATH from the registry ('user' or 'system'). Empty off Windows or on error."""
    if sys.platform != "win32":
        return ""
    import winreg
    key, sub = ((winreg.HKEY_CURRENT_USER, "Environment") if scope == "user" else
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
    try:
        with winreg.OpenKey(key, sub) as k:
            return str(winreg.QueryValueEx(k, "Path")[0])
    except OSError:
        return ""


def current_path() -> str:
    """PATH as a newly started program would see it (this process's copy can be stale after we change it)."""
    if sys.platform != "win32":
        return os.environ.get("PATH", "")
    parts = [p for p in (_registry_path("system") + ";" + _registry_path("user")).split(";") if p.strip()]
    parts += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p.strip()]
    seen, out = set(), []
    for p in parts:
        n = _norm(p)
        if n and n not in seen:
            seen.add(n)
            out.append(os.path.expandvars(p))
    return os.pathsep.join(out)


def which(name: str) -> str | None:
    return shutil.which(name, path=current_path())


def on_user_path(folder: Path) -> bool:
    target = _norm(str(folder))
    source = _registry_path("user") if sys.platform == "win32" else os.environ.get("PATH", "")
    sep = ";" if sys.platform == "win32" else os.pathsep
    return any(_norm(p) == target for p in source.split(sep))


def add_to_user_path(folder: Path) -> bool:
    """Append folder to HKCU PATH and tell running programs. Returns False when it was already there."""
    if sys.platform != "win32":
        raise ValueError("只支援 Windows：請自行把資料夾加入 PATH：" + str(folder))
    if on_user_path(folder):
        return False
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
        try:
            value, kind = winreg.QueryValueEx(k, "Path")
        except OSError:
            value, kind = "", winreg.REG_EXPAND_SZ
        value = str(value).rstrip(";")
        winreg.SetValueEx(k, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ,
                          (value + ";" if value else "") + str(folder))
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x1A, 0x2
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 3000,
                                             ctypes.byref(result))
    return True


def link(exe: Path, folder: Path) -> Path:
    """Copy exe into folder under its own name (replaced only when it differs)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / exe.name
    if target.exists() and target.stat().st_size == exe.stat().st_size and target.read_bytes() == exe.read_bytes():
        return target
    temp = target.with_name(target.name + ".new")
    shutil.copy2(exe, temp)
    try:
        os.replace(temp, target)
    except OSError:
        temp.unlink(missing_ok=True)
        raise ValueError(f"{target.name} 正在執行中，請關閉使用它的 Agent 後再試。") from None
    return target
