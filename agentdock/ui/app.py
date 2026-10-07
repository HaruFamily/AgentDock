"""Entry point: the floating card + tray + broker, single instance per data directory."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from PySide6.QtCore import QLockFile, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from agentdock.agents import AgentManager
from agentdock.library import Library
from agentdock.broker import Broker
from agentdock.files import atomic_json, load_json
from agentdock.paths import ROOT, data_dir, venv_dir
from agentdock.qa.store import QuestionStore
from agentdock.tools import ToolContext, discover
from agentdock.ui import theme
from agentdock.ui.agents_view import AgentsView
from agentdock.ui import winutil
from agentdock.ui.card import MARGIN, FloatingCard, heart_icon
from agentdock.ui.winutil import GlobalHotkey
from agentdock.ui.inbox_view import InboxView
from agentdock.ui import fonts


class Bus(QObject):
    changed = Signal()
    new_question = Signal(dict)
    new_message = Signal(dict)
    show_requested = Signal()


class LauncherIcon(FloatingCard):
    """Reuse the icon drawing and dragging without ever resizing into a card."""

    def set_mode(self, mode: str, auto: bool = False) -> None:
        if mode in ("card", "pill"):
            self.mode_changed.emit("card")
        else:
            super().set_mode(mode, auto)


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
        self.tray = None
        self._toast_qid: str | None = None
        local_theme = theme.load_private(self.data)
        self.apply_theme(self.ui.get("theme", "princess" if local_theme else "clean"), save=False)

        # One window: the floating card (heart / pill / card with 額度・問答・設定 pages). Kept as self.ball.
        self.ball = FloatingCard()
        # A separate, always visible launcher; its card pages are never opened.
        self.icon = LauncherIcon()
        self.icon.set_mode("heart")
        self.ball.set_mode("heart")
        self.icon.mode_changed.connect(self._icon_clicked)
        self.icon.moved.connect(lambda _p: self._save_ui())
        self.icon.quit_requested.connect(self.quit)
        self.icon.theme_requested.connect(self.apply_theme)
        self.qa = InboxView(self.store)
        self.agents = AgentsView(self.manager, self.library, ui_state=self.ui, save_state=self._save_ui)
        self.ball.set_qa(self.qa)
        self.ball.set_settings(self.agents)
        ctx = ToolContext(data_dir=self.data, store=self.store, notify=self.notify)
        for tool in discover():
            try:
                self.ball.add_tool_page(f"tool:{tool.title}", tool.title, tool.create(ctx))
            except Exception:
                logging.exception("tool %s failed", tool.title)

        self.ball.moved.connect(lambda _p: self._save_ui())
        self.ball.quit_requested.connect(self.quit)
        self.ball.mode_changed.connect(self._mode_changed)
        self.ball.page_changed.connect(self._page_changed)
        self.ball.theme_requested.connect(self.apply_theme)
        self.ball.bg_opacity_changed.connect(self._bg_opacity)
        self.ball.autostart_requested.connect(self.set_autostart)
        if winutil.IS_WINDOWS:
            self.ball.autostart_state = lambda: winutil.autostart_get() is not None
        self.ball.open_question.connect(self._open_question)
        self.ball.bubble_answer.connect(self._bubble_answer)
        self.qa.pending_items.connect(self.ball.set_questions)
        self.qa.pending_changed.connect(self._pending)
        self.qa.answered.connect(self.ball.celebrate)
        self.agents.summary_changed.connect(self.ball.set_agent_summary)
        self._chat_refresh = QTimer(self, singleShot=True, interval=100)
        self._chat_refresh.timeout.connect(self.qa.refresh)
        self.bus.changed.connect(lambda: self._chat_refresh.start() if not self._chat_refresh.isActive() else None)
        self.bus.new_question.connect(self._on_new_question)
        self.bus.new_message.connect(self._on_new_message)
        self.bus.show_requested.connect(self.summon)

        try:
            from agentdock.tools.tokengauge import QuotaModel
            self.quota = QuotaModel(self.data, self.notify)
            self.ball.set_quota(self.quota)
            QTimer.singleShot(2500, self._close_other_gauges)
        except Exception:
            logging.exception("quota")
        self.agents.refresh()
        self._summary_timer = QTimer(self, interval=10 * 60 * 1000)
        self._summary_timer.timeout.connect(lambda: None if self.ball.page == "settings" else self.agents.refresh())
        self._summary_timer.start()
        self._tray()
        self._restore_ui()
        self.qa.refresh()
        self.ball.show()
        self.icon.show()
        self._sync_icon()
        self.broker.start()
        self._keep_visible()
        self._tick = QTimer(self, interval=3000)   # "Agent waiting / reconnecting" label of the open question
        self._tick.timeout.connect(self._tick_waiting)
        self._tick.start()
        self._fullscreen = QTimer(self, interval=2000)  # rest as a heart while a full-screen app is in front
        self._fullscreen.timeout.connect(self._check_fullscreen)
        if winutil.IS_WINDOWS:
            self._fullscreen.start()
        QTimer.singleShot(5000, self.agents.check_updates)
        QTimer.singleShot(3000, self._launcher_checks)
        self._update_timer = QTimer(self, interval=6 * 3600 * 1000)
        self._update_timer.timeout.connect(self.agents.check_updates)
        self._update_timer.start()

    # ------------------------------------------------------------------ look
    def apply_theme(self, name: str, save: bool = True) -> None:
        theme.apply(name)
        if theme.CUTE and (source := getattr(theme.PRIVATE, "FONT_SOURCE", None)):
            family = fonts.ensure(self.data, lambda path: self._font_ready(path), source)
            if family:
                self._use_font(family)
        else:
            self._use_font(None)
        self.app.setStyleSheet(theme.STYLE)
        icon = QIcon(heart_icon(64))
        self.app.setWindowIcon(icon)
        if self.tray:
            self.tray.setIcon(icon)
        if hasattr(self, "ball"):
            for window in (self.ball, self.icon):
                if window.bubble is not None:
                    window.bubble.close()
            self.ball.restyle()
            self.icon.restyle()
            self.qa.refresh()
        if save:
            self.ui["theme"] = theme.NAME
            self._save_ui()

    def _use_font(self, family: str | None) -> None:
        font = QFont(family or "Microsoft JhengHei UI")
        font.setPointSizeF(9.5)
        self.app.setFont(font)
        if family:
            cfg = theme.THEMES["princess"]
            if not cfg["FONT"].startswith(f'"{family}"'):
                cfg["FONT"] = f'"{family}", ' + cfg["FONT"]
            theme.apply(theme.NAME)

    def _font_ready(self, path: str) -> None:
        family = fonts.family_for(path)
        if family and theme.CUTE:
            self._use_font(family)
            self.app.setStyleSheet(theme.STYLE)
            self.ball.restyle()

    # ------------------------------------------------------------------ events
    def _store_event(self, event: str, question: dict | None) -> None:
        # Called from the broker thread: hop to the UI thread via queued signals.
        if event == "new-question" and question:
            self.bus.new_question.emit(question)
        elif event == "new-message" and question:
            self.bus.new_message.emit(question)
        else:
            self.bus.changed.emit()

    def _on_new_message(self, message: dict) -> None:
        self.qa.refresh()

    def _on_new_question(self, q: dict) -> None:
        self.qa.refresh()

    def _toast_question(self, q: dict) -> None:
        """Compatibility hook: chat attention is shown on the launcher only."""
        return

    def _toast_clicked(self) -> None:
        work = getattr(self, "_toast_work", None)
        self._toast_work = None
        if work:
            self.summon()
            self.ball.set_page("qa")
            self.qa.open_conversation(*work)
            return
        qid, self._toast_qid = getattr(self, "_toast_qid", None), None
        if not qid:
            return
        self.summon()
        self.ball.set_page("qa")
        self.qa.open_question(qid)

    def set_question_toast(self, on: bool) -> None:
        self.ui["question_toast"] = on
        self._save_ui()

    def _bubble_answer(self, qid: str, oid: str) -> None:
        try:
            self.store.answer(qid, {"selected": [oid], "text": "", "notes": {}, "attachment_ids": []})
            self.ball.celebrate()
        except Exception as e:
            self.notify(f"回答失敗：{e}")
            self.ball.set_mode("card")
            self.ball.set_page("qa")
            self.qa.open_question(qid)
        self.qa.refresh()

    def _pending(self, count: int) -> None:
        self.ball.set_pending(count)
        unread = sum(w["state"] == "unread" for w in self.store.conversation_summaries())
        self.icon.set_attention(bool(count or unread))
        self.ball.set_attention(bool(count or unread))
        if self.tray:
            self.tray.setToolTip(f"AgentDock：{count} 題待回答，{unread} 個未讀對話" if count or unread else "AgentDock")

    def _page_changed(self, page: str) -> None:
        self.ui["card_page"] = page
        if page == "settings":
            self.agents.refresh()
            QTimer.singleShot(0, self.agents.check_restarts)
        self._save_ui()

    def _mode_changed(self, mode: str) -> None:
        self._sync_icon()
        if not self.ball.rest_from:   # automatic rests are not remembered
            self.ui["card_mode"] = mode
        self._save_ui()

    def _icon_clicked(self, mode: str) -> None:
        if mode == "card":
            self.icon.set_mode("heart")
            self.toggle_card()

    def _sync_icon(self) -> None:
        # The original small window remains the launcher while the card is hidden.
        self.icon.setVisible(self.ball.mode == "card")
        if self.ball.mode == "heart":
            self.ball.move(self.icon.pos())
        elif self.ball.geometry().intersects(self.icon.geometry()):
            self.ball.move(self.icon.x() - self.ball.width(), self.icon.y() - self.ball.height() + self.icon.height())
            self.ball.keep_on_screen()
        self.icon.setToolTip("點一下隱藏浮動視窗")

    def _open_question(self, qid: str) -> None:
        if qid:
            self.qa.open_question(qid)
        else:
            self.qa.show_first_pending()

    def _check_fullscreen(self) -> None:
        if self.ui.get("rest_in_fullscreen", True):
            self.ball.rest(winutil.foreground_is_fullscreen())

    def _close_other_gauges(self) -> None:
        """The card shows the quota: close separately running TokenGauge windows so there is one."""
        if self.quota.close_other_gauges():
            self.notify("額度已整合到浮動視窗，已關閉另外開著的 TokenGauge。")

    def refresh_agents(self) -> None:
        self.agents.refresh()

    def notify(self, text: str) -> None:
        self._toast_qid = None   # a plain notice: clicking it opens nothing
        if self.tray:
            self.tray.showMessage("AgentDock", text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _tick_waiting(self) -> None:
        if self.ball.isVisible() and self.qa.isVisible():
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
        self.appearance_hotkey = GlobalHotkey(lambda: self.apply_theme("clean"), hotkey_id=0xAD02)
        self.app.installNativeEventFilter(self.appearance_hotkey)
        if winutil.IS_WINDOWS and not self.appearance_hotkey.register(self.ball, "H", ctrl=True, alt=True):
            self.notify("Ctrl+Alt+H 已被其他程式占用，可從系統匣選單切換極簡外觀。")

    def _watch_screen(self, screen) -> None:
        screen.availableGeometryChanged.connect(lambda _g: QTimer.singleShot(400, self._reposition))
        QTimer.singleShot(400, self._reposition)

    def _raise_all(self) -> None:
        if self.app.activeModalWidget() or self.app.activePopupWidget():
            return
        winutil.force_topmost(self.ball)
        if self.icon.isVisible():
            winutil.force_topmost(self.icon)

    def _reposition(self) -> None:
        """After a monitor is unplugged / resolution changes: pull the card back on screen."""
        self.ball.keep_on_screen()
        self.icon.keep_on_screen()
        self._raise_all()
        self._save_ui()

    def summon(self) -> None:
        """Ctrl+Alt+Q / tray / a second start: bring the card back, opened, in front."""
        self.ball.rest_from = None
        self.ball.show()
        self.ball.set_mode("card")
        if self.ball.pending:
            self.ball.set_page("qa")
            self.qa.show_first_pending()
        self.ball.keep_on_screen()

        self.ball.raise_()
        self.ball.activateWindow()
        self._raise_all()

    def toggle_card(self) -> None:
        self.ball.set_mode("heart" if self.ball.mode == "card" else "card")

    def _tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(heart_icon(64))
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon)
        menu = QMenu()
        menu.addAction(QAction("展開浮動視窗", menu, triggered=self.summon))
        menu.addAction(QAction("找回浮動視窗（Ctrl+Alt+Q）", menu, triggered=self.summon))
        menu.addAction(QAction("切換極簡外觀（Ctrl+Alt+H）", menu, triggered=lambda: self.apply_theme("clean")))
        menu.addSeparator()
        menu.addAction(QAction("結束 AgentDock", menu, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.toggle_card() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(self._toast_clicked)
        self.tray.setToolTip("AgentDock")
        self.tray.show()
        self._tray_menu = menu

    # ------------------------------------------------------------------ launcher
    SHORTCUT_NAME = "AgentDock.lnk"

    def _launcher_checks(self) -> None:
        """Portable: nothing outside the AgentDock folder. Removes the Start-menu shortcut that 0.6.1 created,
        and says when uv.lock changed so AgentDock.exe re-prepares runtime\\ on the next start."""
        if not winutil.IS_WINDOWS:
            return
        marker = self.data / "launcher.json"
        if marker.exists():
            try:
                (winutil.start_menu_dir() / self.SHORTCUT_NAME).unlink(missing_ok=True)
                marker.unlink()
            except OSError:
                logging.exception("remove start menu shortcut")
        self._drop_old_venv()
        self._fix_autostart()
        synced = venv_dir() / "agentdock-synced.lock"
        lock = ROOT / "uv.lock"
        try:
            stale = synced.exists() and lock.exists() and synced.read_bytes() != lock.read_bytes()
        except OSError:
            stale = False
        if stale:
            self.notify("相依套件有更新：結束 AgentDock 後再開一次 AgentDock.exe，會自動更新。")

    @staticmethod
    def autostart_command() -> str:
        return f'"{ROOT / "AgentDock.exe"}"'

    def set_autostart(self, on: bool) -> None:
        """The one exception to "nothing outside the folder": a HKCU Run value, only while the user has it ticked."""
        try:
            winutil.autostart_set(self.autostart_command() if on else None)
            self.notify("開機時會自動開啟 AgentDock。" if on else "已取消開機自動啟動。")
        except OSError as e:
            logging.exception("autostart")
            self.notify(f"設定開機自動啟動失敗：{e}")

    def _fix_autostart(self) -> None:
        """The folder moved (or was copied to another PC): point the Run value at this AgentDock.exe."""
        current = winutil.autostart_get()
        if current and current != self.autostart_command() and (ROOT / "AgentDock.exe").exists():
            try:
                winutil.autostart_set(self.autostart_command())
            except OSError:
                logging.exception("autostart path")

    def _bg_opacity(self, value: float) -> None:
        self.ui["bg_opacity"] = value
        self._save_ui()

    def _drop_old_venv(self) -> None:
        """After moving to runtime\\venv: delete the old .venv once no Agent config points into it any more."""
        old = ROOT / ".venv"
        if venv_dir() == old or not old.exists():
            return
        needle = str(old).lower().replace("\\", "/")
        rows = self.manager.inspect()
        if any(needle in json.dumps(s["config"]).lower().replace("\\\\", "/") for r in rows for s in r["servers"]):
            return
        import shutil
        import threading
        threading.Thread(target=lambda: shutil.rmtree(old, ignore_errors=True), daemon=True).start()

    # ------------------------------------------------------------------ persistence
    def _restore_ui(self) -> None:
        area = QApplication.primaryScreen().availableGeometry()
        self.icon.move(QPoint(
            area.right() + 1 - self.icon.width() + MARGIN,
            area.top() + (area.height() - self.icon.height()) // 2))
        self.icon.keep_on_screen()
        # Page restoration emits save signals, so place the resting window first.
        self.ball.move(self.icon.pos())
        mode = "heart"  # Each new launch starts quietly, even if the last session was expanded.
        try:
            self.ball.set_bg_opacity(float(self.ui.get("bg_opacity", 1.0)))
        except (TypeError, ValueError):
            pass
        if size := self.ui.get("card_size"):
            self.ball.card_size = tuple(size)
            if self.ball.mode == "card":
                self.ball._apply_mode()
        self.ball.set_page(self.ui.get("card_page", "quota"))
        self.ball.set_mode(mode)
        self.ball.move(self.icon.pos())
        self.ball.keep_on_screen()

    def _save_ui(self) -> None:
        if not hasattr(self, "ball"):
            return
        self.ui.update({"ball": [self.ball.x(), self.ball.y()], "card_size": list(self.ball.card_size)})
        if self.ball.mode == "heart":
            self.icon.move(self.ball.pos())
        self.ui["icon_pos"] = [self.icon.x(), self.icon.y()]
        for old in ("pinned", "panel_size", "card_collapsed"):
            self.ui.pop(old, None)
        try:
            atomic_json(self.ui_file, self.ui)
        except OSError:
            logging.exception("save ui")

    def quit(self) -> None:
        self.qa._flush()
        self.store.flush_reads()
        if getattr(self, "hotkey", None):
            self.hotkey.unregister()
        if getattr(self, "appearance_hotkey", None):
            self.appearance_hotkey.unregister()
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
