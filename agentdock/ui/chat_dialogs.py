"""Project page, dialogs, the Ctrl+K palette and the drawn title bar of the conversation window."""
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
from agentdock.ui.chat_common import AGENTS, COLUMN, ROLE, icon, when
from agentdock.ui.chat_sidebar import Sidebar
from agentdock.ui.chat_new import NewConversationView, RecentRow


# --------------------------------------------------------------------------- project page
class ProjectView(QWidget):
    """A project's own page: its name and folder, a message box that starts a conversation there,
    and its conversations. Less used actions sit in the ⋯ menu."""

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.pid = ''
        self.root_path = ''
        self._shown = None
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(32, 28, 32, 28)
        row.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(COLUMN)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(36, 36)
        head.addWidget(self.icon_preview)
        heading = label('專案設定', name='PageTitle')
        head.addWidget(heading, 1)
        lay.addLayout(head)
        lay.addSpacing(24)
        lay.addWidget(label('名稱', name='FieldLabel'))
        self.name = QLineEdit()
        self.name.setAccessibleName('專案名稱')
        self.name.editingFinished.connect(self._rename)
        lay.addWidget(self.name)
        lay.addSpacing(28)
        lay.addWidget(label('圖示', name='FieldLabel'))
        symbols = QHBoxLayout()
        symbols.setSpacing(8)
        self.icon_buttons = {}
        for key, title in (('folder', '資料夾'), ('tools', '工具'), ('chat', '對話'),
                           ('shield', '盾牌'), ('agent', '機器人')):
            choice = QPushButton()
            choice.setCheckable(True)
            choice.setFixedSize(38, 36)
            choice.setAccessibleName(title)
            choice.setToolTip(title)
            choice.clicked.connect(lambda _checked=False, value=key: self._pick_icon(value))
            self.icon_buttons[key] = choice
            symbols.addWidget(choice)
        symbols.addStretch(1)
        self.color_button = button('顏色', flat=True)
        self.color_button.clicked.connect(self._pick_color)
        symbols.addWidget(self.color_button)
        reset = button('預設顏色', flat=True)
        reset.clicked.connect(lambda: self._appearance(color=''))
        symbols.addWidget(reset)
        lay.addLayout(symbols)
        lay.addSpacing(28)
        lay.addWidget(label('資料夾', name='FieldLabel'))
        place = QHBoxLayout()
        place.setSpacing(8)
        self.root = QLineEdit()
        self.root.setReadOnly(True)
        self.root.setAccessibleName('目前資料夾')
        self.root.setMinimumWidth(0)
        place.addWidget(self.root, 1)
        self.change_root = button('瀏覽…', flat=True)
        self.change_root.clicked.connect(self._change_root)
        place.addWidget(self.change_root)
        self.open_root = button('開啟資料夾', flat=True)
        self.open_root.clicked.connect(self._open_root)
        place.addWidget(self.open_root)
        lay.addLayout(place)
        lay.addSpacing(24)
        from agentdock.ui.git_view import GitView
        from agentdock.ui.settings_page import Card
        self.git_card = Card()
        self.git_view = GitView(external=window.external_tools.open, embedded=True)
        self.git_card.lay.addWidget(self.git_view)
        lay.addWidget(self.git_card)
        lay.addSpacing(24)
        actions = QHBoxLayout()
        self.import_button = button('匯入對話', flat=True)
        self.import_button.clicked.connect(lambda: self.window_.import_conversations(self.pid))
        actions.addWidget(self.import_button)
        actions.addStretch(1)
        remove = button('刪除專案', danger=True)
        remove.clicked.connect(self._remove)
        actions.addWidget(remove)
        lay.addLayout(actions)
        lay.addStretch(1)
        self.starter = window.new_view
        self.composer = self.starter.composer
        row.addWidget(column, 100)
        row.addStretch(1)
        scroll.setWidget(body)
        self.restyle()

    def restyle(self) -> None:
        self.git_card.restyle()
        self.git_view.restyle()
        self.refresh()
        self.composer.restyle()
        self.starter.agent_chip.restyle()

    def show_project(self, pid: str) -> None:
        self.pid = pid
        self._shown = None
        before = self.git_view.directory
        self.refresh()
        if before == self.root_path:
            self.git_view.set_root(self.root_path, force=True)

    def refresh(self) -> None:
        project = self.window_.projects.get(self.pid)
        if project is None:
            return
        if not self.name.hasFocus():
            self.name.setText(project['name'])
        self.icon_preview.setPixmap(icon(project.get('icon', 'folder'), project.get('color') or theme.MUTED or '#888888', 22).pixmap(22, 22))
        for key, choice in self.icon_buttons.items():
            choice.setIcon(icon(key, project.get('color') or theme.MUTED or '#888888', 20))
            choice.setChecked(key == project.get('icon', 'folder'))
        self.root_path = project['root']
        if hasattr(self, 'git_view'):
            self.git_view.set_root(self.root_path)
        if self.root.text() != self.root_path:
            self.root.setText(self.root_path)
            self.root.setCursorPosition(0)

    def _pick_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        project = self.window_.projects.get(self.pid)
        if project is None:
            return
        chosen = QColorDialog.getColor(QColor(project.get('color') or theme.MUTED), self, '專案圖示顏色')
        if chosen.isValid():
            self.window_.projects.update(self.pid, color=chosen.name())
            self.refresh()
            self.window_.sidebar.refresh()

    def _appearance(self, **changes) -> None:
        self.window_.projects.update(self.pid, **changes)
        self.refresh()
        self.window_.sidebar.refresh()

    def _pick_icon(self, value: str) -> None:
        self._appearance(icon=value)

    def _open_root(self) -> None:
        if os.path.isdir(self.root_path):
            os.startfile(self.root_path)   # noqa: S606 - Explorer, no console
        else:
            self.window_.notify('找不到這個資料夾。')

    def _rename(self) -> None:
        try:
            self.window_.projects.update(self.pid, name=self.name.text())
        except (KeyError, ValueError) as e:
            self.window_.notify(str(e))

    def _change_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, '選擇根目錄', self.root_path)
        if chosen:
            try:
                self.window_.projects.update(self.pid, root=chosen)
                self.refresh()
                self.window_.sidebar.refresh()
            except (KeyError, ValueError) as e:
                self.window_.notify(str(e))

    def _remove(self) -> None:
        project = self.window_.projects.get(self.pid)
        if project is None:
            return
        answer = QMessageBox.question(self, '刪除專案', f"刪除「{project['name']}」？對話不會刪除。")
        if answer == QMessageBox.StandardButton.Yes:
            self.window_.projects.remove(self.pid)
            self.window_.show_new()


class ProjectDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('新增專案')
        form = QFormLayout(self)
        self.name = QLineEdit()
        form.addRow('名稱', self.name)
        root_row = QHBoxLayout()
        self.root = QLineEdit()
        root_row.addWidget(self.root, 1)
        browse = button('選擇…')
        browse.clicked.connect(self.browse)
        root_row.addWidget(browse)
        form.addRow('根目錄', root_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(520, 0)

    def browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, '選擇根目錄', self.root.text() or os.path.expanduser('~'))
        if chosen:
            self.root.setText(os.path.normpath(chosen))
            if not self.name.text().strip():
                self.name.setText(os.path.basename(os.path.normpath(chosen)))


class RenameDialog(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle('改名')
        layout = QVBoxLayout(self)
        self.name = QLineEdit(title)
        self.name.selectAll()
        layout.addWidget(self.name)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button('取消', flat=True)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = button('儲存', primary=True)
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.resize(420, 0)


class ImportDialog(QDialog):
    """Sessions started in an agent's own app or terminal, to continue them here."""

    def __init__(self, window, project: str | None, agent: str):
        super().__init__(window)
        self.window_ = window
        self.project = project
        self.found: list[dict] = []
        self.setWindowTitle('匯入對話')
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.agent = QComboBox()
        for kind, name in AGENTS:
            if kind in window.dispatcher.clients:
                self.agent.addItem(name, kind)
        self.agent.setCurrentIndex(max(0, self.agent.findData(agent)))
        self.agent.currentIndexChanged.connect(lambda _i: self.load())
        top.addWidget(self.agent)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.fill())
        top.addWidget(self.search, 1)
        layout.addLayout(top)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(['標題', '資料夾', '時間'])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda _i, _c: self.adopt())
        self.list.itemSelectionChanged.connect(self._sync)
        header = self.list.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.Interactive)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        self.list.setColumnWidth(1, 240)
        layout.addWidget(self.list, 1)
        self.status = label('', muted=True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.only_project = QCheckBox('只列出專案資料夾內的')
        root = (window.projects.get(project) or {}).get('root', '') if project else ''
        self.root = root
        self.only_project.setVisible(bool(root))
        self.only_project.setChecked(bool(root))
        self.only_project.toggled.connect(lambda _c: self.fill())
        buttons.addWidget(self.only_project)
        buttons.addStretch(1)
        close = button('關閉', flat=True)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.adopt_button = button('匯入', primary=True)
        self.adopt_button.clicked.connect(self.adopt)
        buttons.addWidget(self.adopt_button)
        layout.addLayout(buttons)
        window.bridge.sessions.connect(self.arrived)
        self.load()

    def load(self) -> None:
        self.found = []
        self.list.clear()
        self.status.setText('載入中…')
        self.status.show()
        client = self.agent.currentData()
        self.window_.dispatcher.sessions(client, lambda found: self.window_.bridge.sessions.emit(client, found))
        self._sync()

    def arrived(self, client: str, found: list) -> None:
        if client != self.agent.currentData():
            return
        self.found = found
        self.fill()

    def fill(self) -> None:
        self.list.clear()
        query = self.search.text().strip().lower()
        root = os.path.normcase(os.path.normpath(self.root)) if self.root and self.only_project.isChecked() else ''
        shown = 0
        for session in self.found:
            folder = session.get('directory', '')
            if query and query not in session['title'].lower() and query not in folder.lower():
                continue
            if root:
                norm = os.path.normcase(os.path.normpath(folder)) if folder else ''
                if norm != root and not norm.startswith(root.rstrip(os.sep) + os.sep):
                    continue
            stamp = datetime.fromtimestamp(session['updated'], timezone.utc).isoformat() if session.get('updated') else ''
            item = QTreeWidgetItem([session['title'], folder, when(stamp) if stamp else ''])
            item.setData(0, ROLE, session)
            item.setToolTip(1, folder)
            if session.get('tracked'):
                item.setText(2, '已在 AgentDock')
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addTopLevelItem(item)
            shown += 1
        self.status.setText('' if shown else '沒有可匯入的對話。')
        self.status.setVisible(not shown)
        self._sync()

    def _sync(self) -> None:
        self.adopt_button.setEnabled(bool(self.list.selectedItems()))

    def adopt(self) -> None:
        client = self.agent.currentData()
        target = self.window_.target_for(client)
        picked = [i.data(0, ROLE) for i in self.list.selectedItems() if i.data(0, ROLE)]
        for index, session in enumerate(picked):
            last = index == len(picked) - 1
            self.window_.dispatcher.adopt(client, session['id'], target['owner'], session['title'],
                                          directory=session.get('directory', ''), project=self.project,
                                          done=self.window_.bridge.started.emit if last else (lambda _k: None))
        if picked:
            self.accept()


# --------------------------------------------------------------------------- window
class SearchPalette(QFrame):
    """Ctrl+K: a box over the window; type to find a conversation by title, messages, Agent or folder,
    Enter opens it."""
    LIMIT = 9

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setObjectName('PaletteBackdrop')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.box = QFrame(self)
        self.box.setObjectName('Palette')
        lay = QVBoxLayout(self.box)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.query = QLineEdit()
        self.query.setObjectName('PaletteQuery')
        self.query.setPlaceholderText('搜尋')
        self.query.textChanged.connect(self.fill)
        self.query.installEventFilter(self)
        lay.addWidget(self.query)
        self.results = QListWidget()
        self.results.setObjectName('Suggestions')
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.itemClicked.connect(lambda _i: self.choose())
        lay.addWidget(self.results)
        self.hide()

    def open(self) -> None:
        self.setGeometry(self.window_.rect())
        width = min(640, self.width() - 80)
        self.box.setGeometry((self.width() - width) // 2, 72, width, 80)
        self.query.clear()
        self.fill()
        self.show()
        self.raise_()
        self.query.setFocus()

    def fill(self) -> None:
        w = self.window_
        query = self.query.text().strip().lower()
        rows = sorted(w.rows().items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
        hits = [(k, r) for k, r in rows
                if not query or any(query in str(r.get(n, '')).lower()
                                    for n in ('title', 'prompt', 'reply', 'source', 'directory'))]
        self.results.clear()
        for key, row in hits[:self.LIMIT]:
            pid = w.projects.project_of(row)
            project = w.projects.get(pid) if pid else None
            meta = ' · '.join(p for p in (row.get('source', ''), project['name'] if project else '',
                                          when(row.get('updated_at', ''))) if p)
            item = QListWidgetItem(f"{row['title']}\n{meta}")
            item.setData(ROLE, key)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        rows = sum(self.results.sizeHintForRow(i) + 4 for i in range(self.results.count()))
        self.results.setFixedHeight(rows + 2 * self.results.frameWidth() + 4)
        self.results.setVisible(bool(self.results.count()))
        self.box.resize(self.box.width(), self.box.sizeHint().height())

    def choose(self) -> None:
        item = self.results.currentItem()
        self.hide()
        if item is not None:
            self.window_.open_conversation(item.data(ROLE))

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.query and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.results.count():
                step = -1 if key == Qt.Key.Key_Up else 1
                self.results.setCurrentRow((self.results.currentRow() + step) % self.results.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.choose()
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):  # noqa: N802
        if not self.box.geometry().contains(event.position().toPoint()):
            self.hide()


class TitleBar(QWidget):
    """The window's own title bar when the local look draws one (draw_titlebar): drag to move, double-click
    to maximise, and minimise / maximise / close buttons the look paints. Moving and resizing are left to
    the system (startSystemMove / startSystemResize), so snapping to screen edges keeps working."""
    HEIGHT = 44
    BUTTONS = ('min', 'max', 'close')

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.hovered = ''
        self.pressed = ''

    def button_rects(self) -> dict:
        from PySide6.QtCore import QRectF
        size, gap = 30, 6
        right = self.width() - 14
        rects = {}
        for name in reversed(self.BUTTONS):
            rects[name] = QRectF(right - size, (self.height() - size) / 2, size, size)
            right -= size + gap
        return rects

    def _at(self, pos) -> str:
        return next((name for name, r in self.button_rects().items() if r.contains(pos)), '')

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme.decorate('titlebar', painter, QRectF(self.rect()), self.window_.windowTitle(), self.button_rects(),
                       self.hovered, self.window_.isMaximized())
        painter.end()

    def mouseMoveEvent(self, event):  # noqa: N802
        name = self._at(event.position())
        if name != self.hovered:
            self.hovered = name
            self.update()

    def leaveEvent(self, event):  # noqa: N802
        self.hovered = ''
        self.update()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.pressed = self._at(event.position())
        if not self.pressed and self.window_.windowHandle() is not None:
            self.window_.windowHandle().startSystemMove()

    def mouseReleaseEvent(self, event):  # noqa: N802
        name = self._at(event.position())
        if name and name == self.pressed:
            self.act(name)
        self.pressed = ''

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if not self._at(event.position()):
            self.act('max')

    def act(self, name: str) -> None:
        w = self.window_
        if name == 'min':
            w.showMinimized()
        elif name == 'max':
            w.showNormal() if w.isMaximized() else w.showMaximized()
        elif name == 'close':
            w.close()
