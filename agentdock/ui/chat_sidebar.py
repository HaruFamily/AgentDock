"""The conversation window's rail and its list of projects and conversations."""
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
from agentdock.ui.chat_common import RAIL, ROLE, SIDEBAR, heart_icon, icon, paint_decor, theme_color, when


# --------------------------------------------------------------------------- rail
class Rail(QFrame):
    """Icons for the window's pages. Clicking 聊天 while it is open shows or hides the conversation list,
    as activity bars in desktop editors do."""
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('ChatRail')
        self.setFixedWidth(RAIL)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)
        self.buttons = {}
        for name, text in (('chat', '聊天'), ('new', '新對話'), ('tools', '工具')):
            layout.addWidget(self._button(name, text))
        self.buttons['new'].hide()   # only while the list (with its own button) is hidden
        layout.addStretch(1)
        layout.addWidget(self._button('settings', '設定'))
        self.current = 'chat'
        self.set_current('chat')

    def _button(self, name, text):
        b = QPushButton()
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(text)
        b.setToolTip(text)
        b.setIconSize(QSize(18, 18))
        b.setProperty('label', text)
        b.clicked.connect(lambda: self.picked.emit(name))
        self.buttons[name] = b
        return b

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        paint_decor(self, 'rail', event)

    def set_current(self, name: str) -> None:
        self.current = name
        for key, b in self.buttons.items():
            b.setIcon(icon('plus' if key == 'new' else key,
                           (theme.ACCENT_DEEP if key == name else (theme.ACCENT if theme.CUTE else theme.MUTED))
                           or '#888888'))
            b.setProperty('current', key == name)
            b.style().unpolish(b)
            b.style().polish(b)


# --------------------------------------------------------------------------- sidebar
class ConversationTree(QTreeWidget):
    """Projects with their conversations. Dropping a conversation on a project moves it there."""
    moved = Signal(str, str)
    key_action = Signal(str, str)   # ('delete' | 'rename', conversation key)

    def __init__(self):
        super().__init__()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setHeaderHidden(True)
        self.setColumnCount(3)
        self.setIndentation(18)
        self.setRootIsDecorated(False)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        from PySide6.QtGui import QColor, QPalette
        palette = self.palette()   # drawRow paints the rounded highlight; the system's square one stays off
        for role in (QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText):
            palette.setColor(role, QColor(0, 0, 0, 0) if role == QPalette.ColorRole.Highlight else QColor(theme.INK or '#000'))
        if hasattr(QPalette.ColorRole, 'Accent'):   # the Windows 11 style's bar beside the selected row
            palette.setColor(QPalette.ColorRole.Accent, QColor(0, 0, 0, 0))
        self.setPalette(palette)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, self.header().ResizeMode.Stretch)
        for column, width in ((1, 52), (2, 28)):
            self.header().setSectionResizeMode(column, self.header().ResizeMode.Fixed)
            self.setColumnWidth(column, width)

    def drawRow(self, painter, option, index):  # noqa: N802
        """One rounded highlight for the whole row; per-cell backgrounds would split it in two."""
        from PySide6.QtGui import QCursor, QPainter
        selected = self.selectionModel().isSelected(index)
        hovered = index == self.indexAt(self.viewport().mapFromGlobal(QCursor.pos())).siblingAtColumn(0)
        if selected or hovered:
            first = self.visualRect(index.siblingAtColumn(0))
            rect = option.rect.adjusted(first.left() - option.rect.left(), 1, -2, -1)
            radius = 13 if theme.CUTE else 8
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_color('ACCENT_SOFT' if selected else 'HOVER'))
            painter.drawRoundedRect(rect, radius, radius)
            painter.restore()
        super().drawRow(painter, option, index)

    def drawBranches(self, painter, rect, index):  # noqa: N802
        pass

    def mouseMoveEvent(self, event):  # noqa: N802
        super().mouseMoveEvent(event)
        self.viewport().update()

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self.viewport().update()

    @staticmethod
    def _project_at(item) -> str | None:
        while item is not None:
            kind, value = item.data(0, ROLE) or ('', '')
            if kind == 'project':
                return value
            item = item.parent()
        return None

    def keyPressEvent(self, event):  # noqa: N802
        item = self.currentItem()
        kind, value = (item.data(0, ROLE) or ('', '')) if item else ('', '')
        action = {Qt.Key.Key_Delete: 'delete', Qt.Key.Key_F2: 'rename'}.get(event.key())
        if kind == 'conv' and action:
            self.key_action.emit(action, value)
            return
        super().keyPressEvent(event)

    def startDrag(self, actions):  # noqa: N802
        item = self.currentItem()
        if item and (item.data(0, ROLE) or ('',))[0] == 'conv':
            super().startDrag(actions)

    def dragMoveEvent(self, event):  # noqa: N802
        target = self._project_at(self.itemAt(event.position().toPoint()))
        if target is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        # Never let Qt move the rows itself; the store decides and the list is rebuilt.
        item = self.currentItem()
        target = self._project_at(self.itemAt(event.position().toPoint()))
        event.ignore()
        if item is not None and target is not None and (item.data(0, ROLE) or ('',))[0] == 'conv':
            self.moved.emit(item.data(0, ROLE)[1], target)


class Sidebar(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.setObjectName('ChatSidebar')
        self.setMinimumWidth(SIDEBAR[0])
        self.setMaximumWidth(SIDEBAR[2])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(10)
        self.new_button = button(theme.t('chat_new'))
        self.new_button.clicked.connect(lambda: window.show_new())
        self.style_new_button()
        layout.addWidget(self.new_button)
        self.search = QLineEdit()
        self.search.setObjectName('SidebarSearch')
        self.search.setPlaceholderText('搜尋對話')
        self.search.setAccessibleName('搜尋對話')
        self.search.addAction(icon('search', theme.MUTED or '#888888', 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh())
        layout.addWidget(self.search)
        head = QHBoxLayout()
        section = QLabel('專案')
        section.setObjectName('SectionLabel')
        head.addWidget(section, 1)
        add = icon_button('＋', '新增專案')
        add.clicked.connect(lambda: window.add_project())
        head.addWidget(add)
        layout.addLayout(head)
        self.tree = ConversationTree()
        self.tree.itemClicked.connect(self._clicked)
        self.tree.moved.connect(window.move_conversation)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.setMouseTracking(True)
        self.tree.itemEntered.connect(self._hovered)
        self.tree.verticalScrollBar().valueChanged.connect(lambda _v: self._unhover())
        self.tree.viewport().installEventFilter(self)
        self.hover_item = None
        self.hover_button = None
        self.hover_new = None
        self.tree.key_action.connect(lambda action, key: (window.delete_conversation(key) if action == 'delete'
                                                          else window.rename_conversation(key)))
        layout.addWidget(self.tree, 1)

    def restyle(self) -> None:
        # A painted panel (lace, ruffles) needs room around the list.
        self.layout().setContentsMargins(*((26, 40, 26, 26) if theme.decorated('panel') else (12, 14, 12, 12)))
        self.style_new_button()

    def style_new_button(self) -> None:
        """Filled with the accent only in the sweet look; otherwise a quiet outlined button, so the accent
        stays for sending and the selected conversation."""
        b = self.new_button
        b.setText('新對話')
        b.setIcon(icon('plus', theme.INK or '#888888', 16))
        b.setProperty('primary', False)
        b.setObjectName('NewChat')
        b.setMinimumHeight(38)
        b.style().unpolish(b)
        b.style().polish(b)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        paint_decor(self, 'panel', event, 26)

    def keys(self) -> list[str]:
        """Conversation keys in the order shown."""
        found = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            found += [top.child(j).data(0, ROLE)[1] for j in range(top.childCount())
                      if (top.child(j).data(0, ROLE) or ('',))[0] == 'conv']
        return found

    # -- the ⋯ on the row under the mouse opens the same menu as a right click --------
    def _hovered(self, item) -> None:
        if item is self.hover_item:
            return
        self._unhover()
        kind, _value = item.data(0, ROLE) or ('', '')
        if kind not in ('conv', 'project') or (kind == 'project' and not _value):
            return
        self.hover_item = item
        b = QPushButton(self.tree.viewport())
        b.setObjectName('RowMore')
        b.setAccessibleName('專案設定' if kind == 'project' else '更多')
        b.setToolTip('專案設定' if kind == 'project' else '更多')
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(icon('settings' if kind == 'project' else 'more', theme.MUTED or '#888888', 16))
        b.setFixedSize(26, 22)
        if kind == 'project':
            b.clicked.connect(lambda _c=False, pid=_value: self.window_.project_settings(pid))
        else:
            b.clicked.connect(lambda _c=False, i=item, w=b: self._menu_for(i, w.mapToGlobal(w.rect().bottomLeft())))
        self.hover_button = b
        rect = self.tree.visualItemRect(item)
        b.move(self.tree.viewport().width() - 28, rect.center().y() - 11)
        b.show()
        if kind == 'project':
            add = QPushButton(self.tree.viewport())
            add.setObjectName('RowMore')
            add.setAccessibleName('新對話')
            add.setIcon(icon('plus', theme.MUTED or '#888888', 16))
            add.setFixedSize(26, 22)
            add.setCursor(Qt.CursorShape.PointingHandCursor)
            add.move(b.x() - 28, b.y())
            add.clicked.connect(lambda _c=False, pid=_value: self.window_.show_new(pid))
            add.show()
            self.hover_new = add

    def _unhover(self) -> None:
        if self.hover_item is not None:
            try:
                self.hover_button.hide()
                self.hover_button.deleteLater()
            except RuntimeError:
                pass   # the list was rebuilt
        if self.hover_new is not None:
            self.hover_new.hide()
            self.hover_new.deleteLater()
            self.hover_new = None
        self.hover_item = self.hover_button = None

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.tree.viewport() and event.type() == QEvent.Type.Leave:
            self._unhover()
        return super().eventFilter(watched, event)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is not None:
            self._menu_for(item, self.tree.viewport().mapToGlobal(pos))

    def _menu_for(self, item, where) -> None:
        kind, value = item.data(0, ROLE) or ('', '')
        w = self.window_
        menu = QMenu(self)
        if kind == 'conv':
            row = w.store.dispatch.get(value)
            if row is None:
                return
            pinned = bool(row.get('pinned'))
            menu.addAction('取消釘選' if pinned else '釘選', lambda: w.store.dispatch.set_pinned(value, not pinned))
            menu.addAction('改名', lambda: w.rename_conversation(value))
            move = menu.addMenu('移到專案')
            current = w.projects.project_of(row)
            for pid, name in [(p['id'], p['name']) for p in w.projects.list()] + [(UNSORTED, '未分類')]:
                action = move.addAction(name, lambda p=pid: w.move_conversation(value, p))
                action.setCheckable(True)
                action.setChecked(pid == current)
            menu.addAction('複製工作階段 ID', lambda: QApplication.clipboard().setText(row['session_id']))
            if sys.platform == 'win32':
                action = menu.addAction('在終端機接續', lambda: w.resume_in_terminal(value))
                action.setEnabled(row['state'] != 'running')
            menu.addSeparator()
            action = menu.addAction('刪除', lambda: w.delete_conversation(value))
            action.setEnabled(row['state'] != 'running')
        elif kind == 'project' and value:
            menu.addAction('在此專案開新對話', lambda: w.show_new(value))
            menu.addAction('匯入對話到此專案…', lambda: w.import_conversations(value))
            menu.addAction('專案變更', lambda: w.open_git((w.projects.get(value) or {}).get('root', ''), report=True))
            menu.addAction('專案設定…', lambda: w.project_settings(value))
        else:
            return
        self.show_menu(menu, where)

    def show_menu(self, menu, where) -> None:
        menu.exec(where)

    def _clicked(self, item, _column):
        kind, value = item.data(0, ROLE) or ('', '')
        if kind == 'conv':
            self.window_.open_conversation(value)
        elif kind in ('project', 'pinned'):
            item.setExpanded(not item.isExpanded())

    def refresh(self) -> None:
        w = self.window_
        query = self.search.text().strip().lower()
        expanded = {tuple(self.tree.topLevelItem(i).data(0, ROLE)): self.tree.topLevelItem(i).isExpanded()
                    for i in range(self.tree.topLevelItemCount())}
        rows = sorted(w.rows().items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
        project_names = {p['id']: p['name'].lower() for p in w.projects.list()}
        groups: dict[str, list] = {}
        for key, row in rows:
            if query and query not in project_names.get(w.projects.project_of(row), '') and not any(query in str(row.get(name, '')).lower()
                                 for name in ('title', 'prompt', 'reply', 'source', 'directory')):
                continue
            groups.setdefault('\x00pinned' if row.get('pinned') else w.projects.project_of(row), []).append((key, row))
        self._unhover()
        self.tree.clear()
        PINNED = '\x00pinned'
        entries = [(p['id'], p['name']) for p in w.projects.list()] + [(UNSORTED, '未分類')]
        if groups.get(PINNED):
            entries.insert(0, (PINNED, '釘選'))
        for pid, name in entries:
            convs = groups.get(pid, [])
            if pid in (UNSORTED, PINNED) and not convs:
                continue
            top = QTreeWidgetItem([name, '', ''])
            top.setData(0, ROLE, ('pinned', '') if pid == PINNED else ('project', pid))
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setSizeHint(0, QSize(0, 38))
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsSelectable)
            if pid and pid != PINNED:
                project = w.projects.get(pid)
                top.setToolTip(0, project['root'])
                top.setIcon(0, icon(project.get('icon', 'folder'), project.get('color') or theme.MUTED or '#888888', 16))
            self.tree.addTopLevelItem(top)
            selected_project = w.project_view.pid if w.pages.currentWidget() is w.project_view else w.new_view.pid
            if not w.current_key and pid and pid == selected_project:
                self.tree.setCurrentItem(top)
            for key, row in convs:
                badge = self._badge(row)
                child = QTreeWidgetItem([row['title'], badge, ''])
                child.setSizeHint(0, QSize(0, 32))
                child.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
                child.setData(0, ROLE, ('conv', key))
                child.setToolTip(0, row['title'])
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                if badge in ('待授權', '待回答'):
                    child.setForeground(1, theme_color('ALERT'))
                elif badge == '●':
                    child.setForeground(1, theme_color('ACCENT'))
                else:
                    child.setForeground(1, theme_color('MUTED'))
                top.addChild(child)
                if key == w.current_key:
                    self.tree.setCurrentItem(child)
            if not convs:
                empty = QTreeWidgetItem(['沒有符合的對話' if query else '沒有對話', '', ''])
                empty.setData(0, ROLE, ('empty', ''))
                empty.setFlags(Qt.ItemFlag.ItemIsEnabled)
                empty.setForeground(0, theme_color('MUTED'))
                empty.setSizeHint(0, QSize(0, 30))
                top.addChild(empty)
            top.setExpanded(expanded.get(tuple(top.data(0, ROLE)), True) or bool(query))

    @staticmethod
    def _badge(row: dict) -> str:
        if row.get('permissions'):
            return '待授權'
        if row.get('forms'):
            return '待回答'
        if row['state'] == 'running':
            return '進行中'
        if row['state'] == 'failed':
            return '失敗'
        return '●' if not row['read'] else ''
