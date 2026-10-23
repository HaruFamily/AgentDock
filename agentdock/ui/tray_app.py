"""AgentDock as a tray app: the conversation window, the tray icon and an optional quota float.

Replaces the floating card (still available with ui.json "shell": "card" for one version).
The Inbox is retired: conversations are answered natively in the window, and the broker only
answers old Inbox MCP entries with RETIRED until they are removed in Settings.
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from agentdock.agents import RETIRED_KINDS, AgentManager
from agentdock.broker import Broker
from agentdock.dispatch.projects import ProjectStore
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.store import FINAL
from agentdock.files import atomic_json, load_json
from agentdock.library import Library
from agentdock.paths import ROOT, data_dir
from agentdock.qa.store import QuestionStore
from agentdock.tools import ToolContext, discover
from agentdock.ui import theme, winutil
from agentdock.ui.agents_view import AgentsView
from agentdock.ui.app import Bus, Dock
from agentdock.ui.card import heart_icon
from agentdock.ui.chat_window import ChatWindow
from agentdock.ui.quota_view import QuotaFloat, ToolsPage
from agentdock.ui.winutil import GlobalHotkey


def attention(rows: dict) -> tuple[int, int, int]:
    """(approvals, questions, unread results) over all conversations."""
    approvals = sum(len(r.get('permissions') or {}) for r in rows.values())
    questions = sum(len(r.get('forms') or {}) for r in rows.values())
    unread = sum(not r['read'] and r['state'] in FINAL for r in rows.values())
    return approvals, questions, unread


def badge_icon(count: int, dot: bool) -> QIcon:
    base = heart_icon(64)
    if not count and not dot:
        return QIcon(base)
    pixmap = QPixmap(base)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.ALERT if count else theme.ACCENT_DEEP or '#d33'))
    size = 34 if count else 22
    rect = QRectF(64 - size, 0, size, size)
    painter.drawEllipse(rect)
    if count:
        painter.setPen(QColor('white'))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(24 if count < 10 else 18)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(count) if count < 100 else '99')
    painter.end()
    return QIcon(pixmap)


class TrayApp(Dock):
    def __init__(self, app: QApplication, background: bool) -> None:   # noqa: D401 - replaces Dock.__init__
        QObject.__init__(self)
        self.app = app
        self.data = data_dir()
        self.ui_file = self.data / 'ui.json'
        self.ui: dict[str, Any] = load_json(self.ui_file, {})
        self.bus = Bus()
        self.tray = None
        self.chat = None
        self.quota_float = None
        self._toast_key = ''
        self._toast_qid = None
        self.store = QuestionStore(self.data)
        self.store.subscribe(self._store_event)
        self.manager = AgentManager(self.data)
        self.manager.hidden_kinds = RETIRED_KINDS   # only the Agents the chat window talks to
        self.manager.rename_old_defaults()
        self.library = Library(ROOT, self.data)
        self.library.inbox_retired = True
        self.broker = Broker(self.data, self.store, on_show=lambda _id: self.bus.show_requested.emit(), inbox=False)
        local_theme = theme.load_private(self.data)
        from agentdock.ui import cute
        self.dresser = cute.install(app)   # dialogs, menus and tooltips follow the sweet look
        self.apply_theme(self.ui.get('theme', 'princess' if local_theme else 'clean'), save=False)
        self.dispatcher = Dispatcher(self.store.dispatch, notify=self.bus.notice.emit)
        self.projects = ProjectStore(self.data, emit=self.bus.changed.emit)
        self.agents = AgentsView(self.manager, self.library, ui_state=self.ui, save_state=self._save_ui, wide=True)
        self.quota = None
        try:
            from agentdock.tools.tokengauge import QuotaModel
            self.quota = QuotaModel(self.data, self.notify)
            self.quota.set_active(False)
        except Exception:
            logging.exception('quota')
        ctx = ToolContext(data_dir=self.data, store=self.store, notify=self.notify)
        extra = []
        for tool in discover():
            try:
                extra.append((tool.title, tool.create(ctx)))
            except Exception:
                logging.exception('tool %s failed', tool.title)
        self.chat = ChatWindow(self.store, self.dispatcher, self.projects, self._dispatch_targets,
                               notify=self.notify, ui_state=self.ui, save_state=self._save_ui)
        self.tools_page = ToolsPage(self.quota, bool(self.ui.get('quota_float')), extra)
        self.chat.add_page('tools', self.tools_page)
        self.chat.add_page('settings', self.agents)
        self._settings_pages()
        self.chat.page_changed.connect(self._page_changed)
        self.tools_page.float_toggled.connect(self.set_quota_float)
        if self.quota is not None:
            self.quota_float = QuotaFloat(self.quota)
            self.quota_float.moved.connect(self._float_moved)

        self._tray_sync = QTimer(self, singleShot=True, interval=150)
        self._tray_sync.timeout.connect(self._sync_tray)
        self.bus.changed.connect(self.chat.schedule_refresh)
        self.bus.changed.connect(self._tray_sync.start)
        self.bus.new_message.connect(self._attention)
        self.bus.new_question.connect(lambda _q: self._tray_sync.start())
        self.bus.show_requested.connect(self.summon)
        self.bus.notice.connect(self.notify)

        self._tray()
        self._hotkeys()
        self.set_quota_float(bool(self.ui.get('quota_float')), save=False)
        QTimer.singleShot(5000, self.agents.check_updates)
        QTimer.singleShot(3000, self._launcher_checks)
        QTimer.singleShot(6000, self._legacy_inbox_check)
        self._update_timer = QTimer(self, interval=6 * 3600 * 1000)
        self._update_timer.timeout.connect(self.agents.check_updates)
        self._update_timer.start()
        self.agents.refresh()
        self.broker.start()
        self.dispatcher.resume()
        self._sync_tray()
        if not background:
            self.open_chat()

    # ------------------------------------------------------------------ window
    def open_chat(self, key: str | None = None) -> None:
        """None: show the window as it was; '': a new conversation; a key: that conversation."""
        if key:
            self.chat.open_conversation(key)
        elif key == '':
            self.chat.show_new()
        if self.chat.isMinimized():
            self.chat.showNormal()
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        self._page_changed(self.chat.rail.current)

    def summon(self) -> None:
        self.open_chat()

    def toggle_card(self) -> None:
        if self.chat.isVisible() and self.chat.isActiveWindow():
            self.chat.close()
        else:
            self.open_chat()

    def _page_changed(self, name: str) -> None:
        self._quota_activity()
        if name == 'settings':
            QTimer.singleShot(0, self.agents.check_restarts)
            self.facts.refresh_versions([p.kind for p in self.manager.list()], self.agents.facts_ready.emit)

    # ------------------------------------------------------------------ settings pages
    def _settings_pages(self) -> None:
        """一般 and 外觀 of the settings page: the same switches as the tray menu, kept in step with it."""
        from agentdock.agent_info import AgentFacts
        from agentdock.ui.settings_page import GeneralPage, LookPage
        self.facts = AgentFacts(lambda kind: self.store.dispatch.options.get(kind, {}),
                                lambda key: getattr(self.quota, 'results', {}).get(key))
        self.agents.agent_info = self.facts
        items = [('notify', '需要處理時跳出通知', bool(self.ui.get('notify_attention', True)), self.set_notify)]
        if self.quota is not None:
            items.append(('quota_float', '額度浮動視窗', bool(self.ui.get('quota_float')), self.set_quota_float))
        if winutil.IS_WINDOWS:
            items.insert(0, ('autostart', '開機自動啟動', winutil.autostart_get() is not None, self._autostart_toggled))
        self.general_page = GeneralPage(items)
        self.agents.set_page('general', self.general_page)
        self.look_page = LookPage(theme.THEMES, theme.NAME)
        self.look_page.picked.connect(self.apply_theme)
        self.agents.set_page('look', self.look_page)

    def set_notify(self, on: bool) -> None:
        self.ui['notify_attention'] = on
        self._save_ui()
        if getattr(self, '_notify_action', None) is not None and self._notify_action.isChecked() != on:
            self._notify_action.blockSignals(True)
            self._notify_action.setChecked(on)
            self._notify_action.blockSignals(False)
        if getattr(self, 'general_page', None) is not None:
            self.general_page.set_on('notify', on)

    def _autostart_toggled(self, on: bool) -> None:
        self.set_autostart(on)
        on = winutil.autostart_get() is not None
        for widget in (getattr(self, '_autostart_action', None),):
            if widget is not None and widget.isChecked() != on:
                widget.blockSignals(True)
                widget.setChecked(on)
                widget.blockSignals(False)
        if getattr(self, 'general_page', None) is not None:
            self.general_page.set_on('autostart', on)

    def _quota_activity(self) -> None:
        if self.quota is not None:
            shown = (self.chat.isVisible() and self.chat.rail.current == 'tools') or \
                    bool(self.quota_float and self.quota_float.isVisible())
            if shown != self.quota.active:
                self.quota.set_active(shown)

    # ------------------------------------------------------------------ quota float
    def set_quota_float(self, on: bool, save: bool = True) -> None:
        if self.quota_float is None:
            return
        if on:
            pos = self.ui.get('quota_float_pos')
            if isinstance(pos, list) and len(pos) == 2:
                self.quota_float.move(QPoint(*pos))
            else:
                area = QApplication.primaryScreen().availableGeometry()
                self.quota_float.adjustSize()
                self.quota_float.move(area.right() - self.quota_float.width() - 16, area.bottom() - 240)
            self.quota_float.show()
        else:
            self.quota_float.hide()
        for widget in (self.tools_page.float_switch, getattr(self, '_float_action', None)):
            if widget is not None and widget.isChecked() != on:
                widget.blockSignals(True)
                widget.setChecked(on)
                widget.blockSignals(False)
        if getattr(self, 'general_page', None) is not None:
            self.general_page.set_on('quota_float', on)
        self._quota_activity()
        if save:
            self.ui['quota_float'] = on
            self._save_ui()

    def _float_moved(self, pos: QPoint) -> None:
        self.ui['quota_float_pos'] = [pos.x(), pos.y()]
        self._save_ui()

    # ------------------------------------------------------------------ tray and notices
    def _tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(badge_icon(0, False))
        menu = QMenu()
        menu.addAction(QAction('對話視窗', menu, triggered=lambda: self.open_chat()))
        menu.addAction(QAction('新對話', menu, triggered=lambda: self.open_chat('')))
        menu.addSeparator()
        self._float_action = QAction('額度浮動視窗', menu, checkable=True)
        self._float_action.setChecked(bool(self.ui.get('quota_float')))
        self._float_action.toggled.connect(self.set_quota_float)
        self._float_action.setEnabled(self.quota_float is not None)
        menu.addAction(self._float_action)
        notify = QAction('需要處理時跳出通知', menu, checkable=True)
        notify.setChecked(self.ui.get('notify_attention', True))
        notify.toggled.connect(self.set_notify)
        self._notify_action = notify
        menu.addAction(notify)
        looks = menu.addMenu('外觀')
        group = QActionGroup(looks)
        self._theme_actions = {}
        for name, config in theme.THEMES.items():
            action = QAction(config.get('label', name), looks, checkable=True)
            action.setChecked(name == theme.NAME)
            action.triggered.connect(lambda _c=False, n=name: self.apply_theme(n))
            group.addAction(action)
            looks.addAction(action)
            self._theme_actions[name] = action
        if winutil.IS_WINDOWS:
            auto = QAction('開機自動啟動', menu, checkable=True)
            auto.setChecked(winutil.autostart_get() is not None)
            auto.toggled.connect(self._autostart_toggled)
            self._autostart_action = auto
            menu.addAction(auto)
        menu.addSeparator()
        menu.addAction(QAction('結束 AgentDock', menu, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.toggle_card()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(self._toast_clicked)
        self.tray.setToolTip('AgentDock')
        self.tray.show()
        self._tray_menu = menu

    def _sync_tray(self) -> None:
        approvals, questions, unread = attention(self.store.dispatch.rows)
        if self.tray is None:
            return
        self.tray.setIcon(badge_icon(approvals + questions, bool(unread)))
        parts = [f'{label} {n}' for label, n in (('待授權', approvals), ('待回答', questions), ('新回覆', unread)) if n]
        self.tray.setToolTip('AgentDock' + ('：' + ' · '.join(parts) if parts else ''))

    def _attention(self, row: dict) -> None:
        """A new approval or question in a conversation; results only update the badge."""
        self._tray_sync.start()
        if not row.get('client') or not (row.get('permissions') or row.get('forms')):
            return
        key = f"{row['client']}:{row['session_id']}"
        if self.chat.isVisible() and self.chat.isActiveWindow() and self.chat.current_key == key:
            return
        if self.tray is None or not self.ui.get('notify_attention', True):
            return
        what = '待授權' if row.get('permissions') else '待回答'
        self._toast_key = key
        self.tray.showMessage(f"{row['source']}：{what}", row['title'], QSystemTrayIcon.MessageIcon.Information, 8000)

    def _toast_clicked(self) -> None:
        key, self._toast_key = self._toast_key, ''
        self.open_chat(key or None)

    def notify(self, text: str) -> None:
        self._toast_key = ''
        if self.tray:
            self.tray.showMessage('AgentDock', text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _legacy_inbox_check(self) -> None:
        """Tell once per start which Agents still carry the retired Inbox MCP."""
        try:
            names = [r['name'] for r in self.manager.inspect() if any(s.get('qai') for s in r['servers'])]
        except Exception:
            logging.exception('legacy inbox check')
            return
        if names:
            self.notify('、'.join(names) + ' 還掛著已停用的 Inbox：到「設定」按「全部更新」，預覽後套用即可移除。')

    # ------------------------------------------------------------------ look
    def apply_theme(self, name: str, save: bool = True) -> None:
        Dock.apply_theme(self, name, save=False)
        if self.chat is not None:
            self.chat.refresh_theme()
        if self.quota_float is not None:
            self.quota_float.restyle()
        for key, action in getattr(self, '_theme_actions', {}).items():
            action.setChecked(key == theme.NAME)
        if getattr(self, 'look_page', None) is not None:
            self.look_page.set_current(theme.NAME)
            self.agents.restyle()
        if self.tray is not None:
            self._sync_tray()
        if save:
            self.ui['theme'] = theme.NAME
            self._save_ui()

    def _hotkeys(self) -> None:
        self.hotkey = GlobalHotkey(self.summon)
        self.app.installNativeEventFilter(self.hotkey)
        if winutil.IS_WINDOWS and not self.hotkey.register(self.chat, 'Q', ctrl=True, alt=True):
            self.notify('Ctrl+Alt+Q 已被其他程式占用，可從系統匣圖示打開 AgentDock。')
        self.appearance_hotkey = GlobalHotkey(lambda: self.apply_theme('clean'), hotkey_id=0xAD02)
        self.app.installNativeEventFilter(self.appearance_hotkey)
        if winutil.IS_WINDOWS and not self.appearance_hotkey.register(self.chat, 'H', ctrl=True, alt=True):
            self.notify('Ctrl+Alt+H 已被其他程式占用，可從系統匣選單切換極簡外觀。')

    @staticmethod
    def autostart_command() -> str:
        # Starting with Windows opens quietly in the tray.
        return f'"{ROOT / "AgentDock.exe"}" --background'

    # ------------------------------------------------------------------ persistence
    def _save_ui(self) -> None:
        try:
            atomic_json(self.ui_file, self.ui)
        except OSError:
            logging.exception('save ui')

    def quit(self) -> None:
        self.chat.conversation.composer.save_draft()
        self.chat.remember()
        self.chat.hide()
        if self.quota_float is not None:
            self.quota_float.hide()
        self.dispatcher.close()
        self.store.flush_reads()
        for hotkey in (getattr(self, 'hotkey', None), getattr(self, 'appearance_hotkey', None)):
            if hotkey:
                hotkey.unregister()
        self._save_ui()
        self.broker.close()
        try:
            import json
            endpoint = json.loads((self.data / 'endpoint.json').read_text(encoding='utf-8'))
            if endpoint.get('token') == self.broker.token:
                (self.data / 'endpoint.json').unlink()
        except Exception:
            pass
        if self.tray:
            self.tray.hide()
        self.app.quit()
