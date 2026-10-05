"""Optional local-theme font loading and caching."""
from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase

class _Bus(QObject):
    ready = Signal(str)


_bus = _Bus()
_loaded: dict[str, str] = {}


def _load(path: Path) -> str | None:
    if str(path) in _loaded:
        return _loaded[str(path)]
    fid = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    if families:
        _loaded[str(path)] = families[0]
        return families[0]
    return None


def ensure(data_dir: Path, on_ready: Callable[[str], None], source: dict) -> str | None:
    """Family name when the font is available now; otherwise downloads it and calls on_ready(family) later."""
    path = data_dir / "fonts" / Path(source["file"]).name
    if path.is_file():
        return _load(path)
    _bus.ready.connect(on_ready)

    def work() -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(source["url"], headers={"User-Agent": "AgentDock"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 100_000:
                raise ValueError("font download too small")
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            _bus.ready.emit(str(path))
        except Exception:
            logging.exception("font download")

    threading.Thread(target=work, daemon=True).start()
    return None


def family_for(path_or_family: str) -> str | None:
    p = Path(path_or_family)
    return _load(p) if p.suffix == ".ttf" else path_or_family
