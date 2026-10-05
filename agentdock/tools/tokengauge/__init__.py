"""Quota data for AgentDock's floating card (TokenGauge integrated).

gauge.py holds TokenGauge's collectors (Claude Code, Codex workspaces, Grok); its old tkinter window is gone.
The floating card shows the numbers and only this process talks to the servers (token refreshes write back to
the CLIs' credential files).

Settings and saved Codex workspaces live in data/tokengauge (copied once from %APPDATA%\\TokenGauge).
No TOOL here on purpose: quota is shown on the floating card, not as a panel tab.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from agentdock.tools.tokengauge import gauge


def migrate(folder: Path) -> list[str]:
    """First run: copy the standalone TokenGauge settings and saved Codex logins, and stop its own autostart."""
    done = []
    old = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "TokenGauge"
    if not (folder / "config.json").exists() and old.is_dir() and old.resolve() != folder.resolve():
        folder.mkdir(parents=True, exist_ok=True)
        if (old / "config.json").is_file():
            shutil.copy2(old / "config.json", folder / "config.json")
        if (old / "codex_accounts").is_dir():
            shutil.copytree(old / "codex_accounts", folder / "codex_accounts", dirs_exist_ok=True)
        done.append("已沿用原本 TokenGauge 的設定與 Codex workspace")
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/TokenGauge.cmd"
    try:
        if startup.is_file() and "tokengauge.pyw" in startup.read_text("utf-8", "replace").lower():
            startup.unlink()
            done.append("已取消原本 TokenGauge 的開機自動啟動")
    except OSError:
        pass
    return done


def other_gauges() -> list[Any]:
    """TokenGauge windows running on their own (the old tokengauge.pyw, or 0.6.1's separate floating window)."""
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if "python" not in (proc.info.get("name") or "").lower() or proc.pid == os.getpid():
                continue
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if "tokengauge.pyw" in cmd or "agentdock.tools.tokengauge.gauge" in cmd:
                found.append(proc)
        except Exception:
            continue
    return found


class QuotaModel(QObject):
    """Collects every few minutes while the card is expanded (collapsed = resting: no requests, like TokenGauge)."""
    changed = Signal()
    _done = Signal(object)

    def __init__(self, data_dir: Path, notify=lambda _t: None) -> None:
        super().__init__()
        self.folder = data_dir / "tokengauge"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.notes = migrate(self.folder)
        gauge.configure(self.folder)
        self._plain_config()
        self.notify = notify
        self.results: dict[str, gauge.ProviderResult] = {}
        self.last: float | None = None
        self.busy = False
        self.active = True
        at, results = gauge.load_latest()
        if results:
            self.results, self.last = {r.key: r for r in results}, at
        self._done.connect(self._collected)
        self.timer = QTimer(self, interval=30_000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        (self.folder / "agentdock.json").unlink(missing_ok=True)  # 0.6.1's floating-window switch
        if self.notes:
            QTimer.singleShot(4000, lambda: notify("額度：" + "；".join(self.notes)))

    # ------------------------------------------------------------------ settings (TokenGauge's config.json)
    def config(self) -> dict:
        return gauge.load_config()

    def set_config(self, **values: Any) -> None:
        cfg = gauge.load_config()
        cfg.update(values)
        gauge.save_config(cfg)
        self.changed.emit()

    @staticmethod
    def _plain_config() -> None:
        """The card always shows what is left for every provider that has data (no menu switches for these),
        so undo choices made in the old TokenGauge menu."""
        cfg = gauge.load_config()
        want = {"show": "remaining", "providers": {k: True for k, _n, _f in gauge.PROVIDERS}}
        if any(cfg.get(k) != v for k, v in want.items()):
            cfg.update(want)
            gauge.save_config(cfg)

    # ------------------------------------------------------------------ data
    def visible_results(self) -> list[gauge.ProviderResult]:
        enabled = self.config().get("providers") or {}
        order = [k for k, _n, _f in gauge.PROVIDERS]
        shown = [r for r in self.results.values() if not r.missing and enabled.get(r.key, True)]
        return sorted(shown, key=lambda r: order.index(r.key) if r.key in order else 99)

    def poll_seconds(self) -> int:
        try:
            return max(60, int(float(self.config().get("poll_minutes", 5)) * 60))
        except Exception:
            return 300

    def set_active(self, active: bool) -> None:
        """Card expanded = active. Becoming active refreshes right away when the numbers are old."""
        self.active = active
        if active:
            self.tick()

    def tick(self) -> None:
        if self.active and (self.last is None or time.time() - self.last >= self.poll_seconds()):
            self.collect()
        else:
            self.changed.emit()  # countdowns move on

    def collect(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.changed.emit()
        cfg = gauge.load_config()

        def work() -> None:
            try:
                results = gauge.collect_all(cfg)
            except Exception:
                logging.exception("tokengauge collect")
                results = []
            self._done.emit(results)

        threading.Thread(target=work, daemon=True).start()

    def _collected(self, results: list) -> None:
        self.busy = False
        for r in results:
            prev = self.results.get(r.key)
            if r.error and not r.missing and prev and prev.accounts:
                prev.error, prev.stale = r.error, True  # keep the last numbers
                continue
            self.results[r.key] = r
        self.last = time.time()
        gauge.save_latest(list(self.results.values()), self.last)
        self.changed.emit()

    @staticmethod
    def close_other_gauges() -> int:
        procs = other_gauges()
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        return len(procs)
