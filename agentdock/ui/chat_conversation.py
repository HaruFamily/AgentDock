"""One open conversation: header, transcript (web page or Qt widgets), requests, message box."""
from __future__ import annotations


import difflib
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from agentdock.dispatch.adapter import EFFORT_LABELS
from agentdock.dispatch.projects import UNSORTED
from agentdock.dispatch.store import owner_for, work_id
from agentdock.dispatch.transcript import Transcript, tool_summary
from agentdock.ui import theme
from agentdock.ui.dispatch_view import FormBox, status_text, text_box
from agentdock.ui.qa_view import button, label
from agentdock.ui import web_transcript
from agentdock.ui.question_text import QuestionText
from agentdock.ui.web_transcript import tool_name
from agentdock.ui.widgets import ElidedLabel, icon_button
from agentdock.ui.chat_common import COLUMN, context_text, hidden, icon, mini_button, theme_color
from agentdock.ui.chat_messages import CodeBlock, MessageWidget, ReasoningRow, RichText, ToolRow
from agentdock.ui.chat_composer import Composer


# --------------------------------------------------------------------------- conversation page
class Fade(QWidget):
    """A soft edge where messages scroll out above the message box, instead of a hard cut."""
    HEIGHT = 24

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QLinearGradient
        canvas = theme_color('CANVAS')
        clear = theme_color('CANVAS')
        clear.setAlpha(0)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, clear)
        gradient.setColorAt(1, canvas)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)
        painter.end()


class ConversationView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.key = ''
        self.transcript = Transcript()
        self.widgets: dict[str, MessageWidget] = {}
        self.open_tools: set[str] = set()   # folded tool runs the user opened, by their first tool's key
        self.open_process: set[str] = set()   # finished answers whose steps the user opened, by first item id
        self.older: str | None = None
        self.loading = False
        self._pending_items: set[str] = set()
        self._flush = QTimer(self, singleShot=True, interval=80)   # streaming text redraws at most ~12/s
        self._flush.timeout.connect(self._flush_items)
        self._group = QTimer(self, singleShot=True, interval=0)
        self._group.timeout.connect(self._regroup)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()   # the title sits at the window's left edge, not over the reading column
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 12, 16, 8)
        head.setSpacing(2)
        self.title = ElidedLabel('')
        self.title.setObjectName('ConvTitle')
        head.addWidget(self.title, 1)
        self.project_chip = QPushButton()
        self.project_chip.setObjectName('ChatChip')
        self.project_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project_chip.clicked.connect(self._open_project)
        self.project_chip.hide()   # the list already shows the project
        self.context_chip = QLabel()
        self.context_chip.setObjectName('ChatChip')
        self.context_chip.hide()
        self.agent_chip = QLabel()
        self.agent_chip.setObjectName('ChatChip')
        self.agent_chip.hide()   # the model chip already says who answers
        self.more_button = QPushButton()
        self.more_button.setObjectName('MiniButton')
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._more_menu)
        head.addWidget(self.more_button)
        outer.addWidget(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        column = QHBoxLayout(body)
        column.setContentsMargins(24, 16, 24, 16)
        column.addStretch(1)
        self.content = QWidget()
        self.content.setMaximumWidth(COLUMN)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.items = QVBoxLayout(self.content)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.items.setSpacing(0)   # each message keeps its own gap, so the steps of one answer can touch
        self.more = button('載入更早的訊息', flat=True)
        self.more.clicked.connect(self.load_older)
        self.items.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label('', muted=True)
        self.items.addWidget(self.status)
        # Permission and question cards: under the messages here; above the message box with the web page.
        self.request_box = QWidget()
        self.requests = QVBoxLayout(self.request_box)
        self.requests.setContentsMargins(0, 10, 0, 0)
        self.requests.setSpacing(10)
        self.items.addWidget(self.request_box)
        self.items.addStretch(1)
        column.addWidget(self.content, 100)
        column.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        # The web page (web_transcript) draws the conversation instead when the app enabled it.
        self.web = None
        self.use_web = False
        self.request_area = QScrollArea()
        self.request_area.setWidgetResizable(True)
        self.request_area.setFrameShape(QFrame.Shape.NoFrame)
        self.request_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self.request_row = QHBoxLayout(holder)
        self.request_row.setContentsMargins(24, 0, 24, 0)
        self.request_row.addStretch(1)
        self.request_row.addStretch(1)
        self.request_area.setWidget(holder)
        self.request_area.hide()
        if web_transcript.ENABLED:
            self.web = web_transcript.WebTranscript(COLUMN + 48, self._web_action)
            self.web.resolve = self.file_path
            outer.addWidget(self.web.view, 1)
            self.web.view.hide()
        outer.addWidget(self.request_area)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self._stick = True
        self._anchor = None   # (message, its top on screen) kept while its editor opens or closes
        self._anchor_timer = QTimer(self, singleShot=True, interval=400)
        self._anchor_timer.timeout.connect(lambda: setattr(self, '_anchor', None))
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.latest = QPushButton(self.scroll)
        self.latest.setObjectName('LatestButton')
        self.latest.setAccessibleName('最新訊息')
        self.latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.latest.setFixedSize(34, 34)
        self.latest.clicked.connect(self.to_latest)
        self.latest.hide()
        self.fade = Fade(self.scroll)
        self.composer = Composer(self, enter_sends=True)
        self.composer.recall = self._last_user_text
        # Context sits with the other facts of the work, in the box's own row (as in Codex); the header
        # keeps only the title. Git is opened from the project.
        self.composer.bar.insertWidget(self.composer.bar.indexOf(self.composer.settings.model_chip), self.context_chip)
        self.composer.submitted.connect(self.send)
        self.composer.stop.clicked.connect(lambda: self.window_.dispatcher.interrupt(self.key))
        self.composer.settings.changed.connect(
            lambda field, value: self.key and self.window_.dispatcher.configure(self.key, **{field: value}))
        self.find = FindBar(self)
        find_row = QHBoxLayout()
        find_row.setContentsMargins(24, 6, 24, 0)
        find_row.addStretch(1)
        find_row.addWidget(self.find, 100)
        find_row.addStretch(1)
        self.find.setMaximumWidth(COLUMN)
        outer.addLayout(find_row)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(24, 8, 24, 18)
        wrap.addStretch(1)
        wrap.addWidget(self.composer, 100)
        wrap.addStretch(1)
        outer.addLayout(wrap)
        # Quota of the Agent's account: in the bottom-right corner beside the message box when there is room,
        # else under the box at its right edge.
        self.quota: QWidget | None = None
        self.quota_slot = QWidget()
        self.quota_slot.setMaximumWidth(COLUMN)
        slot = QHBoxLayout(self.quota_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        slot.addStretch(1)
        self.quota_slot.hide()
        quota_row = QHBoxLayout()
        quota_row.setContentsMargins(24, 0, 24, 12)
        quota_row.addStretch(1)
        quota_row.addWidget(self.quota_slot, 100)
        quota_row.addStretch(1)
        outer.addLayout(quota_row)
        self.history_cache: dict[str, tuple[list, object]] = {}
        self._clock = QTimer(self, interval=1000)   # the running turn's elapsed time
        self._clock.timeout.connect(self._tick)
        self.places: dict[str, int] = {}   # conversation -> distance from the bottom when it was left
        self.refreshing: list | None = None   # the cached page on screen until the client's copy arrives
        self._place = 0
        self.request_ids: tuple = ()
        self.forms: dict[str, FormBox] = {}
        self.permission_rows: list[QWidget] = []
        self.buffered: list[dict] = []
        self.last_state = ''

    # -- web page or Qt widgets ------------------------------------------------------
    @property
    def _stick(self) -> bool:
        """Follow new content (stay at the bottom); setting it brings the page to the bottom too."""
        return self.__dict__.get('_stick_value', True)

    @_stick.setter
    def _stick(self, value: bool) -> None:
        self._stick_value = value
        if value and self.use_web:
            self.web.to_bottom()

    def _set_mode(self) -> None:
        """The web page when the app enabled it and the look does not paint the chat itself."""
        web = self.web is not None and not any(theme.decorated(n) for n in ('canvas', 'bubble', 'code', 'reply'))
        self.use_web = web
        self.scroll.setVisible(not web)
        if self.web is not None:
            self.web.view.setVisible(web)
            if web:
                self.web.restyle()
        if self.request_box.parentWidget() is not (self.request_area.widget() if web else self.content):
            self.request_box.parentWidget().layout().removeWidget(self.request_box)
            if web:
                self.request_row.insertWidget(1, self.request_box, 100)
            else:
                self.items.insertWidget(self.items.indexOf(self.status) + 1, self.request_box)
        self.request_box.setMaximumWidth(COLUMN if web else 16777215)
        self.request_area.setVisible(web and any(self.request_ids))

    def restyle(self) -> None:
        """After the look changed: a page drawn again, or the conversation rebuilt when the drawing changes."""
        before = self.use_web
        self._set_mode()
        if self.key and before != self.use_web:
            self.reload()
        if self.quota is not None:
            self.quota.setFixedWidth(theme.px(230))
            self.quota.restyle()
            self._place_quota()

    def _retry_id(self) -> str:
        """The newest ending, when it failed or was stopped and the Agent is idle, offers to run it again."""
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        items = self.transcript.items
        if row is not None and row['state'] != 'running' and items and items[-1]['kind'] == 'end' \
                and self._last_user_text():
            return items[-1]['id']
        return ''

    def _render_web(self) -> None:
        if self.use_web:
            status = '' if self.status.isHidden() else self.status.text()
            row = self.window_.store.dispatch.get(self.key) if self.key else None
            self.web.show(self.transcript.items, self._retry_id(), bool(self.older), status,
                          bool(row) and row['state'] == 'running')

    def file_path(self, name: str) -> str:
        """Where an attachment named in a message is: sent from this window, or a pasted picture."""
        path = self.window_.sent_files.get(name) or (
            str(self.composer.attach_dir / name) if self.composer.attach_dir else '')
        return path if path and os.path.isfile(path) else ''

    def _web_action(self, action: str, arg: str) -> None:
        """A button on the page was clicked."""
        if action == 'copy':
            QApplication.clipboard().setText(arg)
        elif action == 'edit':
            try:
                message_id, text = json.loads(arg)
            except (ValueError, TypeError):
                return
            self.submit_edit(str(message_id), str(text))
        elif action == 'fork':
            self.fork(arg)
        elif action == 'retry':
            self.retry()
        elif action == 'image':
            if not any(arg in item.get('files', []) for item in self.transcript.items if item.get('kind') == 'user'):
                return
            path = self.file_path(arg)
            if path:
                self.window_.external_tools.open('image', path)
        elif action == 'older':
            self.load_older()

    # -- loading ---------------------------------------------------------------
    CACHED = 12   # conversations whose newest page is kept, to show at once when opened again

    def open(self, key: str) -> None:
        if key == self.key:
            self.update_row()
            return
        self.composer.save_draft()
        if self.key:
            self._remember_place(self.key)
        self._set_mode()
        self._rebuild_requests({'permissions': {}, 'forms': {}}, ((), ()))
        self.key = key
        self._clear()
        self.refreshing = None
        self.older = None
        self.loading = True
        self._stick = True
        self.more.hide()
        self.status.setText('載入中…')
        self.status.show()
        self.request_ids = ()
        self.composer.load_draft(key)
        self.buffered = []
        self.find.hide()
        row = self.window_.store.dispatch.get(key) or {}
        self.composer.settings.set_options(self.window_.store.dispatch.options.get(row.get('client', ''), {}), row,
                                           self.window_.store.dispatch.options.get(row.get('client', ''), {})
                                           .get('mode_fixed', False))
        self.composer.root = row.get('directory', '')
        self._commands(self.window_.store.dispatch.options.get(row.get('client', ''), {}))
        self.last_state = row.get('state', '')
        self.update_row()
        self._place = self.places.pop(key, 0)
        cached = self.history_cache.get(key)
        if cached is not None and row.get('state') != 'running':
            # Shown at once from last time; the client's copy replaces it if it changed meanwhile.
            self.history_loaded(key, *cached)
            self.refreshing = cached[0]
            self.loading = True
            self.more.setEnabled(False)
        self._fetch(None)
        self.window_.request_options(row.get('client', ''), key, row.get('directory', ''))
        self._quota_for(row.get('client', ''))

    # -- quota -----------------------------------------------------------------------
    def set_quota(self, model) -> None:
        from agentdock.ui.quota_view import QuotaView
        self.quota = QuotaView(model, compact=True, show_head=False)
        self.quota.setFixedWidth(theme.px(230))
        self.quota.keys = set()
        self.quota.render()
        model.changed.connect(self._place_quota)
        self._place_quota()

    def _quota_for(self, client: str) -> None:
        if self.quota is None:
            return
        from agentdock.agent_info import QUOTA_KEYS
        self.quota.keys = {QUOTA_KEYS[client]} if client in QUOTA_KEYS else set()
        self.quota.render()
        self._place_quota()

    def _place_quota(self) -> None:
        q = self.quota
        if q is None:
            return
        shown = bool(self.key) and q.shown > 0
        q.adjustSize()
        column = min(COLUMN, self.width() - 48)
        corner = self.width() - q.width() - 20
        if corner >= (self.width() + column) // 2 + 16:   # room beside the message box
            if q.parentWidget() is not self:
                self.quota_slot.layout().removeWidget(q)
                q.setParent(self)
            q.move(corner, self.height() - q.height() - 18)
            q.raise_()
            self.quota_slot.hide()
        else:
            if q.parentWidget() is not self.quota_slot:
                self.quota_slot.layout().addWidget(q)
            self.quota_slot.setVisible(shown)
        q.setVisible(shown)

    def _show_status(self, row: dict) -> None:
        if self.loading:
            return
        running = row['state'] == 'running' and not row.get('permissions') and not row.get('forms')
        # Only OpenCode goes through a shared service that can be offline.
        offline = self.window_.store.dispatch.offline if row['client'] == 'opencode' else ''
        text = status_text({**row, 'offline': offline})
        elapsed = self._elapsed() if running else ''
        if elapsed:
            head, _, rest = text.partition('\n')
            text = f'{head} · {elapsed}' + (f'\n{rest}' if rest else '')
        show = running or row['state'] in ('unknown',) or bool(offline and row['state'] != 'done')
        self.status.setText(text if show else '')
        self.status.setVisible(show)

    def _elapsed(self) -> str:
        """How long the current turn has run, as 1:23 (or 1:02:03)."""
        start = self.window_.run_started.get(self.key)
        if start is None:
            return ''
        seconds = max(0, int(time.monotonic() - start))
        hours, rest = divmod(seconds, 3600)
        return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'

    def _tick(self) -> None:
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        if row is None or row['state'] != 'running':
            self._clock.stop()
            return
        self._show_status(row)
        if self.use_web:
            self._render_web()

    def options_arrived(self, key: str, options: dict) -> None:
        row = self.window_.store.dispatch.get(key)
        if key == self.key and row is not None:
            self.composer.settings.set_options(options, row, options.get('mode_fixed', False))
            self._commands(options)

    def _commands(self, options: dict) -> None:
        """What '/' offers: AgentDock's own /compact, then the Agent's commands it reported."""
        own = [{'name': 'compact', 'description': '壓縮上下文'}]
        self.composer.commands = own + [c for c in options.get('commands', []) if c.get('name') != 'compact']

    def _more_menu(self) -> None:
        if not self.key:
            return
        menu = QMenu(self)
        menu.addAction('搜尋對話（Ctrl+F）', self.find.open_bar)
        compact = menu.addAction('壓縮對話', lambda: self.window_.dispatcher.compact(self.key))
        row = self.window_.store.dispatch.get(self.key) or {}
        compact.setEnabled(row.get('state') != 'running')
        menu.addSeparator()
        menu.addAction('改名', lambda: self.window_.rename_conversation(self.key))
        delete = menu.addAction('刪除', lambda: self.window_.delete_conversation(self.key))
        delete.setEnabled(row.get('state') != 'running')
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def _fetch(self, cursor) -> None:
        key, bridge = self.key, self.window_.bridge
        self.window_.dispatcher.history(key, cursor, lambda messages, older: bridge.history.emit(key, messages, older),
                                        lambda message: bridge.history_failed.emit(key, message))

    def history_failed(self, key: str, message: str) -> None:
        if key != self.key:
            return
        self.loading = False
        self.more.setEnabled(True)
        self.status.setText(message)
        self.status.show()
        self._render_web()

    def load_older(self) -> None:
        if self.older and not self.loading:
            self.loading = True
            self.more.setEnabled(False)
            self._fetch(self.older)

    def _clear(self) -> None:
        if self.use_web:
            self.web.clear()
        self.transcript = Transcript()
        for widget in self.widgets.values():
            self.items.removeWidget(widget)
            widget.deleteLater()
        self.widgets = {}
        self.open_tools = set()
        self.open_process = set()

    def _cache(self, key: str, messages: list, older) -> None:
        self.history_cache.pop(key, None)
        self.history_cache[key] = (messages, older)
        while len(self.history_cache) > self.CACHED:
            self.history_cache.pop(next(iter(self.history_cache)))

    def _remember_place(self, key: str) -> None:
        """How far above the bottom the reader was, to come back there."""
        if self.use_web:
            self.web.where(lambda value, k=key: self.places.__setitem__(k, int(value or 0)))
        else:
            bar = self.scroll.verticalScrollBar()
            self.places[key] = 0 if self._stick else bar.maximum() - bar.value()

    def _go_to(self, from_bottom: int) -> None:
        self._stick_value = False
        if self.use_web:
            self.web.go_to(from_bottom)
        else:
            bar = self.scroll.verticalScrollBar()
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - from_bottom))

    def _refreshed(self, key: str, messages: list, older) -> None:
        """The client's copy after the cached one was shown: redraw only when it differs."""
        shown, self.refreshing = self.refreshing, None
        self._cache(key, messages, older)
        self.loading = False
        self.more.setEnabled(True)
        row = self.window_.store.dispatch.get(key) or {}
        # Once a new turn streams in, the live view is the current one.
        if messages == shown or row.get('state') == 'running':
            return
        bar = self.scroll.verticalScrollBar()
        from_bottom = 0 if self._stick or self.use_web else bar.maximum() - bar.value()
        self._clear()
        self._place = from_bottom
        self.history_loaded(key, messages, older)

    def history_loaded(self, key: str, messages: list, older) -> None:
        if key != self.key:
            return
        if self.refreshing is not None:
            self._refreshed(key, messages, older)
            return
        first = not self.transcript.items
        if first:
            self._cache(key, messages, older)
        bar = self.scroll.verticalScrollBar()
        from_bottom = bar.maximum() - bar.value()
        added = self.transcript.prepend(messages)
        if not self.use_web:   # the page keeps the reader's place itself
            index = self.items.indexOf(self.more) + 1
            for offset, item in enumerate(added):
                widget = self._widget(item)
                self.items.insertWidget(index + offset, widget)
            self._group.start()
        self.older = older
        self.loading = False
        self.more.setEnabled(True)
        self.more.setVisible(bool(older))
        self.status.hide()
        place = 0
        if first:
            place, self._place = self._place, 0
            self._stick = True
            events, self.buffered = self.buffered, []
            for event in events:   # what streamed in while the history was loading
                self.live_event(key, event)
            self.update_row()
        elif not self.use_web:
            # Keep the reader's place when older messages appear above.
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - from_bottom))
        self._sync_retry()
        if place > 40:   # back where the reader left it
            self._go_to(place)

    def live_event(self, key: str, event: dict) -> None:
        if key != self.key:
            return
        if self.loading and not self.transcript.items:
            self.buffered.append(event)
            return
        item = self.transcript.apply(event)
        if item is None:
            return
        if self.use_web:
            if not self._flush.isActive():
                self._flush.start()   # streaming redraws at most ~12/s
            return
        if item['id'] not in self.widgets:
            widget = self._widget(item)
            self.items.insertWidget(self.items.indexOf(self.status), widget)
            self._group.start()
            self._sync_retry()
        else:
            self._pending_items.add(item['id'])
            if not self._flush.isActive():
                self._flush.start()

    def _widget(self, item: dict) -> MessageWidget:
        widget = MessageWidget(item, resolve=self.file_path)
        widget.edit_submitted.connect(self.submit_edit)
        widget.keep_place = lambda change, w=widget: self._keep_place(w, change)
        widget.retry_requested.connect(self.retry)
        widget.fork_requested.connect(self.fork)
        self.widgets[item['id']] = widget
        return widget

    def _sync_retry(self) -> None:
        """Only the newest ending, when it failed or was stopped, offers to run the last message again."""
        if self.use_web:
            self._render_web()
            return
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        last = self.transcript.items[-1]['id'] if self.transcript.items else ''
        idle = row is not None and row['state'] != 'running'
        for item_id, widget in self.widgets.items():
            if widget.retry is not None:
                widget.retry.setVisible(idle and item_id == last and self._last_user_text() != '')

    def _last_user_text(self) -> str:
        return next((i['text'] for i in reversed(self.transcript.items) if i['kind'] == 'user'), '')

    def fork(self, message_id: str) -> None:
        if self.key:
            self.window_.dispatcher.fork(self.key, message_id, done=self.window_.bridge.started.emit)

    def retry(self) -> None:
        text = self._last_user_text()
        if text and self.key:
            self.window_.dispatcher.send(self.key, text)
            self._stick = True

    def submit_edit(self, message_id: str, text: str) -> None:
        """A message edited in place: it and everything after it are replaced by the new text."""
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        if row is None or not text.strip():
            return
        if row['state'] == 'running':
            self.window_.notify('這個對話還在執行，請先停止。')
            return
        key, bridge = self.key, self.window_.bridge
        users = [i['id'] for i in self.transcript.items if i['kind'] == 'user']
        back = len(users) - 1 - users.index(message_id) if message_id in users else -1
        self._cut(message_id)
        self._anchor = None   # from here the list follows the new answer
        self._stick = True
        self.window_.dispatcher.edit(key, message_id, back, text, [], replaced=bridge.replaced.emit,
                                     failed=lambda: bridge.reload.emit(key))

    def _cut(self, message_id: str) -> None:
        """The edited message and what followed leave the page at once; the new answer streams in after."""
        for item in self.transcript.cut(message_id):
            widget = self.widgets.pop(item['id'], None)
            if widget is not None:
                self.items.removeWidget(widget)
                widget.deleteLater()
        if self.use_web:
            self._render_web()
        else:
            self._group.start()
            self._sync_retry()

    def _flush_items(self) -> None:
        if self.use_web:
            self._render_web()
            return
        for item_id in self._pending_items:
            widget = self.widgets.get(item_id)
            if widget and widget.item['kind'] == 'assistant':
                widget.update_item(widget.item)
        self._pending_items.clear()
        self._regroup()

    def _regroup(self) -> None:
        """Consecutive shown assistant steps form one card; copying from it copies the whole answer."""
        every = [w for w in (self.items.itemAt(i).widget() for i in range(self.items.count()))
                 if isinstance(w, MessageWidget)]
        self._fold_process(every)
        shown = [w for w in every if w.folded or not hidden(w)]
        self._fold_tools(shown)
        shown = [w for w in shown if not w.folded]
        groups: list[list[MessageWidget]] = []
        for i, widget in enumerate(shown):
            mine = widget.item['kind'] == 'assistant'
            top = mine and i > 0 and shown[i - 1].item['kind'] == 'assistant'
            bottom = mine and i + 1 < len(shown) and shown[i + 1].item['kind'] == 'assistant'
            widget.set_join(top, bottom)
            if mine:
                if top:
                    groups[-1].append(widget)
                else:
                    groups.append([widget])
        for members in groups:
            for widget in members:
                widget.copy_text = lambda g=members: '\n\n'.join(t for t in (w.plain_text() for w in g) if t)

    def _fold_process(self, widgets: list[MessageWidget]) -> None:
        """As on the web page: while the Agent works every step shows (thinking open). Once an answer is done,
        everything up to its last tool or thought, the remarks in between too, folds into '處理了 N 個步驟',
        with a rule above the rest of the answer."""
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        running = row is not None and row['state'] == 'running'
        turns: list[list[MessageWidget]] = [[]]
        for widget in widgets:
            if widget.item['kind'] == 'assistant':
                turns[-1].append(widget)
            elif turns[-1]:
                turns.append([])
        live_turn = turns[-1] if running and turns[-1] else None
        for turn in turns:
            head = next((w for w in turn if w.has_content()), None)
            if head is None:
                continue
            live = turn is live_turn
            parts = [(w, p['key'], p['type']) for w in turn for p in w.item.get('parts', [])
                     if p['key'] in w.parts and (p['type'] == 'tool' or str(p.get('text') or '').strip())]
            last = max((i for i, (_w, _k, kind) in enumerate(parts) if kind != 'text'), default=-1)
            fold = not live and last >= 0
            key = head.item['id']
            opened = key in self.open_process
            for index, (widget, part_key, kind) in enumerate(parts):
                part = widget.parts[part_key]
                if isinstance(part, ReasoningRow):
                    part.set_live(live)
                part.setVisible(not (fold and index <= last and not opened))
            head.on_process = lambda k=key: (self.open_process.symmetric_difference_update({k}), self._regroup())
            head.set_process(sum(1 for _w, _k, kind in parts[:last + 1] if kind != 'text') if fold else 0, opened)
            answer = parts[last + 1] if fold and last + 1 < len(parts) else None
            for widget in turn:
                widget.set_rule(answer[1] if answer and answer[0] is widget else None)
            # A step whose parts all went into the folded line takes no space; the first keeps the line.
            for widget in turn:
                if not widget.has_content():
                    continue
                showing = (widget.process is not None and not hidden(widget.process)) or any(
                    not hidden(p) for p in widget.parts.values())
                if widget.isHidden() == showing and not widget.folded:
                    widget.setVisible(showing)

    def _fold_tools(self, shown: list[MessageWidget]) -> None:
        """Two or more tools with nothing shown between them (even across steps) fold into one line on the
        first; clicking it unfolds them. A step left with nothing but folded tools takes no space."""
        runs: list[list[ToolRow]] = [[]]
        for widget in shown:
            if widget.item['kind'] != 'assistant':
                runs.append([])
                continue
            for part in widget.parts.values():
                if isinstance(part, ToolRow):
                    runs[-1].append(part)
                elif not hidden(part):
                    runs.append([])
        for run in runs:
            if len(run) == 1:
                run[0].set_fold(None, False)
            elif run:
                key = run[0].part['key']
                folded = key not in self.open_tools
                run[0].on_summary = lambda k=key: (self.open_tools.symmetric_difference_update({k}), self._regroup())
                for i, row in enumerate(run):
                    row.set_fold(run if i == 0 else None, folded)
        for widget in shown:
            if widget.item['kind'] != 'assistant' or not widget.has_content():
                continue
            visible = (widget.process is not None and not hidden(widget.process)) or any(
                not hidden(p) and not (isinstance(p, ToolRow) and p.folded and not p.heads)
                for p in widget.parts.values())
            if widget.folded == visible:
                widget.folded = not visible
                widget.setVisible(visible)

    def reload(self, fresh: bool = False) -> None:
        """Build the open conversation again: after the text size changed, or (`fresh`) from the client's
        copy only, when what was shown is known to be wrong."""
        if self.key:
            key, self.key = self.key, ''
            if fresh:
                self.history_cache.pop(key, None)
            self.open(key)

    # -- state -----------------------------------------------------------------
    def update_row(self) -> None:
        row = self.window_.store.dispatch.get(self.key)
        if row is None:
            return
        self.title.setText(row['title'])
        self._commands(self.window_.store.dispatch.options.get(row['client'], {}))   # learned as it starts
        pid = self.window_.projects.project_of(row)
        project = self.window_.projects.get(pid) if pid else None
        chat = project is None and self.window_.is_chat_dir(row.get('directory', ''))
        self.project_chip.setIcon(icon('chat' if chat else 'folder', theme.MUTED or '#888888', 14))
        self.project_chip.setText(project['name'] if project else '只聊天' if chat else '未分類')
        self.project_chip.setToolTip(row.get('directory', ''))
        self.agent_chip.setText(row['source'])
        usage = row.get('context') or {}
        self.context_chip.setText(context_text(usage))
        self.context_chip.setToolTip(f"{usage.get('used', 0):,} / {usage['limit']:,} tokens" if usage.get('limit')
                                     else '')
        high = bool(usage.get('limit')) and usage.get('used', 0) >= 0.8 * usage['limit']
        if self.context_chip.property('high') != high:
            self.context_chip.setProperty('high', high)
            self.context_chip.style().unpolish(self.context_chip)
            self.context_chip.style().polish(self.context_chip)
        self.context_chip.setVisible(bool(self.context_chip.text()))
        if row['state'] != self.last_state:
            self.last_state = row['state']
            if not self.use_web:
                self._group.start()   # a finished answer folds its steps
        running = row['state'] == 'running' and not row.get('permissions') and not row.get('forms')
        self.composer.set_running(row['state'] == 'running')
        self._show_status(row)
        if running != self._clock.isActive():
            self._clock.start() if running else self._clock.stop()
        self._sync_retry()
        ids = (tuple(row.get('permissions', {})), tuple(row.get('forms', {})))
        if ids != self.request_ids:
            self._rebuild_requests(row, ids)
        if not row['read'] and self.isVisible() and self.window().isActiveWindow():
            self.window_.store.queue_mark_read(row['owner'], f"session:{row['client']}:{row['session_id']}", [row['id']])

    def _rebuild_requests(self, row: dict, ids: tuple) -> None:
        self.request_ids = ids
        for widget in self.permission_rows:
            self.requests.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.permission_rows = []
        for fid in list(self.forms):
            if fid not in row.get('forms', {}):
                widget = self.forms.pop(fid)
                self.requests.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
        for pid, permission in row.get('permissions', {}).items():
            card = QFrame()
            card.setObjectName('Request')
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.addWidget(label(f"{row['source']} 要求：{permission.get('action', '')}", name='FieldLabel'))
            resources = '\n'.join(permission.get('resources', []))
            if resources:
                lay.addWidget(label(resources, name='ToolDetail'))
            buttons = QHBoxLayout()
            buttons.addStretch()
            for text, decision, kind in (('拒絕', 'reject', 'danger'), ('一律允許', 'always', 'flat'),
                                         ('允許', 'once', 'primary')):
                b = button(text, **{kind: True})
                b.clicked.connect(lambda _c=False, p=pid, d=decision:
                                  self.window_.dispatcher.reply_permission(self.key, p, d))
                buttons.addWidget(b)
            lay.addLayout(buttons)
            self.requests.addWidget(card)
            self.permission_rows.append(card)
        for fid, form in row.get('forms', {}).items():
            if fid in self.forms:
                continue
            box = FormBox(self, form)
            box.setObjectName('Request')
            self.forms[fid] = box
            self.requests.addWidget(box)
        if ids != ((), ()):
            self._stick = True
        self.request_area.setVisible(self.use_web and any(ids))
        QTimer.singleShot(0, self._fit_requests)

    def _fit_requests(self) -> None:
        """With the web page the cards sit above the message box: as tall as they are, at most half the page."""
        if self.request_area.isVisible():
            height = self.request_area.widget().sizeHint().height() + 2
            self.request_area.setFixedHeight(min(height, max(120, self.height() // 2)))

    def answer_form(self, form_id: str, answer) -> None:
        self.window_.dispatcher.reply_form(self.key, form_id, answer)

    def send(self) -> None:
        text = self.composer.text.toPlainText().strip()
        if not self.key or not (text or self.composer.files):
            return
        if text == '/compact' and not self.composer.files:
            self.window_.dispatcher.compact(self.key)   # each Agent's own way to compact
        else:
            files = self.composer.take_files()
            self.window_.sent_files.update({os.path.basename(f): f for f in files})
            key, bridge = self.key, self.window_.bridge
            self.window_.dispatcher.send(key, text, files, failed=lambda: bridge.unsent.emit(key, text, files))
        self.composer.clear()
        self._stick = True

    def _open_project(self) -> None:
        row = self.window_.store.dispatch.get(self.key)
        pid = self.window_.projects.project_of(row) if row else ''
        if pid:
            self.window_.show_project(pid)

    # -- scrolling: follow new content only when already at the bottom ----------------------
    def _scrolled(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self._stick = bar.maximum() - value < 40
        self._place_latest()

    def _place_latest(self) -> None:
        """The ↓ centred just above the message box while reading older messages, and the fade along the
        transcript's lower edge while there is more below."""
        bar = self.scroll.verticalScrollBar()
        view = self.scroll.viewport().geometry()
        below = bar.maximum() - bar.value()
        self.fade.setGeometry(view.left(), view.bottom() - Fade.HEIGHT + 1, view.width(), Fade.HEIGHT)
        self.fade.setVisible(below > 0 and not theme.decorated('canvas'))
        self.fade.raise_()
        away = below > 200 and hasattr(self, 'composer')
        if away:
            self.latest.setIcon(icon('down', theme.MUTED or '#888888', 16))
            middle = self.composer.geometry().center().x() - self.scroll.geometry().left()
            self.latest.move(middle - self.latest.width() // 2, view.bottom() - self.latest.height() - 10)
            self.latest.raise_()
        self.latest.setVisible(away)

    def to_latest(self) -> None:
        self._stick = True
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_latest()
        self._fit_requests()
        self._place_quota()

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        if self._anchor is not None:
            self._restore_anchor()
        elif self._stick:
            self.scroll.verticalScrollBar().setValue(maximum)
        self._place_latest()

    def _keep_place(self, widget: QWidget, change) -> None:
        """Change a message (open or close its editor) without the list moving under the reader: its top
        stays where it was on screen while the layout settles."""
        self._anchor = (widget, widget.mapTo(self.scroll.viewport(), QPoint(0, 0)).y())
        change()
        self._restore_anchor()
        QTimer.singleShot(0, self._restore_anchor)
        self._anchor_timer.start()

    def _restore_anchor(self) -> None:
        if self._anchor is None:
            return
        widget, top = self._anchor
        try:
            now = widget.mapTo(self.scroll.viewport(), QPoint(0, 0)).y()
        except RuntimeError:   # the message is gone
            self._anchor = None
            return
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + now - top)


class FindBar(QFrame):
    """Ctrl+F in a conversation: finds text in the loaded messages, Enter / ↓ for the next match."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setObjectName('FindBar')
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(4)
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜尋')
        self.query.textChanged.connect(lambda _t: self.search(0))
        self.query.returnPressed.connect(lambda: self.step(1))
        row.addWidget(self.query, 1)
        self.count = QLabel('')
        self.count.setObjectName('FileNote')
        row.addWidget(self.count)
        for text, step in (('↑', -1), ('↓', 1)):
            b = mini_button(text)
            b.clicked.connect(lambda _c=False, s=step: self.step(s))
            row.addWidget(b)
        close = mini_button('✕')
        close.clicked.connect(self.close_bar)
        row.addWidget(close)
        self.matches: list[tuple[QWidget, int]] = []
        self.index = -1
        self.hide()

    def open_bar(self) -> None:
        self.show()
        self.query.setFocus()
        self.query.selectAll()
        self.search(0)

    def close_bar(self) -> None:
        self.hide()
        self.matches, self.index = [], -1
        if self.view.use_web:
            self.view.web.find('', False, lambda _a, _n: None)   # clears the highlight
        self.view.composer.text.setFocus()

    def _found(self, active: int, total: int) -> None:
        self.count.setText(f'{active}/{total}' if total else ('0' if self.query.text().strip() else ''))

    @staticmethod
    def _text_of(widget: QWidget) -> str:
        if isinstance(widget, QuestionText):
            return widget.document().toPlainText()
        return widget.text() if isinstance(widget, QLabel) else ''

    def _targets(self) -> list[QWidget]:
        found = []
        for item in self.view.transcript.items:
            message = self.view.widgets.get(item['id'])
            if message is None:
                continue
            if item['kind'] == 'user':
                found.append(message.user_text)
            for part in message.parts.values():
                if isinstance(part, RichText):
                    found += [w.text if isinstance(w, CodeBlock) else w for _k, w in part.blocks]
        return found

    def search(self, start: int) -> None:
        if self.view.use_web:
            self.view.web.find(self.query.text().strip(), False, self._found)
            return
        needle = self.query.text().strip().lower()
        self.matches = []
        if needle:
            for widget in self._targets():
                text = self._text_of(widget).lower()
                at = text.find(needle)
                while at >= 0:
                    self.matches.append((widget, at))
                    at = text.find(needle, at + 1)
        self.index = -1
        if self.matches:
            self.step(1)
        else:
            self.count.setText('0' if needle else '')

    def step(self, direction: int) -> None:
        if self.view.use_web:
            if self.query.text().strip():
                self.view.web.find(self.query.text().strip(), direction < 0, self._found)
            return
        if not self.matches:
            return
        self.index = (self.index + direction) % len(self.matches)
        widget, at = self.matches[self.index]
        length = len(self.query.text().strip())
        if isinstance(widget, QuestionText):
            cursor = widget.textCursor()
            cursor.setPosition(at)
            cursor.setPosition(at + length, cursor.MoveMode.KeepAnchor)
            widget.setTextCursor(cursor)
        elif isinstance(widget, QLabel):
            widget.setSelection(at, length)
        self.view.scroll.ensureWidgetVisible(widget, 0, 80)
        self.count.setText(f'{self.index + 1}/{len(self.matches)}')
