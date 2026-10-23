"""The conversation window: projects on the left, the whole conversation on the right.

The floating card stays for notices and quick replies; this window is where conversations are read
and continued. Approvals, answers, sends and stops only happen on a click, as in the Inbox.
"""
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
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
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
from agentdock.ui.question_text import QuestionText
from agentdock.ui.widgets import ElidedLabel, icon_button

ROLE = Qt.ItemDataRole.UserRole
AGENTS = (('opencode', 'OpenCode'), ('claude-code', 'Claude Code'), ('codex', 'Codex'), ('grok-cli', 'Grok'))
RAIL = 56
SIDEBAR = (220, 270, 460)   # narrowest, default and widest conversation list
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def split_code(text: str) -> list[tuple]:
    """Markdown split into ('text', body) and ('code', language, body); an unclosed fence runs to the end."""
    out: list[tuple] = []
    lines: list[str] = []
    code: list | None = None   # [fence char, fence length, language, lines]
    for line in text.split('\n'):
        marker = FENCE.match(line)
        if code is None:
            if marker:
                if '\n'.join(lines).strip():
                    out.append(('text', '\n'.join(lines)))
                lines = []
                info = marker.group(2).strip()
                code = [marker.group(1)[0], len(marker.group(1)), info.split()[0] if info else '', []]
            else:
                lines.append(line)
        elif marker and marker.group(1)[0] == code[0] and len(marker.group(1)) >= code[1] and not marker.group(2).strip():
            out.append(('code', code[2], '\n'.join(code[3])))
            code = None
        else:
            code[3].append(line)
    if code is not None:
        out.append(('code', code[2], '\n'.join(code[3])))
    elif '\n'.join(lines).strip():
        out.append(('text', '\n'.join(lines)))
    return out


def when(stamp: str, now: datetime | None = None) -> str:
    """A short local time for the list: 剛剛, 5 分, 14:03, 昨天, 10/20, 2025/12/31."""
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone()
    except (ValueError, AttributeError):
        return ''
    now = (now or datetime.now(timezone.utc)).astimezone()
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return '剛剛'
    if seconds < 3600:
        return f'{int(seconds // 60)} 分'
    days = (now.date() - moment.date()).days
    if days == 0:
        return moment.strftime('%H:%M')
    if days == 1:
        return '昨天'
    if moment.year == now.year:
        return f'{moment.month}/{moment.day}'
    return f'{moment.year}/{moment.month}/{moment.day}'


def resume_command(row: dict) -> list[str] | None:
    """The client's own command that continues this session in a terminal."""
    client, sid = row['client'], row['session_id']
    if client == 'claude-code':
        from agentdock.dispatch.claude import find_executable
        args = ['--resume', sid]
    elif client == 'codex':
        from agentdock.dispatch.codex import find_executable
        args = ['resume', sid]
    elif client == 'grok-cli':
        from agentdock.dispatch.grok import find_executable
        args = ['--resume', sid]
    else:
        from agentdock.dispatch.opencode import find_executable
        args = ['-s', sid]
    exe = find_executable()
    return [exe, *args] if exe else None


def tokens(count: int) -> str:
    if count >= 1_000_000:
        return f'{count / 1_000_000:.1f}M'
    if count >= 1000:
        return f'{count / 1000:.0f}k' if count >= 10_000 else f'{count / 1000:.1f}k'
    return str(count)


def context_text(usage: dict | None) -> str:
    """How full the model's context is, as the client last reported it."""
    if not usage or not usage.get('used'):
        return ''
    used, limit = int(usage['used']), int(usage.get('limit') or 0)
    if limit:
        return f'上下文 {min(100, round(used * 100 / limit))}%'
    return f'上下文 {tokens(used)}'


def _style() -> str:
    t = theme
    return f"""
#ChatWindow {{ background: {t.CANVAS}; }}
#ChatRail {{ background: {t.CARD}; border-right: 1px solid {t.LINE}; }}
#ChatRail QPushButton {{ border: none; border-radius: 10px; padding: 10px; background: transparent; }}
#ChatRail QPushButton:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatRail QPushButton[current="true"] {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DEEP}; font-weight: 700; }}
#ChatSidebar {{ background: {t.CARD}; border-right: 1px solid {t.LINE}; }}
#ChatSplit::handle {{ background: transparent; }}
QPushButton#RowMore {{ border: none; background: transparent; border-radius: 6px; padding: 0; }}
QPushButton#RowMore:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatSplit::handle:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatSidebar QTreeWidget {{ background: transparent; border: none; outline: none; }}
#ChatSidebar QTreeWidget::item {{ min-height: 30px; border-radius: 8px; padding: 2px 4px; }}
#ChatSidebar QTreeWidget::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.INK}; }}
#ChatSidebar QTreeWidget::item:hover {{ background: {t.HOVER}; }}
#SectionLabel {{ color: {t.MUTED}; font-size: 12px; }}
#UserBubble {{ background: {t.ACCENT_SOFT}; border: 1px solid {t.LINE}; border-radius: 14px; }}
#ToolRow {{ text-align: left; border: 1px solid {t.LINE}; border-radius: 8px; padding: 4px 10px; background: {t.CARD}; color: {t.MUTED}; }}
#ToolDetail {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 8px; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 12px; }}
#Request {{ background: {t.WARN_BG}; border: 1px solid {t.LINE}; border-radius: 12px; }}
#Composer {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 18px; }}
#Composer QPlainTextEdit {{ border: none; background: transparent; }}
#ConvTitle {{ font-size: 16px; font-weight: 700; }}
#PageTitle {{ font-size: 22px; font-weight: 700; }}
#ChatChip {{ border: 1px solid {t.LINE}; border-radius: 11px; padding: 2px 10px; color: {t.MUTED}; font-size: 12px; background: transparent; }}
#EndLine {{ color: {t.MUTED}; font-size: 12px; }}
#EndLine[failed="true"] {{ color: {t.DANGER}; }}
#CodeBlock {{ background: {t.ACCENT_SOFT}; border: 1px solid {t.LINE}; border-radius: 10px; }}
#CodeBlock QScrollArea, #CodeBlock QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
#CodeLang {{ color: {t.MUTED}; font-size: 11px; }}
QPushButton#MiniButton {{ border: none; background: transparent; color: {t.MUTED}; padding: 2px 8px; font-size: 12px; border-radius: 6px; }}
QPushButton#MiniButton:hover {{ background: {t.HOVER}; color: {t.INK}; }}
QPushButton#FileChip {{ border: 1px solid {t.LINE}; border-radius: 10px; padding: 2px 10px; background: {t.CANVAS}; color: {t.INK}; font-size: 12px; }}
#FileNote {{ color: {t.MUTED}; font-size: 12px; }}
#ErrorText {{ color: {t.DANGER}; }}
QDialog {{ background: {t.CANVAS}; }}
QDialog QTreeWidget, QDialog QTextBrowser {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 10px; outline: none; }}
QDialog QTreeWidget::item {{ min-height: 26px; }}
QDialog QTreeWidget::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.INK}; }}
QDialog QHeaderView::section {{ background: {t.CARD}; color: {t.MUTED}; border: none; border-bottom: 1px solid {t.LINE}; padding: 4px 6px; }}
#ChatChip[high="true"] {{ color: {t.DANGER}; border-color: {t.DANGER}; }}
QPushButton#Chip {{ border: none; background: transparent; color: {t.MUTED}; padding: 4px 20px 4px 8px; border-radius: 8px; font-size: 13px; text-align: left; }}
QPushButton#Chip:hover {{ background: {t.HOVER}; color: {t.INK}; }}
QPushButton#Chip:disabled {{ color: {t.DIM}; }}
QPushButton#IconButton {{ border: none; background: transparent; border-radius: 15px; padding: 0; }}
QPushButton#IconButton:hover {{ background: {t.HOVER}; }}
QPushButton#RoundButton, QPushButton#StopButton {{ border: none; border-radius: 15px; padding: 0; background: {t.ACCENT}; }}
QPushButton#RoundButton:hover, QPushButton#StopButton:hover {{ background: {t.ACCENT_DEEP}; }}
QPushButton#RoundButton:disabled {{ background: {t.DIM}; }}
#HomeTitle {{ font-size: 28px; font-weight: 700; }}
QPushButton#RecentRow {{ border: none; background: transparent; border-radius: 10px; padding: 0; text-align: left; }}
QPushButton#RecentRow:hover {{ background: {t.HOVER}; }}
#RecentTitle {{ font-size: 14px; }}
#RecentMeta {{ color: {t.MUTED}; font-size: 12px; }}
#RecentAlert {{ color: {t.ALERT}; font-size: 12px; }}
#DiffView {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 8px; }}
#Reasoning {{ color: {t.MUTED}; }}
#FindBar {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 10px; }}
QPushButton#LatestButton {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 16px; padding: 0; }}
QPushButton#LatestButton:hover {{ border-color: {t.ACCENT}; }}
#PaletteBackdrop {{ background: rgba(0, 0, 0, 40); }}
#Palette {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 16px; }}
#Palette QListWidget {{ border: none; }}
#Palette QListWidget::item {{ padding: 6px 10px; }}
#Suggestions {{ background: transparent; border: none; border-bottom: 1px solid {t.LINE}; outline: none; padding: 0 0 4px 0; }}
#Suggestions::item {{ padding: 4px 8px; border-radius: 8px; color: {t.INK}; }}
#Suggestions::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DEEP}; }}
QPushButton[seg="true"] {{ border-radius: 10px; padding: 5px 14px; }}
#SettingsPage[wide="true"] QWidget#SectionHead {{ background: transparent; }}
#SettingsPage[wide="true"] QWidget#WideList {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 14px; }}
#QuotaCard {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 14px; }}
"""


class Bridge(QObject):
    """Worker-thread callbacks into the UI thread."""
    history = Signal(str, list, object)
    history_failed = Signal(str, str)
    live = Signal(str, dict)
    started = Signal(str)
    deleted = Signal(str)
    options = Signal(str, str, dict)   # client, conversation key ('' for a new one), choices
    git = Signal(str, str, str)        # conversation key, repository root ('' when none), status summary
    sessions = Signal(str, list)       # client, its saved sessions


ICONS = {
    'chat': 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z',
    'tools': 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z',
    'settings': 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
    'folder': 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
    'clip': 'M21 11.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9',
    'more': 'M5 12h.01M12 12h.01M19 12h.01',
    'plus': 'M12 5v14M5 12h14',
    'up': 'M12 19V5M5 12l7-7 7 7',
    'stop': 'M8 8h8v8H8z',
    'down': 'M12 5v14M19 12l-7 7-7-7',
    'chevron': 'M6 9l6 6 6-6',
    'shield': 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
    'agent': 'M12 8V4M8 4h8M6 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM9 13h.01M15 13h.01',
}


def on_accent() -> str:
    """Text and icon colour on an accent-filled button."""
    return 'white' if theme.CUTE else (theme.CANVAS or '#000000')


def icon(name: str, color: str, size: int = 18):
    """A stroke icon in the theme's colour; fonts of private themes may lack emoji."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS[name]}"/></svg>')
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ChipButton(QPushButton):
    """A flat pick-one button as in desktop chat apps: optional icon, the current value and a small chevron.
    Clicking opens its menu under it."""

    def __init__(self, icon_name: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('Chip')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_name = icon_name
        self.menu_ = QMenu(self)
        self.clicked.connect(self.open_menu)
        self.restyle()

    def restyle(self) -> None:
        if self.icon_name:
            self.setIcon(icon(self.icon_name, theme.MUTED or '#888888', 14))

    def open_menu(self) -> None:
        if self.menu_.actions():
            self.menu_.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        from PySide6.QtGui import QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(theme_color('MUTED' if self.isEnabled() else 'DIM'))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        x, y = self.width() - 14, self.height() / 2 - 1.5
        painter.drawPolyline([QPointF(x, y), QPointF(x + 3.5, y + 3.5), QPointF(x + 7, y)])
        painter.end()


class RoundButton(QPushButton):
    """The circular send / stop button at the end of the message box."""

    def __init__(self, icon_name: str, tip: str, parent=None):
        super().__init__(parent)
        self.setObjectName('RoundButton')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tip)
        self.setFixedSize(32, 32)
        self.setIconSize(QSize(16, 16))
        self.icon_name = icon_name
        self.restyle()

    def restyle(self) -> None:
        self.setIcon(icon(self.icon_name, on_accent(), 16))


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

    def set_current(self, name: str) -> None:
        self.current = name
        for key, b in self.buttons.items():
            b.setIcon(icon('plus' if key == 'new' else key,
                           (theme.ACCENT_DEEP if key == name else theme.MUTED) or '#888888'))
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
        self.setColumnCount(2)
        self.setIndentation(14)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, self.header().ResizeMode.Stretch)
        self.header().setSectionResizeMode(1, self.header().ResizeMode.ResizeToContents)

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
        self.new_button = button('＋ 新對話', primary=True)
        self.new_button.clicked.connect(lambda: window.show_new())
        layout.addWidget(self.new_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.refresh())
        layout.addWidget(self.search)
        head = QHBoxLayout()
        section = QLabel('專案')
        section.setObjectName('SectionLabel')
        head.addWidget(section, 1)
        adopt = icon_button('⇩', '匯入對話')
        adopt.clicked.connect(lambda: window.import_conversations())
        head.addWidget(adopt)
        add = icon_button('＋', '新增專案')
        add.clicked.connect(window.add_project)
        head.addWidget(add)
        layout.addLayout(head)
        self.tree = ConversationTree()
        self.tree.itemClicked.connect(self._clicked)
        self.tree.moved.connect(window.move_conversation)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.setMouseTracking(True)
        self.tree.itemEntered.connect(self._hovered)
        self.tree.viewport().installEventFilter(self)
        self.hover_item = None
        self.hover_button = None
        self.tree.key_action.connect(lambda action, key: (window.delete_conversation(key) if action == 'delete'
                                                          else window.rename_conversation(key)))
        layout.addWidget(self.tree, 1)

    def keys(self) -> list[str]:
        """Conversation keys in the order shown."""
        found = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            found += [top.child(j).data(0, ROLE)[1] for j in range(top.childCount())]
        return found

    # -- the ⋯ on the row under the mouse opens the same menu as a right click --------
    def _hovered(self, item) -> None:
        if item is self.hover_item:
            return
        self._unhover()
        kind, _value = item.data(0, ROLE) or ('', '')
        if kind != 'conv':
            return
        self.hover_item = item
        b = QPushButton()
        b.setObjectName('RowMore')
        b.setAccessibleName('更多')
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(icon('more', theme.MUTED or '#888888', 16))
        b.setFixedSize(26, 22)
        b.clicked.connect(lambda _c=False, i=item, w=b: self._menu_for(i, w.mapToGlobal(w.rect().bottomLeft())))
        self.hover_button = b
        self.hover_text = item.text(1)
        item.setText(1, ' ' * max(4, len(self.hover_text)))   # keeps the column width; the ⋯ sits on it
        self.tree.setItemWidget(item, 1, b)

    def _unhover(self) -> None:
        if self.hover_item is not None:
            try:
                self.tree.removeItemWidget(self.hover_item, 1)
                self.hover_item.setText(1, self.hover_text)
            except RuntimeError:
                pass   # the list was rebuilt
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
            action = menu.addAction('刪除…', lambda: w.delete_conversation(value))
            action.setEnabled(row['state'] != 'running')
        elif kind == 'project' and value:
            menu.addAction('在此專案開新對話', lambda: w.show_new(value))
            menu.addAction('匯入對話到此專案…', lambda: w.import_conversations(value))
            menu.addAction('專案設定', lambda: w.show_project(value))
        else:
            return
        self.show_menu(menu, where)

    def show_menu(self, menu, where) -> None:
        menu.exec(where)

    def _clicked(self, item, _column):
        kind, value = item.data(0, ROLE) or ('', '')
        if kind == 'conv':
            self.window_.open_conversation(value)
        elif kind == 'project' and value:
            self.window_.show_project(value)

    def refresh(self) -> None:
        w = self.window_
        query = self.search.text().strip().lower()
        expanded = {tuple(self.tree.topLevelItem(i).data(0, ROLE)): self.tree.topLevelItem(i).isExpanded()
                    for i in range(self.tree.topLevelItemCount())}
        rows = sorted(w.store.dispatch.rows.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
        groups: dict[str, list] = {}
        for key, row in rows:
            if query and not any(query in str(row.get(name, '')).lower()
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
            top = QTreeWidgetItem([name, str(len(convs)) if convs and pid != PINNED else ''])
            top.setData(0, ROLE, ('pinned', '') if pid == PINNED else ('project', pid))
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsSelectable)
            if pid and pid != PINNED:
                top.setToolTip(0, w.projects.get(pid)['root'])
            self.tree.addTopLevelItem(top)
            for key, row in convs:
                badge = self._badge(row)
                child = QTreeWidgetItem([row['title'], badge or when(row.get('updated_at', ''))])
                child.setData(0, ROLE, ('conv', key))
                child.setToolTip(0, row['title'])
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                if badge in ('待授權', '待回答'):
                    child.setForeground(1, theme_color('ALERT'))
                elif badge == '●':
                    child.setForeground(1, theme_color('ACCENT'))
                    child.setText(1, f"● {when(row.get('updated_at', ''))}")
                else:
                    child.setForeground(1, theme_color('MUTED'))
                top.addChild(child)
                if key == w.current_key:
                    self.tree.setCurrentItem(child)
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


def theme_color(name: str):
    from PySide6.QtGui import QColor
    return QColor(getattr(theme, name) or '#888888')


# --------------------------------------------------------------------------- messages
EDIT_KEYS = (('old_string', 'new_string'), ('oldString', 'newString'))


def diff_of(part: dict) -> str:
    """A unified diff for an edit: given by the client, or made from the tool's old/new text."""
    if part.get('diff'):
        return part['diff']
    data = part.get('input') or {}
    path = str(data.get('file_path') or data.get('filePath') or data.get('path') or '')
    for old_key, new_key in EDIT_KEYS:
        if isinstance(data.get(old_key), str) and isinstance(data.get(new_key), str):
            return '\n'.join(difflib.unified_diff(data[old_key].splitlines(), data[new_key].splitlines(),
                                                   path, path, lineterm=''))
    edits = data.get('edits')
    if isinstance(edits, list):
        out = []
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get('old_string'), str) and isinstance(e.get('new_string'), str):
                out += difflib.unified_diff(e['old_string'].splitlines(), e['new_string'].splitlines(), path, path,
                                            lineterm='')
        return '\n'.join(out)
    return ''


def diff_html(diff: str, limit: int = 400) -> str:
    """Coloured diff lines; everything escaped, nothing loaded."""
    good, bad = theme.OK or '#2e7d32', theme.DANGER or '#c62828'
    lines = []
    for line in diff.splitlines()[:limit]:
        text = html.escape(line) or '&nbsp;'
        if line.startswith('+') and not line.startswith('+++'):
            lines.append(f'<span style="color:{good}">{text}</span>')
        elif line.startswith('-') and not line.startswith('---'):
            lines.append(f'<span style="color:{bad}">{text}</span>')
        elif line.startswith('@@'):
            lines.append(f'<span style="color:{theme.MUTED}">{text}</span>')
        else:
            lines.append(text)
    return ('<pre style="margin:0; font-family: Consolas, \'JetBrains Mono\', monospace; font-size:12px;">'
            + '<br>'.join(lines) + '</pre>')


class ReasoningRow(QWidget):
    """The model's thinking, folded away like a tool row."""

    def __init__(self, part: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toggle = QPushButton()
        self.toggle.setObjectName('ToolRow')
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.toggle.toggled.connect(self._toggled)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.text = QuestionText('')
        self.text.setObjectName('Reasoning')
        self.text.hide()
        layout.addWidget(self.text)
        self.update_part(part)

    def update_part(self, part: dict) -> None:
        self.part = part
        self.toggle.setText(('▾' if self.toggle.isChecked() else '▸') + ' 思考過程')
        source = f":::note\n{part.get('text', '')}\n:::"   # muted, smaller: it is not the answer
        if self.text.source != source:
            self.text.source = source
            if self.text.isVisible():
                self.text.refresh_theme()

    def _toggled(self, shown: bool) -> None:
        if shown:
            self.text.refresh_theme()
        self.text.setVisible(shown)
        self.update_part(self.part)


class ToolRow(QWidget):
    def __init__(self, part: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toggle = QPushButton()
        self.toggle.setObjectName('ToolRow')
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.toggle.toggled.connect(self._toggled)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail = label('', name='ToolDetail')
        self.detail.hide()
        layout.addWidget(self.detail)
        self.diff = QLabel()
        self.diff.setObjectName('DiffView')
        self.diff.setTextFormat(Qt.TextFormat.RichText)
        self.diff.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.diff.setWordWrap(True)
        self.diff.hide()
        layout.addWidget(self.diff)
        self.diff_text = ''
        self.update_part(part)

    def update_part(self, part: dict) -> None:
        self.part = part
        mark = {'error': ' ✕', 'running': ' …', 'pending': ' …'}.get(part.get('status', ''), '')
        summary = tool_summary(part)
        head = part.get('name') or 'tool'
        text = f'{head}  {summary}' if summary else head
        if len(text) > 90:
            text = text[:89] + '…'
        self.arrow = '▾' if self.toggle.isChecked() else '▸'
        self.toggle.setText(f'{self.arrow} {text}{mark}')
        diff = diff_of(part)
        hidden = {'old_string', 'new_string', 'oldString', 'newString', 'edits'} if diff else set()
        lines = []
        if part.get('input'):
            lines += [f'{k}: {v}' for k, v in part['input'].items()
                      if isinstance(v, (str, int, float, bool)) and k not in hidden]
        if part.get('output') and part.get('output') != diff:
            lines += ['', part['output']]
        if part.get('error'):
            lines += ['', part['error']]
        self.detail.setText('\n'.join(lines).strip())
        if diff != self.diff_text:
            self.diff_text = diff
            self.diff.setText(diff_html(diff) if diff else '')
        self.diff.setVisible(self.toggle.isChecked() and bool(diff))

    def _toggled(self, shown: bool) -> None:
        self.detail.setVisible(shown and bool(self.detail.text()))
        self.update_part(self.part)


def mini_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName('MiniButton')
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def copy_to_clipboard(text: str, b: QPushButton | None = None) -> None:
    QApplication.clipboard().setText(text)
    if b is not None:
        old = b.text()
        b.setText('已複製')
        QTimer.singleShot(1200, b, lambda: b.setText(old) if b.text() == '已複製' else None)


class CodeBlock(QFrame):
    """A fenced code block: monospace, unwrapped (scrolls sideways), with its own copy button."""

    def __init__(self, language: str, code: str):
        super().__init__()
        self.setObjectName('CodeBlock')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 2, 4, 0)
        self.language = QLabel()
        self.language.setObjectName('CodeLang')
        head.addWidget(self.language, 1)
        self.copy = mini_button('複製')
        self.copy.clicked.connect(lambda: copy_to_clipboard(self.code, self.copy))
        head.addWidget(self.copy)
        layout.addLayout(head)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setContentsMargins(10, 2, 10, 2)
        self.area = QScrollArea()
        self.area.setWidget(self.text)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.area)
        self.code = ''
        self.set_code(language, code)

    def set_code(self, language: str, code: str) -> None:
        self.code = code
        self.language.setText(language)
        self.text.setText(code)
        self.text.adjustSize()
        self._fit()

    def _fit(self) -> None:
        wide = self.text.sizeHint().width() > self.area.viewport().width()
        bar = self.area.horizontalScrollBar().sizeHint().height() if wide else 0
        self.area.setFixedHeight(self.text.sizeHint().height() + bar + 2)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit()


class RichText(QWidget):
    """Assistant Markdown, with fenced code split out into CodeBlocks. Updated in place while streaming."""

    def __init__(self, text: str):
        super().__init__()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)
        self.blocks: list[tuple[str, QWidget]] = []
        self.source: str | None = None
        self.set_text(text)

    def set_text(self, text: str) -> None:
        if text == self.source:
            return
        self.source = text
        segments = split_code(text)
        if [s[0] for s in segments] != [kind for kind, _ in self.blocks]:
            for _kind, widget in self.blocks:
                self.layout_.removeWidget(widget)
                widget.hide()
                widget.deleteLater()
            self.blocks = []
            for segment in segments:
                widget = QuestionText(segment[1]) if segment[0] == 'text' else CodeBlock(segment[1], segment[2])
                self.layout_.addWidget(widget)
                self.blocks.append((segment[0], widget))
            return
        for segment, (_kind, widget) in zip(segments, self.blocks):
            if segment[0] == 'text':
                if widget.source != segment[1]:
                    widget.source = segment[1]
                    widget.refresh_theme()
            elif widget.code != segment[2] or widget.language.text() != segment[1]:
                widget.set_code(segment[1], segment[2])


class MessageWidget(QWidget):
    """One transcript item; rebuilt in place when its item changes."""
    edit_requested = Signal(str)
    retry_requested = Signal()
    fork_requested = Signal(str)   # message id

    def __init__(self, item: dict):
        super().__init__()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.parts: dict[str, QWidget] = {}
        self.item = item
        self.actions: QWidget | None = None
        self.retry: QPushButton | None = None
        if item['kind'] == 'user':
            row = QHBoxLayout()
            row.addStretch(1)
            bubble = QFrame()
            bubble.setObjectName('UserBubble')
            bubble.setMaximumWidth(620)
            inner = QVBoxLayout(bubble)
            inner.setContentsMargins(14, 10, 14, 10)
            # Not the shared label(): its ignored width policy would collapse a bubble to one column.
            self.user_text = QLabel(item['text'])
            self.user_text.setWordWrap(True)
            self.user_text.setTextFormat(Qt.TextFormat.PlainText)
            self.user_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.user_text.setMaximumWidth(590)
            # A wrapped label guesses a narrow width; start from the longest line instead.
            longest = max((self.user_text.fontMetrics().horizontalAdvance(line) for line in item['text'].splitlines()),
                          default=0)
            self.user_text.setMinimumWidth(min(longest + 4, 560))
            inner.addWidget(self.user_text)
            self.user_text.setVisible(bool(item['text']))
            if item.get('files'):
                files = QLabel('　'.join(f'附件：{name}' for name in item['files']))
                files.setObjectName('FileNote')
                files.setWordWrap(True)
                inner.addWidget(files)
            row.addWidget(bubble, 0)
            self.layout_.addLayout(row)
            self._add_actions(right=True, edit=True)
        elif item['kind'] == 'end':
            text = {'failed': '失敗' + (f"：{item['error']}" if item.get('error') else ''),
                    'interrupted': '已停止'}.get(item['outcome'], '')
            row = QHBoxLayout()
            row.setSpacing(6)
            line = label(text, name='EndLine')
            line.setProperty('failed', item['outcome'] == 'failed')
            row.addWidget(line, 1)
            self.retry = mini_button('重試')
            self.retry.clicked.connect(self.retry_requested.emit)
            self.retry.hide()
            row.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignTop)
            self.layout_.addLayout(row)
            self.setVisible(bool(text))   # a normal finish needs no line, nor its spacing
        else:
            self._add_actions(right=False, edit=False)
            self.update_item(item)

    def _add_actions(self, right: bool, edit: bool) -> None:
        """Copy (and edit) under a message, shown while the pointer is over it; the space stays reserved."""
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        if right:
            row.addStretch(1)
        copy = mini_button('複製')
        copy.clicked.connect(lambda: copy_to_clipboard(self.plain_text(), copy))
        row.addWidget(copy)
        if edit:
            change = mini_button('編輯')
            change.clicked.connect(lambda: self.edit_requested.emit(self.item.get('text', '')))
            row.addWidget(change)
        else:
            fork = mini_button('分叉')
            fork.clicked.connect(lambda: self.fork_requested.emit(self.item['id']))
            row.addWidget(fork)
        if not right:
            row.addStretch(1)
        policy = self.actions.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.actions.setSizePolicy(policy)
        self.actions.hide()
        self.layout_.addWidget(self.actions)

    def plain_text(self) -> str:
        if self.item['kind'] == 'user':
            return self.item.get('text', '')
        return '\n\n'.join(p['text'] for p in self.item.get('parts', []) if p['type'] == 'text' and p['text'])

    def enterEvent(self, event):  # noqa: N802
        if self.actions is not None:
            self.actions.show()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if self.actions is not None:
            self.actions.hide()
        super().leaveEvent(event)

    def _insert(self, widget: QWidget) -> None:
        index = self.layout_.indexOf(self.actions) if self.actions is not None else -1
        if index < 0:
            self.layout_.addWidget(widget)
        else:
            self.layout_.insertWidget(index, widget)

    def update_item(self, item: dict) -> None:
        self.item = item
        for part in item['parts']:
            widget = self.parts.get(part['key'])
            if part['type'] == 'text':
                if widget is None:
                    widget = RichText(part['text'])
                    self.parts[part['key']] = widget
                    self._insert(widget)
                else:
                    widget.set_text(part['text'])
            elif part['type'] == 'reasoning':
                if widget is None:
                    widget = ReasoningRow(part)
                    self.parts[part['key']] = widget
                    self._insert(widget)
                else:
                    widget.update_part(part)
            else:
                if widget is None:
                    widget = ToolRow(part)
                    self.parts[part['key']] = widget
                    self._insert(widget)
                else:
                    widget.update_part(part)
        if item.get('error') and 'error' not in self.parts:
            err = label('失敗：' + item['error'], name='EndLine')
            err.setProperty('failed', True)
            self.parts['error'] = err
            self._insert(err)


# --------------------------------------------------------------------------- conversation page
class ConversationView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.key = ''
        self.transcript = Transcript()
        self.widgets: dict[str, MessageWidget] = {}
        self.older: str | None = None
        self.loading = False
        self._pending_items: set[str] = set()
        self._flush = QTimer(self, singleShot=True, interval=80)   # streaming text redraws at most ~12/s
        self._flush.timeout.connect(self._flush_items)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 12, 24, 12)
        self.title = ElidedLabel('')
        self.title.setObjectName('ConvTitle')
        head.addWidget(self.title, 1)
        self.project_chip = QPushButton()
        self.project_chip.setObjectName('ChatChip')
        self.project_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project_chip.clicked.connect(self._open_project)
        head.addWidget(self.project_chip)
        self.git_chip = QPushButton()
        self.git_chip.setObjectName('ChatChip')
        self.git_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.git_chip.clicked.connect(lambda: self.git_root and self.window_.open_git(self.git_root))
        self.git_chip.hide()
        head.addWidget(self.git_chip)
        self.git_root = ''
        self.context_chip = QLabel()
        self.context_chip.setObjectName('ChatChip')
        self.context_chip.hide()
        head.addWidget(self.context_chip)
        self.agent_chip = QLabel()
        self.agent_chip.setObjectName('ChatChip')
        head.addWidget(self.agent_chip)
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
        self.content.setMaximumWidth(820)
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.items = QVBoxLayout(self.content)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.items.setSpacing(14)
        self.more = button('載入更早的訊息', flat=True)
        self.more.clicked.connect(self.load_older)
        self.items.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label('', muted=True)
        self.items.addWidget(self.status)
        self.requests = QVBoxLayout()
        self.requests.setSpacing(10)
        self.items.addLayout(self.requests)
        self.items.addStretch(1)
        column.addWidget(self.content, 100)
        column.addStretch(1)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self._stick = True
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.latest = QPushButton(self.scroll)
        self.latest.setObjectName('LatestButton')
        self.latest.setAccessibleName('最新訊息')
        self.latest.setCursor(Qt.CursorShape.PointingHandCursor)
        self.latest.setFixedSize(34, 34)
        self.latest.clicked.connect(self.to_latest)
        self.latest.hide()
        self.composer = Composer(self, enter_sends=True)
        self.composer.recall = self._last_user_text
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
        self.find.setMaximumWidth(820)
        outer.addLayout(find_row)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(24, 8, 24, 18)
        wrap.addStretch(1)
        wrap.addWidget(self.composer, 100)
        wrap.addStretch(1)
        outer.addLayout(wrap)
        self.request_ids: tuple = ()
        self.forms: dict[str, FormBox] = {}
        self.permission_rows: list[QWidget] = []
        self.buffered: list[dict] = []
        self.last_state = ''

    # -- loading ---------------------------------------------------------------
    def open(self, key: str) -> None:
        if key == self.key:
            self.update_row()
            return
        self.composer.save_draft()
        self.key = key
        self.transcript = Transcript()
        for widget in self.widgets.values():
            self.items.removeWidget(widget)
            widget.deleteLater()
        self.widgets = {}
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
        self.git_root = ''
        self.git_chip.hide()
        self.composer.root = row.get('directory', '')
        self._commands(self.window_.store.dispatch.options.get(row.get('client', ''), {}))
        self.last_state = row.get('state', '')
        self.update_row()
        self._fetch(None)
        self.window_.request_options(row.get('client', ''), key, row.get('directory', ''))
        self.check_git()

    def check_git(self) -> None:
        """Branch and change count of the conversation's folder, read off the UI thread."""
        row = self.window_.store.dispatch.get(self.key) if self.key else None
        if row is not None:
            self.window_.git_status(self.key, row.get('directory', ''))

    def git_arrived(self, key: str, root: str, summary: str) -> None:
        if key != self.key:
            return
        self.git_root = root
        self.git_chip.setText(summary)
        self.git_chip.setVisible(bool(root))

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
        delete = menu.addAction('刪除…', lambda: self.window_.delete_conversation(self.key))
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

    def load_older(self) -> None:
        if self.older and not self.loading:
            self.loading = True
            self.more.setEnabled(False)
            self._fetch(self.older)

    def history_loaded(self, key: str, messages: list, older) -> None:
        if key != self.key:
            return
        first = not self.widgets
        bar = self.scroll.verticalScrollBar()
        from_bottom = bar.maximum() - bar.value()
        added = self.transcript.prepend(messages)
        index = self.items.indexOf(self.more) + 1
        for offset, item in enumerate(added):
            widget = self._widget(item)
            self.items.insertWidget(index + offset, widget)
        self.older = older
        self.loading = False
        self.more.setEnabled(True)
        self.more.setVisible(bool(older))
        self.status.hide()
        if first:
            self._stick = True
            events, self.buffered = self.buffered, []
            for event in events:   # what streamed in while the history was loading
                self.live_event(key, event)
            self.update_row()
        else:
            # Keep the reader's place when older messages appear above.
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - from_bottom))
        self._sync_retry()

    def live_event(self, key: str, event: dict) -> None:
        if key != self.key:
            return
        if self.loading and not self.widgets:
            self.buffered.append(event)
            return
        item = self.transcript.apply(event)
        if item is None:
            return
        if item['id'] not in self.widgets:
            widget = self._widget(item)
            self.items.insertWidget(self.items.indexOf(self.status), widget)
            self._sync_retry()
        else:
            self._pending_items.add(item['id'])
            if not self._flush.isActive():
                self._flush.start()

    def _widget(self, item: dict) -> MessageWidget:
        widget = MessageWidget(item)
        widget.edit_requested.connect(self.edit_text)
        widget.retry_requested.connect(self.retry)
        widget.fork_requested.connect(self.fork)
        self.widgets[item['id']] = widget
        return widget

    def _sync_retry(self) -> None:
        """Only the newest ending, when it failed or was stopped, offers to run the last message again."""
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

    def edit_text(self, text: str) -> None:
        """Put an earlier message back in the box to change and send it again."""
        box = self.composer.text
        box.setPlainText(text)
        box.setFocus()
        cursor = box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        box.setTextCursor(cursor)

    def _flush_items(self) -> None:
        for item_id in self._pending_items:
            widget = self.widgets.get(item_id)
            if widget and widget.item['kind'] == 'assistant':
                widget.update_item(widget.item)
        self._pending_items.clear()

    # -- state -----------------------------------------------------------------
    def update_row(self) -> None:
        row = self.window_.store.dispatch.get(self.key)
        if row is None:
            return
        self.title.setText(row['title'])
        self._commands(self.window_.store.dispatch.options.get(row['client'], {}))   # learned as it starts
        pid = self.window_.projects.project_of(row)
        project = self.window_.projects.get(pid) if pid else None
        self.project_chip.setIcon(icon('folder', theme.MUTED or '#888888', 14))
        self.project_chip.setText(project['name'] if project else '未分類')
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
            if self.last_state == 'running':
                self.check_git()   # the agent may have changed files
            self.last_state = row['state']
        running = row['state'] == 'running' and not row.get('permissions') and not row.get('forms')
        self.composer.set_running(row['state'] == 'running')
        if not self.loading:
            # Only OpenCode goes through a shared service that can be offline.
            offline = self.window_.store.dispatch.offline if row['client'] == 'opencode' else ''
            text = status_text({**row, 'offline': offline})
            show = running or row['state'] in ('unknown',) or bool(offline and row['state'] != 'done')
            self.status.setText(text if show else '')
            self.status.setVisible(show)
        self._sync_retry()
        ids = (tuple(row.get('permissions', {})), tuple(row.get('forms', {})))
        if ids != self.request_ids:
            self._rebuild_requests(row, ids)
        if not row['read'] and self.isVisible() and self.window().isActiveWindow():
            self.window_.store.queue_mark_read(row['owner'], f"session:{row['client']}:{row['session_id']}", [row['id']])

    def _rebuild_requests(self, row: dict, ids: tuple) -> None:
        self.request_ids = ids
        for widget in self.permission_rows + list(self.forms.values()):
            self.requests.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.permission_rows, self.forms = [], {}
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
            box = FormBox(self, form)
            box.setObjectName('Request')
            self.forms[fid] = box
            self.requests.addWidget(box)
        if ids != ((), ()):
            self._stick = True

    def answer_form(self, form_id: str, answer) -> None:
        self.window_.dispatcher.reply_form(self.key, form_id, answer)

    def send(self) -> None:
        text = self.composer.text.toPlainText().strip()
        if not self.key or not (text or self.composer.files):
            return
        if text == '/compact' and not self.composer.files:
            self.window_.dispatcher.compact(self.key)   # each Agent's own way to compact
        else:
            self.window_.dispatcher.send(self.key, text, self.composer.take_files())
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
        """The ↓ over the transcript's lower right while reading older messages."""
        bar = self.scroll.verticalScrollBar()
        away = bar.maximum() - bar.value() > 200
        if away:
            self.latest.setIcon(icon('down', theme.MUTED or '#888888', 16))
            view = self.scroll.viewport().geometry()
            self.latest.move(view.right() - self.latest.width() - 18, view.bottom() - self.latest.height() - 12)
            self.latest.raise_()
        self.latest.setVisible(away)

    def to_latest(self) -> None:
        self._stick = True
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_latest()

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self.scroll.verticalScrollBar().setValue(maximum)
        self._place_latest()


class SettingsBar(QObject):
    """Model, reasoning effort and permission mode for one conversation; '' leaves the client's default.

    Two chips as in desktop chat apps: `model_chip` (model and reasoning, one menu) and `mode_chip`; the
    composer places them."""
    changed = Signal(str, str)   # field, value
    DEFAULTS = {'model': '預設模型', 'effort': '預設推理', 'mode': '預設權限'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_chip = ChipButton()
        self.mode_chip = ChipButton('shield')
        self.model_chip.hide()
        self.mode_chip.hide()
        self.options: dict = {}
        self.current = {'model': '', 'effort': '', 'mode': ''}
        self.choices: dict[str, list[tuple[str, str]]] = {'model': [], 'effort': [], 'mode': []}

    def set_options(self, options: dict, current: dict, mode_locked: bool = False) -> None:
        self.options = options or {}
        self.current = {f: current.get(f, '') or '' for f in ('model', 'effort', 'mode')}
        self.choices['model'] = self._listed([(m['id'], m['label']) for m in self.options.get('models', [])],
                                             self.current['model'])
        self.choices['mode'] = self._listed([(m['id'], m['label']) for m in self.options.get('modes', [])],
                                            self.current['mode'])
        self._fill_efforts(keep=True)
        self.mode_chip.setEnabled(not mode_locked)
        self._render()

    @staticmethod
    def _listed(items: list[tuple[str, str]], value: str) -> list[tuple[str, str]]:
        if value and value not in [i for i, _t in items]:
            items = items + [(value, value)]   # chosen earlier, no longer listed: keep showing it
        return items

    def efforts(self) -> list[str]:
        return [e for e, _t in self.choices['effort']]

    def _fill_efforts(self, keep: bool) -> None:
        model = self.current['model']
        models = self.options.get('models', [])
        chosen = next((m for m in models if m['id'] == model), None)
        efforts: list[str] = list(chosen.get('efforts', [])) if chosen else []
        if not chosen:
            for m in models:   # the default model: what the listed models offer
                efforts += [e for e in m.get('efforts', []) if e not in efforts]
        value = self.current['effort']
        if not keep and value not in efforts:
            self.current['effort'] = value = ''   # the new model does not offer it
        self.choices['effort'] = self._listed([(e, EFFORT_LABELS.get(e, e)) for e in efforts], value)

    def _label(self, field: str) -> str:
        value = self.current[field]
        return next((t for i, t in self.choices[field] if i == value), '') if value else ''

    def _render(self) -> None:
        has_model = bool(self.choices['model'] or self.choices['effort'])
        model = self._label('model') or self.DEFAULTS['model']
        effort = self._label('effort')
        self.model_chip.setText(f'{model}  {effort}' if effort else model)
        self.model_chip.setVisible(has_model)
        self.mode_chip.setText(self._label('mode') or self.DEFAULTS['mode'])
        self.mode_chip.setVisible(bool(self.choices['mode']))
        self._menus()

    def _menus(self) -> None:
        from PySide6.QtGui import QActionGroup
        for chip, fields in ((self.model_chip, ('model', 'effort')), (self.mode_chip, ('mode',))):
            menu = chip.menu_
            menu.clear()
            for n, field in enumerate(fields):
                if not self.choices[field]:
                    continue
                if n:
                    menu.addSection('推理')
                group = QActionGroup(menu)
                for item_id, text in [('', self.DEFAULTS[field])] + self.choices[field]:
                    action = menu.addAction(text, lambda f=field, v=item_id: self.pick(f, v))
                    action.setCheckable(True)
                    action.setChecked(item_id == self.current[field])
                    group.addAction(action)

    def pick(self, field: str, value: str) -> None:
        """The user chose `value` for `field` (from a chip's menu)."""
        if self.current[field] == value:
            return
        self.current[field] = value
        if field == 'model':
            before = self.current['effort']
            self._fill_efforts(keep=False)
            if self.current['effort'] != before:
                self.changed.emit('effort', '')
        self._render()
        self.changed.emit(field, value)

    def values(self) -> dict:
        return dict(self.current)


SKIP_DIRS = {'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv', 'runtime', 'dist', 'build',
             '.pytest_cache', '.mypy_cache', '.idea', '.vs', 'target', 'obj', 'bin'}
_FILES: dict[str, tuple[float, list[str]]] = {}


def project_files(root: str, limit: int = 5000) -> list[str]:
    """Relative paths under `root` for @ mentions; cached briefly, heavy folders skipped."""
    cached = _FILES.get(root)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    found: list[str] = []
    try:
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.'))
            rel = os.path.relpath(base, root)
            for name in sorted(files):
                found.append(name if rel == '.' else os.path.join(rel, name).replace('\\', '/'))
            if len(found) >= limit:
                break
    except OSError:
        pass
    _FILES[root] = (time.monotonic(), found[:limit])
    return _FILES[root][1]


class Suggestions(QListWidget):
    """Choices for what is being typed (/ commands, @ files), shown inside the box above the text so typing
    keeps its focus."""
    LIMIT = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('Suggestions')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def show_items(self, items: list[tuple[str, str, str]]) -> None:
        """items: (shown text, muted note, text to insert)."""
        self.clear()
        for text, note, value in items[:self.LIMIT]:
            entry = QListWidgetItem(f'{text}    {note}' if note else text)
            entry.setData(ROLE, value)
            self.addItem(entry)
        if self.count():
            self.setCurrentRow(0)
            row = self.sizeHintForRow(0)
            self.setFixedHeight(min(self.count(), self.LIMIT) * row + 2 * self.frameWidth() + 4)
        self.setVisible(bool(self.count()))

    def step(self, delta: int) -> None:
        if self.count():
            self.setCurrentRow((self.currentRow() + delta) % self.count())

    def value(self) -> str | None:
        item = self.currentItem()
        return item.data(ROLE) if item else None


class Composer(QFrame):
    """The message box: text, attachments, the conversation's settings, send and stop."""
    submitted = Signal()

    def __init__(self, parent=None, enter_sends: bool = True, max_lines: int = 10, min_lines: int = 1):
        super().__init__(parent)
        self.setObjectName('Composer')
        self.setMaximumWidth(820)
        self.setAcceptDrops(True)
        self.drafts: dict[str, tuple[str, list[str]]] = {}
        self.draft_key = ''
        self.files: list[str] = []
        self.attach_dir: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 10, 10)
        layout.setSpacing(6)
        self.chips = QWidget()
        self.chip_row = QHBoxLayout(self.chips)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.chip_row.setSpacing(6)
        self.chips.hide()
        layout.addWidget(self.chips)
        self.suggest = Suggestions(self)
        self.suggest.itemClicked.connect(lambda _i: self._accept())
        layout.addWidget(self.suggest)
        self.commands: list[dict] = []   # {'name', 'description'}; typed after '/'
        self.root = ''                   # folder whose files '@' offers
        self.recall = None               # callable giving the last message, for ↑ in an empty box
        self.text = text_box(max_lines, enter_sends=enter_sends)
        self.text.key_hook = self._key
        self.text.attachments = True
        self.text.min_lines = min_lines
        self.text._fit()
        self.text.submitted.connect(self.submitted.emit)
        self.text.files_pasted.connect(self.add_files)
        self.text.image_pasted.connect(self.add_image)
        layout.addWidget(self.text)
        self.bar = QHBoxLayout()
        self.bar.setSpacing(6)
        self.attach = QPushButton()
        self.attach.setObjectName('IconButton')
        self.attach.setFixedSize(30, 30)
        self.attach.setToolTip('附加檔案')
        self.attach.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach.clicked.connect(self.pick_files)
        self.bar.addWidget(self.attach)
        self.settings = SettingsBar(self)
        self.bar.addWidget(self.settings.mode_chip)
        self.bar.addStretch(1)
        self.bar.addWidget(self.settings.model_chip)
        self.stop = RoundButton('stop', '停止', self)
        self.stop.setObjectName('StopButton')
        self.stop.hide()
        self.bar.addWidget(self.stop)
        self.send = RoundButton('up', '送出', self)
        self.send.setToolTip('Enter（Shift+Enter 換行）')
        self.send.clicked.connect(self.submitted.emit)
        self.bar.addWidget(self.send)
        self.running = False
        self.text.textChanged.connect(self._sync_buttons)
        self.text.cursorPositionChanged.connect(self._suggest)
        layout.addLayout(self.bar)
        self.restyle()

    def restyle(self) -> None:
        self.attach.setIcon(icon('plus', theme.MUTED or '#888888', 18))
        for chip in (self.settings.model_chip, self.settings.mode_chip, self.send, self.stop):
            chip.restyle()

    # -- typing helpers ------------------------------------------------------------
    def _token(self) -> tuple[str, int]:
        """The word being typed at the cursor and where it starts."""
        cursor = self.text.textCursor()
        block = cursor.block().text()
        column = cursor.positionInBlock()
        start = column
        while start > 0 and not block[start - 1].isspace():
            start -= 1
        return block[start:column], cursor.position() - (column - start)

    def _suggest(self) -> None:
        word, start = self._token()
        whole = self.text.toPlainText()
        items: list[tuple[str, str, str]] = []
        if word.startswith('/') and start == 0 and '\n' not in whole and ' ' not in whole:
            typed = word[1:].lower()
            items = [(f"/{c['name']}", c.get('description', ''), f"/{c['name']} ") for c in self.commands
                     if typed in c['name'].lower()]
            items.sort(key=lambda i: not i[0][1:].lower().startswith(typed))
        elif word.startswith('@') and self.root and os.path.isdir(self.root):
            typed = word[1:].lower().replace('\\', '/')
            files = project_files(self.root)
            hits = [f for f in files if typed in f.lower()] if typed else files
            hits.sort(key=lambda f: (typed not in os.path.basename(f).lower(), len(f)))
            items = [(f, '', f'@{f} ') for f in hits[:Suggestions.LIMIT]]
        if items != getattr(self, '_items', None):
            self._items = items
            self.suggest.show_items(items)
        elif not items:
            self.suggest.hide()

    def _accept(self) -> bool:
        value = self.suggest.value()
        if value is None:
            return False
        word, start = self._token()
        cursor = self.text.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + len(word), cursor.MoveMode.KeepAnchor)
        cursor.insertText(value)
        self.text.setTextCursor(cursor)
        self.suggest.hide()
        self._items = []
        return True

    def _key(self, event) -> bool:
        key = event.key()
        if self.suggest.isVisible():
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.suggest.step(-1 if key == Qt.Key.Key_Up else 1)
                return True
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                    and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                return self._accept()
            if key == Qt.Key.Key_Escape:
                self.suggest.hide()
                return True
        if key == Qt.Key.Key_Up and not event.modifiers() and not self.text.toPlainText() and self.recall:
            text = self.recall()
            if text:
                self.text.setPlainText(text)
                cursor = self.text.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.text.setTextCursor(cursor)
                return True
        return False

    # -- attachments -------------------------------------------------------------
    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, '附加檔案')
        self.add_files(paths)

    def add_files(self, paths) -> None:
        for path in paths:
            path = os.path.normpath(path)
            if os.path.isfile(path) and path not in self.files:
                self.files.append(path)
        self._chips()

    def add_image(self, image) -> None:
        """A pasted screenshot is saved next to AgentDock's other attachments and attached as a file."""
        if image is None or self.attach_dir is None:
            return
        self.attach_dir.mkdir(parents=True, exist_ok=True)
        path = self.attach_dir / f"paste-{time.strftime('%Y%m%d-%H%M%S')}-{len(self.files)}.png"
        if image.save(str(path), 'PNG'):
            self.add_files([str(path)])

    def remove_file(self, path: str) -> None:
        self.files = [f for f in self.files if f != path]
        self._chips()

    def _chips(self) -> None:
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for path in self.files:
            chip = QPushButton(f'{os.path.basename(path)}  ✕')
            chip.setObjectName('FileChip')
            chip.setToolTip(path)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, p=path: self.remove_file(p))
            self.chip_row.addWidget(chip)
        self.chip_row.addStretch(1)
        self.chips.setVisible(bool(self.files))
        self._sync_buttons()

    def take_files(self) -> list[str]:
        files, self.files = self.files, []
        self._chips()
        return files

    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    # -- state -------------------------------------------------------------------
    def set_running(self, running: bool) -> None:
        self.running = running
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """One round button as in desktop apps: stop while the Agent works and nothing is typed, otherwise
        send (typing while it works queues the message)."""
        typed = bool(self.text.toPlainText().strip() or self.files)
        self.stop.setVisible(self.running and not typed)
        self.send.setVisible(not self.running or typed)

    def save_draft(self) -> None:
        if self.draft_key:
            self.drafts[self.draft_key] = (self.text.toPlainText(), list(self.files))

    def load_draft(self, key: str) -> None:
        self.draft_key = key
        text, files = self.drafts.get(key, ('', []))
        self.text.setPlainText(text)
        self.files = list(files)
        self._chips()

    def clear(self) -> None:
        self.text.clear()
        self.suggest.hide()
        self.files = []
        self._chips()
        self.drafts.pop(self.draft_key, None)


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
        self.view.composer.text.setFocus()

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


# --------------------------------------------------------------------------- new conversation
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
    """As in desktop chat apps: a heading, the message box, then where it runs (project or folder) and
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
        column.setMaximumWidth(760)
        lay = QVBoxLayout(column)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        heading = QLabel('要做什麼？')
        heading.setObjectName('HomeTitle')
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(heading)
        lay.addSpacing(22)
        self.composer = Composer(self, enter_sends=True, max_lines=12, min_lines=2)
        self.composer.setMaximumWidth(16777215)
        self.composer.submitted.connect(self.send)
        self.composer.settings.changed.connect(self._setting_changed)
        lay.addWidget(self.composer)
        lay.addSpacing(6)
        place = QHBoxLayout()
        place.setContentsMargins(6, 0, 6, 0)   # chips line up with the box's own controls
        place.setSpacing(4)
        self.place = ChipButton('folder')
        place.addWidget(self.place)
        self.agent_chip = ChipButton('agent')
        place.addWidget(self.agent_chip)
        place.addStretch(1)
        lay.addLayout(place)
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
        if not self.folder_path and self.window_.store.dispatch.recent:
            self.folder_path = self.window_.store.dispatch.recent[0]
        self._place_changed()
        self.error.hide()
        self.refresh_recent()
        self.composer.text.setFocus()

    def set_folder(self, folder: str) -> None:
        """Work in a folder outside the projects."""
        self.folder_path = os.path.normpath(folder) if folder else ''
        self.pid = UNSORTED
        self._place_changed()

    def set_project(self, pid: str) -> None:
        self.pid = pid
        self._place_changed()

    def project(self) -> dict | None:
        return self.window_.projects.get(self.pid) if self.pid else None

    def _place_changed(self) -> None:
        project = self.project()
        if project:
            self.place.setText(project['name'])
            self.place.setToolTip(project['root'])
            self.pick_agent(project.get('agent') or 'opencode')
        else:
            self.place.setText(os.path.basename(self.folder_path.rstrip('\\/')) or self.folder_path or '專案或資料夾')
            self.place.setToolTip(self.folder_path)
            self.pick_agent(self.agent)
        self._place_menu()

    def _place_menu(self) -> None:
        from PySide6.QtGui import QActionGroup
        menu = self.place.menu_
        menu.clear()
        group = QActionGroup(menu)
        projects = self.window_.projects.list()
        current = self.project()
        if projects:
            menu.addSection('專案')
            for p in projects:
                action = menu.addAction(icon('folder', theme.MUTED or '#888888', 14), p['name'],
                                        lambda pid=p['id']: self.set_project(pid))
                action.setCheckable(True)
                action.setChecked(current is not None and p['id'] == current['id'])
                action.setToolTip(p['root'])
                group.addAction(action)
        recent = [f for f in self.window_.store.dispatch.recent[:6]]
        if self.folder_path and self.folder_path not in recent:
            recent.insert(0, self.folder_path)
        if recent:
            menu.addSection('資料夾')
            for folder in recent:
                action = menu.addAction(folder, lambda f=folder: self.set_folder(f))
                action.setCheckable(True)
                action.setChecked(current is None and folder == self.folder_path)
                group.addAction(action)
        menu.addSeparator()
        menu.addAction('選擇資料夾…', self.pick_folder)
        menu.addAction('新增專案…', self.window_.add_project)

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
        self.composer.settings.set_options(options, self._saved())
        self.composer.commands = list(options.get('commands', []))
        self.composer.root = self._folder_now()
        if ready and changed:
            self.window_.request_options(kind, '', self._folder_now())

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
            self.composer.settings.set_options(options, self._saved())
            self.composer.commands = list(options.get('commands', []))

    def _folder_now(self) -> str:
        project = self.project()
        return project['root'] if project else self.folder_path.strip().strip('"')

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

    def send(self) -> None:
        text = self.composer.text.toPlainText().strip()
        if not text or self.agent not in self.window_.dispatcher.clients:
            return
        project = self.project()
        if project:
            folder = project['root']
        else:
            if not self.folder_path.strip() and not self.pick_folder():
                return
            folder = self.folder_path.strip().strip('"')
        if not os.path.isdir(folder):
            self.error.setText('找不到這個資料夾。')
            self.error.show()
            return
        target = self.window_.target_for(self.agent)
        self.window_.dispatcher.start_task(os.path.normpath(folder), text, target['owner'],
                                           project=project['id'] if project else None,
                                           started=self.window_.bridge.started.emit, client=self.agent,
                                           settings=self.composer.settings.values(),
                                           files=self.composer.take_files())
        if not project and self.window_.ui_state.get('last_agent') != self.agent:
            self.window_.ui_state['last_agent'] = self.agent   # the next unsorted conversation starts with it
            self.window_.save_state()
        self.composer.clear()


# --------------------------------------------------------------------------- project page
class ProjectView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.pid = ''
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(32, 28, 32, 28)
        row.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(760)
        lay = QVBoxLayout(column)
        lay.setSpacing(18)
        head = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setObjectName('PageTitle')
        self.name.editingFinished.connect(self._rename)
        head.addWidget(self.name, 1)
        git = button('Git')
        git.clicked.connect(lambda: self.window_.open_git(self.root.text(), report=True))
        head.addWidget(git)
        adopt = button('匯入對話')
        adopt.clicked.connect(lambda: self.window_.import_conversations(self.pid))
        head.addWidget(adopt)
        new = button('＋ 在此專案開新對話', primary=True)
        new.clicked.connect(lambda: self.window_.show_new(self.pid))
        head.addWidget(new)
        lay.addLayout(head)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        root_row = QHBoxLayout()
        self.root = QLineEdit()
        self.root.setReadOnly(True)
        root_row.addWidget(self.root, 1)
        change = button('更換')
        change.clicked.connect(self._change_root)
        root_row.addWidget(change)
        form.addRow('根目錄', root_row)
        self.agent = QComboBox()
        for kind, name in AGENTS:
            self.agent.addItem(name, kind)
        self.agent.currentIndexChanged.connect(lambda _i: self._set_agent())
        form.addRow('預設 Agent', self.agent)
        lay.addLayout(form)
        section = QLabel('對話')
        section.setObjectName('SectionLabel')
        lay.addWidget(section)
        self.conversations = QVBoxLayout()
        self.conversations.setSpacing(6)
        lay.addLayout(self.conversations)
        remove = button('刪除專案', danger=True, flat=True)
        remove.clicked.connect(self._remove)
        lay.addWidget(remove, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        row.addWidget(column, 100)
        row.addStretch(1)
        scroll.setWidget(body)

    def show_project(self, pid: str) -> None:
        self.pid = pid
        self.refresh()

    def refresh(self) -> None:
        project = self.window_.projects.get(self.pid)
        if project is None:
            return
        self.name.setText(project['name'])
        self.root.setText(project['root'])
        self.agent.blockSignals(True)
        self.agent.setCurrentIndex(max(0, self.agent.findData(project.get('agent', 'opencode'))))
        self.agent.blockSignals(False)
        while self.conversations.count():
            widget = self.conversations.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        rows = sorted(((k, r) for k, r in self.window_.store.dispatch.rows.items()
                       if self.window_.projects.project_of(r) == self.pid),
                      key=lambda kv: kv[1]['updated_at'], reverse=True)
        for key, row in rows:
            b = button(f"{row['title']}    {Sidebar._badge(row)}", flat=True)
            b.setStyleSheet('text-align: left;')
            b.clicked.connect(lambda _c=False, k=key: self.window_.open_conversation(k))
            self.conversations.addWidget(b)
        if not rows:
            self.conversations.addWidget(label('還沒有對話。', muted=True))

    def _rename(self) -> None:
        try:
            self.window_.projects.update(self.pid, name=self.name.text())
        except (KeyError, ValueError) as e:
            self.window_.notify(str(e))

    def _change_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, '選擇根目錄', self.root.text())
        if chosen:
            try:
                self.window_.projects.update(self.pid, root=chosen)
            except (KeyError, ValueError) as e:
                self.window_.notify(str(e))

    def _set_agent(self) -> None:
        self.window_.projects.update(self.pid, agent=self.agent.currentData())

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


class DeleteDialog(QDialog):
    """Removing from AgentDock is the default; deleting the client's own history is a separate choice."""

    def __init__(self, parent, title: str, source: str):
        super().__init__(parent)
        self.setWindowTitle('刪除對話')
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        text = label(f'刪除「{title}」？')
        layout.addWidget(text)
        self.remote = QCheckBox(f'同時刪除 {source} 裡的紀錄')
        layout.addWidget(self.remote)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button('取消', flat=True)
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        delete = button('刪除', danger=True)
        delete.clicked.connect(self.accept)
        buttons.addWidget(delete)
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
        rows = sorted(w.store.dispatch.rows.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
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


class ChatWindow(QWidget):
    closed = Signal()
    page_changed = Signal(str)   # 'chat', or a page added with add_page

    def __init__(self, store, dispatcher, projects, targets, notify=lambda _m: None,
                 open_page=lambda _name: None, ui_state=None, save_state=lambda: None):
        super().__init__()
        self.store, self.dispatcher, self.projects = store, dispatcher, projects
        self.targets, self.notify, self.open_page = targets, notify, open_page
        self.ui_state = ui_state if ui_state is not None else {}
        self.save_state = save_state
        self.current_key = ''
        self.extra_pages: dict[str, QWidget] = {}
        self.setObjectName('ChatWindow')
        self.setWindowTitle('AgentDock')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setMinimumSize(QSize(900, 600))
        self.setAcceptDrops(True)
        self.bridge = Bridge()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(RAIL, 0, 0, 0)   # the rail floats over this margin
        layout.setSpacing(0)
        self.sidebar = Sidebar(self)
        self.pages = QStackedWidget()
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setObjectName('ChatSplit')
        self.split.setHandleWidth(4)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.sidebar)
        self.split.addWidget(self.pages)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        width = self.ui_state.get('sidebar_width')
        width = width if isinstance(width, int) and SIDEBAR[0] <= width <= SIDEBAR[2] else SIDEBAR[1]
        self.split.setSizes([width, 10000])
        self.split.splitterMoved.connect(lambda _p, _i: self.ui_state.__setitem__('sidebar_width', self.sidebar.width()))
        layout.addWidget(self.split, 1)
        self.new_view = NewConversationView(self)
        self.conversation = ConversationView(self)
        self.project_view = ProjectView(self)
        for page in (self.new_view, self.conversation, self.project_view):
            self.pages.addWidget(page)
        self.rail = Rail(self)
        self.rail.picked.connect(self._rail_picked)
        self.bridge.history.connect(self.conversation.history_loaded)
        self.bridge.history_failed.connect(self.conversation.history_failed)
        self.bridge.live.connect(self.conversation.live_event)
        self.bridge.started.connect(self.open_conversation)
        self.bridge.deleted.connect(self._deleted)
        self.bridge.options.connect(self._options_arrived)
        self.bridge.git.connect(self.conversation.git_arrived)
        self.git_windows: dict[str, QDialog] = {}
        attach_dir = Path(store.dispatch.file).parent / 'attachments' / 'chat'
        self.new_view.composer.attach_dir = self.conversation.composer.attach_dir = attach_dir
        dispatcher.listeners.append(self.bridge.live.emit)
        self._refresh = QTimer(self, singleShot=True, interval=120)
        self._refresh.timeout.connect(self.refresh)
        self.refresh_theme()
        geometry = self.ui_state.get('chat_geometry')
        if isinstance(geometry, list) and len(geometry) == 4:
            self.setGeometry(*geometry)
        else:
            self.resize(1180, 780)
        self._restored = False
        self.show_new()
        self.palette_ = SearchPalette(self)
        for keys, action in (('Ctrl+N', self.new_like_current), ('Ctrl+K', self.palette_.open),
                             ('Ctrl+B', self.toggle_sidebar),
                             ('Ctrl+F', self.find_in_conversation),
                             ('Escape', self.escape), ('Alt+Up', lambda: self.step_conversation(-1)),
                             ('Alt+Down', lambda: self.step_conversation(1))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)

    # -- navigation --------------------------------------------------------------
    def add_page(self, name: str, widget: QWidget) -> None:
        """Tools / settings live in the window; without one, the rail asks open_page instead."""
        self.extra_pages[name] = widget
        self.pages.addWidget(widget)

    def _chat_mode(self) -> None:
        if self.rail.current != 'chat':
            self.rail.set_current('chat')
            self.page_changed.emit('chat')
        self._show_list(not self.ui_state.get('sidebar_hidden'))

    def _show_list(self, shown: bool) -> None:
        self.sidebar.setVisible(shown)
        self.rail.buttons['new'].setVisible(not shown and self.rail.current == 'chat')

    def toggle_sidebar(self) -> None:
        """Ctrl+B, or 聊天 on the rail while chatting: hide or show the conversation list."""
        if self.pages.currentWidget() in self.extra_pages.values():
            self.show_page('chat')
            return
        hidden = not self.sidebar.isHidden()
        self.ui_state['sidebar_hidden'] = hidden
        self.save_state()
        self._show_list(not hidden)

    def show_new(self, project: str | None = None, agent: str | None = None, folder: str | None = None) -> None:
        self._chat_mode()
        self.current_key = ''
        if folder:
            self.new_view.set_folder(folder)
        self.new_view.prepare(project)
        if agent:
            self.new_view.pick_agent(agent)
        self.pages.setCurrentWidget(self.new_view)
        self.sidebar.refresh()

    def open_conversation(self, key: str) -> None:
        if self.store.dispatch.get(key) is None:
            return
        self._chat_mode()
        self.current_key = key
        self.ui_state['last_conversation'] = key
        self.pages.setCurrentWidget(self.conversation)
        self.conversation.open(key)
        self.sidebar.refresh()

    def show_project(self, pid: str) -> None:
        self._chat_mode()
        self.current_key = ''
        self.project_view.show_project(pid)
        self.pages.setCurrentWidget(self.project_view)
        self.sidebar.refresh()

    def show_page(self, name: str) -> None:
        if name == 'chat':
            if self.pages.currentWidget() in self.extra_pages.values():
                if self.current_key and self.store.dispatch.get(self.current_key):
                    self.open_conversation(self.current_key)
                else:
                    self.show_new()
            return
        if name not in self.extra_pages:
            self.open_page(name)
            return
        self.rail.set_current(name)
        self._show_list(False)
        self.rail.buttons['new'].hide()
        self.pages.setCurrentWidget(self.extra_pages[name])
        self.page_changed.emit(name)

    def _rail_picked(self, name: str) -> None:
        if name == 'new':
            self.show_new()
        elif name == 'chat' and self.pages.currentWidget() not in self.extra_pages.values():
            self.toggle_sidebar()
        else:
            self.show_page(name)

    # -- actions -----------------------------------------------------------------
    def add_project(self) -> None:
        dialog = ProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            project = self.projects.add(dialog.name.text() or os.path.basename(dialog.root.text()), dialog.root.text())
        except ValueError as e:
            self.notify(str(e))
            return
        self.show_project(project['id'])

    def move_conversation(self, key: str, project: str) -> None:
        self.store.dispatch.set_project(key, project)
        self.refresh()

    def rename_conversation(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        dialog = RenameDialog(self, row['title'])
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.name.text().strip():
            self.store.dispatch.rename(key, dialog.name.text())
            self.refresh()

    def delete_conversation(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        if row['state'] == 'running':
            self.notify('這個對話還在執行，請先停止。')
            return
        dialog = DeleteDialog(self, row['title'], row['source'])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.remote.isChecked():
            # Removed from the list only after the client has deleted it.
            self.dispatcher.delete(key, True, done=lambda: self.bridge.deleted.emit(key))
        else:
            self._deleted(key)

    def _deleted(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        self.store.remove_conversation(row['owner'], work_id(row))
        if self.ui_state.get('last_conversation') == key:
            self.ui_state.pop('last_conversation', None)
        if self.current_key == key:
            self.conversation.key = ''
            self.show_new()
        self.refresh()

    def import_conversations(self, project: str | None = None) -> None:
        row = self.store.dispatch.get(self.current_key) if self.current_key else None
        agent = (row or {}).get('client') or ((self.projects.get(project) or {}).get('agent') if project else None) \
            or self.ui_state.get('last_agent') or AGENTS[0][0]
        ImportDialog(self, project, agent).exec()

    def git_status(self, key: str, directory: str) -> None:
        import threading

        from agentdock import gitops

        def run():
            root = gitops.repo_root(directory)
            summary = ''
            if root:
                try:
                    summary = gitops.status(root).summary
                except gitops.GitError:
                    root = ''
            self.bridge.git.emit(key, root or '', summary)
        threading.Thread(target=run, name='AgentDock git status', daemon=True).start()

    def open_git(self, directory: str, report: bool = False) -> None:
        from agentdock import gitops
        from agentdock.ui.git_view import GitDialog
        root = gitops.repo_root(directory)
        if not root:
            if report:
                self.notify('這個資料夾不是 Git 儲存庫。')
            return
        dialog = self.git_windows.get(root)
        if dialog is None:
            dialog = GitDialog(self, root)
            dialog.finished.connect(lambda _r, r=root: (self.git_windows.pop(r, None), self.conversation.check_git()))
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.git_windows[root] = dialog
        else:
            dialog.reload()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def resume_in_terminal(self, key: str) -> None:
        """Continue the session in the client's own terminal UI. The one place a window is wanted."""
        row = self.store.dispatch.get(key)
        command = resume_command(row) if row else None
        if not command:
            self.notify(f"找不到 {row['source'] if row else ''} 的執行檔。")
            return
        folder = row.get('directory') if os.path.isdir(row.get('directory') or '') else None
        try:
            subprocess.Popen(command, cwd=folder, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        except OSError as e:
            self.notify(f'無法開啟終端機：{e}')

    # -- keyboard ------------------------------------------------------------------
    def new_like_current(self) -> None:
        """Ctrl+N: a new conversation where the open one is (same project or folder, same agent)."""
        row = self.store.dispatch.get(self.current_key) if self.pages.currentWidget() is self.conversation else None
        if row is None:
            self.show_new()
        else:
            pid = self.projects.project_of(row)
            self.show_new(pid, agent=row['client'], folder=None if pid else row.get('directory'))
        self.new_view.composer.text.setFocus()

    def request_options(self, client: str, key: str, directory: str) -> None:
        if client:
            self.dispatcher.options(client, key or None, directory,
                                    lambda options: self.bridge.options.emit(client, key or '', options))

    def _options_arrived(self, client: str, key: str, options: dict) -> None:
        if key:
            self.conversation.options_arrived(key, options)
        else:
            self.new_view.options_arrived(client, options)

    def find_in_conversation(self) -> None:
        if self.pages.currentWidget() is self.conversation and self.current_key:
            self.conversation.find.open_bar()

    def focus_search(self) -> None:
        if self.sidebar.isHidden():
            self.show_page('chat')
            if self.sidebar.isHidden():
                self.toggle_sidebar()
        self.sidebar.search.setFocus()
        self.sidebar.search.selectAll()

    def escape(self) -> None:
        if self.palette_.isVisible():
            self.palette_.hide()
            return
        if self.sidebar.search.hasFocus() and self.sidebar.search.text():
            self.sidebar.search.clear()
            return
        if self.conversation.find.isVisible():
            self.conversation.find.close_bar()
            return
        row = self.store.dispatch.get(self.current_key) if self.pages.currentWidget() is self.conversation else None
        if row and row['state'] == 'running':
            self.dispatcher.interrupt(self.current_key)

    def step_conversation(self, step: int) -> None:
        keys = self.sidebar.keys()
        if not keys:
            return
        if self.current_key in keys:
            index = max(0, min(len(keys) - 1, keys.index(self.current_key) + step))
        else:
            index = 0 if step > 0 else len(keys) - 1
        self.open_conversation(keys[index])

    def target_for(self, kind: str) -> dict:
        # Without a registered profile the owner still matches what the client's hooks would use.
        return next((t for t in self.targets() if t['client'] == kind),
                    {'label': dict(AGENTS).get(kind, kind), 'client': kind, 'owner': owner_for(None, kind)})

    # -- updates -----------------------------------------------------------------
    def schedule_refresh(self) -> None:
        if not self._refresh.isActive():
            self._refresh.start()

    def refresh(self) -> None:
        self.sidebar.refresh()
        if self.pages.currentWidget() is self.conversation:
            self.conversation.update_row()
        elif self.pages.currentWidget() is self.project_view:
            self.project_view.refresh()
        elif self.pages.currentWidget() is self.new_view:
            self.new_view.refresh_recent()

    def refresh_theme(self) -> None:
        self.setStyleSheet(_style())
        for composer in (self.new_view.composer, self.conversation.composer):
            composer.restyle()
        for chip in (self.new_view.place, self.new_view.agent_chip):
            chip.restyle()
        self.conversation.more_button.setIcon(icon('more', theme.MUTED or '#888888', 18))
        self.rail.set_current(self.rail.current)
        self.sidebar.refresh()
        for widget in self.extra_pages.values():
            if hasattr(widget, 'restyle'):
                widget.restyle()

    def remember(self) -> None:
        g = self.geometry()
        self.ui_state['chat_geometry'] = [g.x(), g.y(), g.width(), g.height()]
        self.save_state()

    # -- Qt -----------------------------------------------------------------------
    def _drop_target(self):
        page = self.pages.currentWidget()
        if page is self.new_view:
            return self.new_view.composer
        if page is self.conversation and self.current_key:
            return self.conversation.composer
        return None

    def dragEnterEvent(self, event):  # noqa: N802
        """Files dropped anywhere on a conversation go to its message box."""
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if self._drop_target() is not None and urls and all(u.isLocalFile() for u in urls):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event):  # noqa: N802
        target = self._drop_target()
        if target is not None:
            target.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            target.text.setFocus()
            event.acceptProposedAction()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.rail.setGeometry(0, 0, self.rail.width(), self.height())
        self.rail.raise_()
        palette = getattr(self, 'palette_', None)
        if palette is not None and palette.isVisible():
            palette.open()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # Back to the conversation that was open last time; only when the window is first shown, so a
        # start in the tray never loads anything.
        if not self._restored:
            self._restored = True
            last = self.ui_state.get('last_conversation')
            if last and self.pages.currentWidget() is self.new_view and self.store.dispatch.get(last):
                self.open_conversation(last)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.conversation.update_row()

    def closeEvent(self, event):  # noqa: N802
        self.conversation.composer.save_draft()
        self.remember()
        self.hide()
        event.ignore()   # keep the window and its drafts; the app owns its lifetime
        self.closed.emit()
