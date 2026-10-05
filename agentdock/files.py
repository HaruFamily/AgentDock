from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("﻿")
    except FileNotFoundError:
        return ""


def atomic_write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        if isinstance(text, bytes):
            temp.write_bytes(text)
        else:
            temp.write_text(text, encoding="utf-8", newline="")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=1))


def load_json(path: Path, default: Any) -> Any:
    text = read_text(path)
    return json.loads(text) if text.strip() else default


def backup_path(path: Path) -> Path:
    import time
    return path.with_name(f"{path.name}.agentdock-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.bak")


def prune_backups(path: Path, keep: int = 3) -> list[Path]:
    """Keep only the newest `keep` AgentDock backups of one file (<name>.agentdock-<ms>-<id>.bak)."""
    found = sorted(path.parent.glob(f"{path.name}.agentdock-*.bak"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    removed = []
    for old in found[keep:]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed
