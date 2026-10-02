"""Entry point: floating ball + panel + tray + broker, single instance per data directory."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from PySide6.QtCore import QLockFile, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from agentdock.agents import AgentManager
from agentdock.library import Library
from agentdock.broker import Broker
from agentdock.files import atomic_json, load_json
from agentdock.paths import ROOT, data_dir
from agentdock.qa.store import QuestionStore
from agentdock.tools import ToolContext, discover
from agentdock.ui import theme
from agentdock.ui.agents_view import AgentsView
from agentdock.ui import winutil
from agentdock.ui.ball import FloatingBall
from agentdock.ui.winutil import GlobalHotkey
from agentdock.ui.panel import Panel
from agentdock.ui.qa_view import QaView


class Bus(QObject):
    changed = Signal()
    new_question = Signal(dict)
    show_requested = Signal()


class Dock(QObject):
    def __init__(self, app: QApplication, background: bool) -> None:
        super().__init__()
        self.app = app
        self.data = data_dir()
        self.ui_file = self.data / "ui.json"
        self.ui: dict[str, Any] = load_json(self.ui_file, {})
        self.bus = Bus()
        self.store = QuestionStore(self.data)
        self.store.subscribe(self._store_event)
        self.manager = AgentManager(self.data)
        self.library = Library(ROOT, self.data)
        self.broker = Broker(self.data, self.store, on_show=lambda _id: self.bus.show_requested.emit())

        self.ball = FloatingBall()
        self.panel = Panel()
        self.qa = QaView(self.store)
        self.agents = AgentsView(self.manager, self.library, ui_state=self.ui, save_state=self._save_ui)
        self.panel.tabs.addTab(self.agents, "Agent")
        self.panel.tabs.addTab(self.qa, "問答")
        ctx = ToolContext(data_dir=self.data, store=self.store, notify=self.notify)
        for tool in discover():
            try:
                self.panel.tabs.addTab(tool.create(ctx), tool.title)
            except Exception:
                logging.exception("tool %s failed", tool.title)
        self.panel.tabs.currentChanged.connect(self._tab_changed)

        self.ball.clicked.connect(self.toggle_panel)
        self.ball.moved.connect(lambda _p: self._save_ui())
        self.ball.quit_requested.connect(self.quit)
        self.panel.geometry_changed.connect(self._save_ui)
        self.qa.pending_changed.connect(self._pending)
        self.bus.changed.connect(self.qa.refresh)
        self.bus.new_question.connect(self._on_new_question)
        self.bus.show_requested.connect(lambda: self.open_panel(activate=True))

        self._tray()
        self._restore_ui()
        self.qa.refresh()
        self.ball.show()
        self.broker.start()
        self._keep_visible()
        # Refresh the "Agent waiting / reconnecting" state of the open question.
        # Only refreshes the "Agent waiting / reconnecting" label of an open question, and only while visible.
        self._tick = QTimer(self, interval=3000)
        self._tick.timeout.connect(self._tick_waiting)
        self._tick.start()
        # GitHub update check: once a day per MCP, in the background, a few seconds after start.
        QTimer.singleShot(5000, self.agents.check_updates)
        self._update_timer = QTimer(self, interval=6 * 3600 * 1000)
        self._update_timer.timeout.connect(self.agents.check_updates)
        self._update_timer.start()
        if not background:
            self.open_panel(activate=True)

    # ------------------------------------------------------------------ events
    def _store_event(self, event: str, question: dict | None) -> None:
        # Called from the broker thread: hop to the UI thread via queued signals.
        if event == "new-question" and question:
            self.bus.new_question.emit(question)
        else:
            self.bus.changed.emit()

    def _on_new_question(self, q: dict) -> None:
        self.qa.refresh()
        if self.ui.get("auto_expand", True) and not self.panel.isVisible():
            self.open_panel(activate=False)
            self.panel.tabs.setCurrentWidget(self.qa)
            self.qa.show_first_pending()
        if self.tray and not self.panel.isVisible():
            self.tray.showMessage("AgentDock", f"{q['source']}：{q['question'][:60]}", QSystemTrayIcon.MessageIcon.Information, 4000)

    def _pending(self, count: int) -> None:
        self.ball.set_pending(count)
        self.panel.tabs.setTabText(self.panel.tabs.indexOf(self.qa), f"問答 ({count})" if count else "問答")
        if self.tray:
            self.tray.setToolTip(f"AgentDock：{count} 題待回答" if count else "AgentDock")

    def _tab_changed(self, index: int) -> None:
        widget = self.panel.tabs.widget(index)
        if widget is self.agents:
            widget.refresh()

    def refresh_agents(self) -> None:
        self.agents.refresh()

    def notify(self, text: str) -> None:
        if self.tray:
            self.tray.showMessage("AgentDock", text, QSystemTrayIcon.MessageIcon.Information, 4000)

    # ------------------------------------------------------------------ windows
    def _tick_waiting(self) -> None:
        if self.panel.isVisible() and self.panel.tabs.currentWidget() is self.qa:
            self.qa.tick()

    # ------------------------------------------------------------------ stay findable
    def _keep_visible(self) -> None:
        """Event-driven only: re-assert topmost when focus/app state changes, re-place after screen changes,
        and Ctrl+Alt+Q to bring everything back."""
        self.app.applicationStateChanged.connect(lambda _s: self._raise_all())
        self.app.focusWindowChanged.connect(lambda _w: self._raise_all())
        self.app.screenAdded.connect(self._watch_screen)
        self.app.screenRemoved.connect(lambda _s: QTimer.singleShot(400, self._reposition))
        self.app.primaryScreenChanged.connect(lambda _s: QTimer.singleShot(400, self._reposition))
        for screen in self.app.screens():
            self._watch_screen(screen)
        self.hotkey = GlobalHotkey(self.summon)
        self.app.installNativeEventFilter(self.hotkey)
        if winutil.IS_WINDOWS and not self.hotkey.register(self.ball, "Q", ctrl=True, alt=True):
            self.notify("Ctrl+Alt+Q 已被其他程式占用，可從系統匣圖示叫出 AgentDock。")

    def _watch_screen(self, screen) -> None:
        screen.availableGeometryChanged.connect(lambda _g: QTimer.singleShot(400, self._reposition))
        QTimer.singleShot(400, self._reposition)

    def _raise_all(self) -> None:
        # Not while a dialog/menu of ours is open: raising the panel would cover it.
        if self.app.activeModalWidget() or self.app.activePopupWidget():
            return
        winutil.force_topmost(self.panel)
        winutil.force_topmost(self.ball)

    def _reposition(self) -> None:
        """After a monitor is unplugged / resolution changes: pull the ball (and panel) back on screen."""
        self.ball.keep_on_screen()
        if self.panel.isVisible():
            screen = QApplication.screenAt(self.panel.frameGeometry().center())
            if screen is None:
                self.panel.place_near(self.ball.frameGeometry())
        self._raise_all()
        self._save_ui()

    def summon(self) -> None:
        """Ctrl+Alt+Q / tray: show the ball on screen and open the panel in front."""
        self.ball.show()
        self.ball.keep_on_screen()
        self.open_panel(activate=True)
        self._raise_all()

    def open_panel(self, activate: bool) -> None:
        if not self.panel.isVisible():
            self.panel.place_near(self.ball.frameGeometry())
            QTimer.singleShot(0, self.agents.check_restarts)
        self.panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
        self.panel.show()
        winutil.force_topmost(self.panel)
        if activate:
            self.panel.raise_()
            self.panel.activateWindow()

    def toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            if self.ball.pending:
                self.panel.tabs.setCurrentWidget(self.qa)
            self.open_panel(activate=True)

    def _tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(self.ball.grab())
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon)
        menu = QMenu()
        menu.addAction(QAction("展開面板", menu, triggered=lambda: self.open_panel(activate=True)))
        show_ball = QAction("找回小球與面板（Ctrl+Alt+Q）", menu, triggered=self.summon)
        menu.addAction(show_ball)
        menu.addSeparator()
        menu.addAction(QAction("結束 AgentDock", menu, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.toggle_panel() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip("AgentDock")
        self.tray.show()
        self._tray_menu = menu

    # ------------------------------------------------------------------ persistence
    def _restore_ui(self) -> None:
        area = QApplication.primaryScreen().availableGeometry()
        pos = self.ui.get("ball")
        if pos:
            self.ball.move(QPoint(*pos))
        else:
            self.ball.move(area.right() - self.ball.width() - 8, area.top() + area.height() // 3)
        self.ball.keep_on_screen()
        if size := self.ui.get("panel_size"):
            self.panel.resize(*size)

    def _save_ui(self) -> None:
        self.ui.update({"ball": [self.ball.x(), self.ball.y()], "panel_size": [self.panel.width(), self.panel.height()]})
        self.ui.pop("pinned", None)
        try:
            atomic_json(self.ui_file, self.ui)
        except OSError:
            logging.exception("save ui")

    def quit(self) -> None:
        if getattr(self, "hotkey", None):
            self.hotkey.unregister()
        self._save_ui()
        self.broker.close()
        try:
            endpoint = json.loads((self.data / "endpoint.json").read_text(encoding="utf-8"))
            if endpoint.get("token") == self.broker.token:
                (self.data / "endpoint.json").unlink()
        except Exception:
            pass
        if self.tray:
            self.tray.hide()
        self.app.quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentdock")
    parser.add_argument("--background", action="store_true", help="start as the floating ball only")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    data = data_dir()
    logging.basicConfig(filename=str(data / "agentdock.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    from agentdock.client import _find

    lock = QLockFile(str(data / "agentdock.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        running = _find()
        if running:
            try:
                running.request("/show", {})
            except Exception:
                pass
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("AgentDock")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLE)
    font = QFont("Microsoft JhengHei UI")
    font.setPointSizeF(9.5)
    app.setFont(font)
    try:
        dock = Dock(app, args.background)
    except Exception as e:
        logging.exception("startup failed")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "AgentDock 無法啟動", f"{e}\n\n詳細記錄：{data / 'agentdock.log'}")
        return 1
    app.aboutToQuit.connect(lambda: lock.unlock())
    dock_ref = dock  # keep alive
    code = app.exec()
    del dock_ref
    return code
