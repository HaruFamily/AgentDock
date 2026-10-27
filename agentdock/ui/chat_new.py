"""The new-conversation page: the message box, where and with which Agent, recent conversations."""
from __future__ import annotations


import difflib
import html
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
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
from agentdock.ui.chat_common import AGENTS, CHAT_MODES, COLUMN, ChipButton, icon, paint_decor, when
from agentdock.ui.chat_sidebar import Sidebar
from agentdock.ui.chat_composer import Composer


MASCOT = 96   # height the local look's mascot gets above the new-conversation box


class Mascot(QWidget):
    """Above the new-conversation box, only when the local look draws one (draw_mascot). No title text:
    the empty box says what to do."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(MASCOT)
        self.restyle()

    def restyle(self) -> None:
        self.setVisible(theme.decorated('mascot'))

    def paintEvent(self, event):  # noqa: N802
        paint_decor(self, 'mascot', event)


class RecentRow(QPushButton):
    """One conversation under the new-conversation box: title, then its state, Agent and time."""

    def __init__(self, key: str, row: dict, badge: str):
        super().__init__()
        self.key = key
        self.setObjectName('RecentRow')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self.title = ElidedLabel(row['title'])
        self.title.setObjectName('RecentTitle')
        lay.addWidget(self.title)
        line = QHBoxLayout()
        line.setSpacing(0)
        parts = [row.get('source') or dict(AGENTS).get(row.get('client', ''), ''), when(row.get('updated_at', ''))]
        if badge in ('待授權', '待回答', '進行中', '失敗'):
            state = QLabel(badge + ' · ')
            state.setObjectName('RecentAlert' if badge in ('待授權', '待回答', '失敗') else 'RecentMeta')
            line.addWidget(state)
        meta = QLabel(' · '.join(p for p in parts if p))
        meta.setObjectName('RecentMeta')
        line.addWidget(meta)
        line.addStretch(1)
        lay.addLayout(line)
        self.setFixedHeight(self.sizeHint().height())

    def sizeHint(self):  # noqa: N802
        return self.layout().sizeHint()


class NewConversationView(QWidget):
    """As in desktop chat apps: the message box, then where it runs (project or folder) and
    which Agent, and the latest conversations to jump back into."""
    RECENT = 3

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.pid: str = UNSORTED
        self.folder_path = ''
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(2)
        column = QWidget()
        column.setMaximumWidth(COLUMN)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.mascot = Mascot()
        lay.addWidget(self.mascot)
        lay.addSpacing(12)
        self.composer = Composer(self, enter_sends=True, max_lines=12, min_lines=2)
        self.composer.setMaximumWidth(16777215)
        self.composer.submitted.connect(self.send)
        self.composer.settings.changed.connect(self._setting_changed)
        lay.addWidget(self.composer)
        # Where and with which Agent sit with the other choices in the box's own row: + 專案 Agent 權限 … 模型 推理.
        self.place = ChipButton('folder')
        self.composer.bar.insertWidget(1, self.place)
        self.agent_chip = ChipButton('agent')
        self.composer.bar.insertWidget(2, self.agent_chip)
        self.error = label('', muted=True)
        self.error.setObjectName('ErrorText')
        self.error.setContentsMargins(10, 6, 10, 0)
        self.error.hide()
        lay.addWidget(self.error)
        lay.addSpacing(28)
        self.recent_head = QLabel('最近')
        self.recent_head.setObjectName('SectionLabel')
        self.recent_head.setContentsMargins(12, 0, 0, 4)
        lay.addWidget(self.recent_head)
        self.recent = QVBoxLayout()
        self.recent.setSpacing(2)
        lay.addLayout(self.recent)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(column, 100)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        self.agent = self.window_.ui_state.get('last_agent') or 'opencode'

    # -- where -------------------------------------------------------------------
    def prepare(self, project: str | None = None) -> None:
        if project is not None:
            self.pid = project if (project == UNSORTED or self.window_.projects.get(project)) else UNSORTED
        elif self.pid != UNSORTED and not self.window_.projects.get(self.pid):
            self.pid = UNSORTED
        if not self.folder_path:
            self.folder_path = str(self.window_.chat_dir())   # no project: just talk
        self._place_changed()
        self.error.hide()
        self.refresh_recent()
        self.composer.text.setFocus()

    def set_folder(self, folder: str) -> None:
        """Work in a folder outside the projects: only to continue next to an older conversation (Ctrl+N);
        new work in a folder goes through a project."""
        self.folder_path = os.path.normpath(folder) if folder else ''
        self.pid = UNSORTED
        self._place_changed()

    def set_project(self, pid: str) -> None:
        self.pid = pid
        self._place_changed()

    def set_chat_only(self) -> None:
        """Just talk: the Agent works in AgentDock's own empty data/chat folder, with a cautious mode."""
        self.set_folder(str(self.window_.chat_dir()))

    def chat_only(self) -> bool:
        return self.project() is None and self.window_.is_chat_dir(self.folder_path)

    def project(self) -> dict | None:
        return self.window_.projects.get(self.pid) if self.pid else None

    def _place_changed(self) -> None:
        project = self.project()
        if project:
            self.place.setText(project['name'])
            self.place.setToolTip(project['root'])
            self.pick_agent(project.get('agent') or 'opencode')
        elif self.chat_only():
            self.place.setText('只聊天')
            self.place.setToolTip('')
            self.pick_agent(self.agent)
        else:
            self.place.setText(os.path.basename(self.folder_path.rstrip('\\/')) or self.folder_path)
            self.place.setToolTip(self.folder_path)
            self.pick_agent(self.agent)
        self.place.icon_name = 'chat' if self.chat_only() else 'folder'
        self.place.restyle()
        self._place_menu()

    def _place_menu(self) -> None:
        from PySide6.QtGui import QActionGroup
        menu = self.place.menu_
        menu.clear()
        group = QActionGroup(menu)
        projects = self.window_.projects.list()
        current = self.project()
        chat = menu.addAction(icon('chat', theme.MUTED or '#888888', 14), '只聊天', self.set_chat_only)
        chat.setCheckable(True)
        chat.setChecked(self.chat_only())
        group.addAction(chat)
        if projects:
            menu.addSection('專案')
            for p in projects:
                action = menu.addAction(icon('folder', theme.MUTED or '#888888', 14), p['name'],
                                        lambda pid=p['id']: self.set_project(pid))
                action.setCheckable(True)
                action.setChecked(current is not None and p['id'] == current['id'])
                action.setToolTip(p['root'])
                group.addAction(action)
        if not current and not self.chat_only() and self.folder_path:   # an older conversation's own folder
            action = menu.addAction(icon('folder', theme.MUTED or '#888888', 14),
                                    os.path.basename(self.folder_path.rstrip('\\/')) or self.folder_path)
            action.setCheckable(True)
            action.setChecked(True)
            action.setToolTip(self.folder_path)
            group.addAction(action)
        menu.addSeparator()
        menu.addAction('新增專案…', self._new_project)

    # -- which Agent -------------------------------------------------------------
    def pick_agent(self, kind: str) -> None:
        ready = kind in self.window_.dispatcher.clients
        changed = kind != self.agent or not self.composer.settings.options
        self.agent = kind
        self.agent_chip.setText(dict(AGENTS)[kind])
        from PySide6.QtGui import QActionGroup
        menu = self.agent_chip.menu_
        menu.clear()
        group = QActionGroup(menu)
        for key, name in AGENTS:
            action = menu.addAction(name, lambda k=key: self.pick_agent(k))
            action.setCheckable(True)
            action.setChecked(key == kind)
            action.setEnabled(key in self.window_.dispatcher.clients or key == kind)
            group.addAction(action)
        self.error.setText('' if ready else f'{dict(AGENTS)[kind]} 尚未支援。')
        self.error.setVisible(not ready)
        self.composer.send.setEnabled(ready)
        options = self.window_.store.dispatch.options.get(kind, {})
        self.composer.settings.set_options(options, self._chat_settings(kind, options, self._saved()))
        self.composer.commands = list(options.get('commands', []))
        self.composer.root = self._folder_now()
        if ready and changed:
            self.window_.request_options(kind, '', self._folder_now())

    def _chat_settings(self, kind: str, options: dict, settings: dict) -> dict:
        """In 只聊天 a mode the user did not choose becomes the Agent's cautious one (plan / read-only)."""
        mode = CHAT_MODES.get(kind, '')
        listed = [m.get('id') for m in options.get('modes', [])]
        if self.chat_only() and not settings.get('mode') and mode in listed:
            return {**settings, 'mode': mode}
        return settings

    def _saved(self) -> dict:
        return dict((self.window_.ui_state.get('new_settings') or {}).get(self.agent) or {})

    def _setting_changed(self, field: str, value: str) -> None:
        """Remember the choice per Agent for the next new conversation."""
        saved = dict(self.window_.ui_state.get('new_settings') or {})
        saved[self.agent] = {**self._saved(), field: value}
        self.window_.ui_state['new_settings'] = saved
        self.window_.save_state()

    def options_arrived(self, client: str, options: dict) -> None:
        if client == self.agent:
            self.composer.settings.set_options(options, self._chat_settings(client, options, self._saved()))
            self.composer.commands = list(options.get('commands', []))

    def _folder_now(self) -> str:
        project = self.project()
        return project['root'] if project else self.folder_path.strip().strip('"')

    def _new_project(self) -> None:
        """A folder to work in becomes a project; the new conversation then goes there."""
        project = self.window_.add_project(show=False)
        if project:
            self.set_project(project['id'])

    def pick_folder(self) -> bool:
        start = self.folder_path if os.path.isdir(self.folder_path) else os.path.expanduser('~')
        chosen = QFileDialog.getExistingDirectory(self, '選擇資料夾', start)
        if chosen:
            self.set_folder(chosen)
        return bool(chosen)

    # -- jump back in ------------------------------------------------------------
    def refresh_recent(self) -> None:
        rows = sorted(self.window_.store.dispatch.rows.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
        shown = [(key, row['title'], row.get('updated_at'), Sidebar._badge(row)) for key, row in rows[:self.RECENT]]
        if shown == getattr(self, '_shown', None):
            return
        self._shown = shown
        while self.recent.count():
            item = self.recent.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for key, row in rows[:self.RECENT]:
            entry = RecentRow(key, row, Sidebar._badge(row))
            entry.clicked.connect(lambda _c=False, k=key: self.window_.open_conversation(k))
            self.recent.addWidget(entry)
        self.recent_head.setVisible(bool(rows))

    def _take_files(self) -> list[str]:
        files = self.composer.take_files()
        self.window_.sent_files.update({os.path.basename(f): f for f in files})   # to show pictures sent
        return files

    def send(self) -> None:
        text = self.composer.text.toPlainText().strip()
        if not text or self.agent not in self.window_.dispatcher.clients:
            return
        project = self.project()
        if project:
            folder = project['root']
        else:
            if not self.folder_path.strip():
                self.folder_path = str(self.window_.chat_dir())
            folder = self.folder_path.strip().strip('"')
        if self.chat_only():
            os.makedirs(folder, exist_ok=True)
        if not os.path.isdir(folder):
            self.error.setText('找不到這個資料夾。')
            self.error.show()
            return
        target = self.window_.target_for(self.agent)
        self.window_.dispatcher.start_task(os.path.normpath(folder), text, target['owner'],
                                           project=project['id'] if project else None,
                                           started=self.window_.bridge.started.emit, client=self.agent,
                                           settings=self.composer.settings.values(),
                                           files=self._take_files())
        if not project and self.window_.ui_state.get('last_agent') != self.agent:
            self.window_.ui_state['last_agent'] = self.agent   # the next unsorted conversation starts with it
            self.window_.save_state()
        self.composer.clear()
