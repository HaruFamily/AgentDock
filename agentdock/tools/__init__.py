"""Extra tool tabs for the panel.

To integrate another Python tool, add a module in this package that defines:

    from agentdock.tools import ToolSpec
    def create(ctx):            # ctx: ToolContext (data_dir, store, notify)
        return MyWidget()       # any QWidget
    TOOL = ToolSpec(title="我的工具", create=create, order=50)

It shows up as a tab automatically. A tool that fails to load is skipped and logged.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ToolContext:
    data_dir: Path
    store: Any
    notify: Callable[[str], None]


@dataclass
class ToolSpec:
    title: str
    create: Callable[[ToolContext], Any]
    order: int = 100
    error: str = field(default="", repr=False)


def discover() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
            spec = getattr(module, "TOOL", None)
            if isinstance(spec, ToolSpec):
                tools.append(spec)
        except Exception:
            logging.exception("tool %s failed to load", info.name)
    return sorted(tools, key=lambda t: (t.order, t.title))
