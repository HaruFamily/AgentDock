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

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
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
