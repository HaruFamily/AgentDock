"""Small Windows helpers. Everything is a no-op on other platforms (tests run there).

- force_topmost(): re-assert HWND_TOPMOST; Windows can drop a Qt "stays on top" window after another
  topmost app, a closed dialog or Win+D. One SetWindowPos call, called only on events — never polled.
- GlobalHotkey: RegisterHotKey; Windows delivers WM_HOTKEY, so waiting for it costs nothing.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QWidget

IS_WINDOWS = sys.platform == "win32"
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000


def force_topmost(widget: QWidget) -> None:
    if not IS_WINDOWS or not widget.isVisible():
        return
    try:
        import ctypes
        ctypes.windll.user32.SetWindowPos(int(widget.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                                          SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        logging.exception("SetWindowPos")


class GlobalHotkey(QAbstractNativeEventFilter):
    """System-wide shortcut (default Ctrl+Alt+Q) that calls `callback`."""

    HOTKEY_ID = 0xAD01

    def __init__(self, callback: Callable[[], None], hotkey_id: int = 0xAD01) -> None:
        super().__init__()
        self.HOTKEY_ID = hotkey_id
        self.callback = callback
        self.hwnd = 0
        self.registered = False

    def register(self, widget: QWidget, key: str = "Q", ctrl: bool = True, alt: bool = True, shift: bool = False) -> bool:
        if not IS_WINDOWS:
            return False
        import ctypes
        mods = MOD_NOREPEAT | (MOD_CONTROL if ctrl else 0) | (MOD_ALT if alt else 0) | (MOD_SHIFT if shift else 0)
        self.hwnd = int(widget.winId())
        self.registered = bool(ctypes.windll.user32.RegisterHotKey(self.hwnd, self.HOTKEY_ID, mods, ord(key.upper())))
        if not self.registered:
            logging.warning("RegisterHotKey failed (shortcut used by another program?)")
        return self.registered

    def unregister(self) -> None:
        if IS_WINDOWS and self.registered:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(self.hwnd, self.HOTKEY_ID)
            self.registered = False

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        kind = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
        if IS_WINDOWS and self.registered and kind == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0


# ---------------------------------------------------------------------------- shortcuts
def start_menu_dir() -> "Path":
    import os
    from pathlib import Path
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


# ---------------------------------------------------------------------------- start with Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AgentDock"


def autostart_get() -> str | None:
    """The command Windows runs at sign-in for AgentDock (HKCU Run), or None."""
    if not IS_WINDOWS:
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, RUN_NAME)[0])
    except OSError:
        return None


def autostart_set(command: str | None) -> None:
    """Write (or with None remove) the HKCU Run value. No admin rights needed."""
    if not IS_WINDOWS:
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if command:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def foreground_is_fullscreen() -> bool:
    """A game / video / presentation covers its whole monitor (our own windows and the desktop don't count)."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        import os
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception:
        return False
