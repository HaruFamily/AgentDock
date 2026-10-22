"""The conversation window: projects on the left, the whole conversation on the right.

The floating card stays for notices and quick replies; this window is where conversations are read
and continued. Approvals, answers, sends and stops only happen on a click, as in the Inbox.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from agentdock.dispatch.projects import UNSORTED
from agentdock.dispatch.transcript import Transcript, tool_summary
from agentdock.ui import theme
from agentdock.ui.dispatch_view import FormBox, status_text, text_box
from agentdock.ui.qa_view import button, label
from agentdock.ui.question_text import QuestionText
from agentdock.ui.widgets import ElidedLabel, icon_button

ROLE = Qt.ItemDataRole.UserRole
AGENTS = (('opencode', 'OpenCode'), ('claude-code', 'Claude Code'), ('codex', 'Codex'))
RAIL = 56
RAIL_OPEN = 188


def _style() -> str:
    t = theme
    return f"""
#ChatWindow {{ background: {t.CANVAS}; }}
#ChatRail {{ background: {t.CARD}; border-right: 1px solid {t.LINE}; }}
#ChatRail QPushButton {{ text-align: left; border: none; border-radius: 10px; padding: 10px 12px; background: transparent; }}
#ChatRail QPushButton:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatRail QPushButton[current="true"] {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DEEP}; font-weight: 700; }}
#ChatSidebar {{ background: {t.CARD}; border-right: 1px solid {t.LINE}; }}
#ChatSidebar QTreeWidget {{ background: transparent; border: none; outline: none; }}
#ChatSidebar QTreeWidget::item {{ min-height: 30px; border-radius: 8px; padding: 2px 4px; }}
#ChatSidebar QTreeWidget::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.INK}; }}
#ChatSidebar QTreeWidget::item:hover {{ background: {t.HOVER}; }}
#SectionLabel {{ color: {t.MUTED}; font-size: 12px; }}
#UserBubble {{ background: {t.ACCENT_SOFT}; border: 1px solid {t.LINE}; border-radius: 14px; }}
#ToolRow {{ text-align: left; border: 1px solid {t.LINE}; border-radius: 8px; padding: 4px 10px; background: {t.CARD}; color: {t.MUTED}; }}
#ToolDetail {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 8px; padding: 8px; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 12px; }}
#Request {{ background: {t.WARN_BG}; border: 1px solid {t.LINE}; border-radius: 12px; }}
#Composer {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 16px; }}
#Composer QPlainTextEdit {{ border: none; background: transparent; }}
#ConvTitle {{ font-size: 16px; font-weight: 700; }}
#PageTitle {{ font-size: 22px; font-weight: 700; }}
#ChatChip {{ border: 1px solid {t.LINE}; border-radius: 11px; padding: 2px 10px; color: {t.MUTED}; font-size: 12px; background: transparent; }}
#EndLine {{ color: {t.MUTED}; font-size: 12px; }}
#EndLine[failed="true"] {{ color: {t.DANGER}; }}
"""


class Bridge(QObject):
    """Worker-thread callbacks into the UI thread."""
    history = Signal(str, list, object)
    history_failed = Signal(str, str)
    live = Signal(str, dict)
    started = Signal(str)


ICONS = {
    'chat': 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z',
    'tools': 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z',
    'settings': 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
    'folder': 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
}


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


# --------------------------------------------------------------------------- rail
class Rail(QFrame):
    """Collapsed to icons; widens over the list while the mouse is on it."""
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('ChatRail')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)
        self.buttons = {}
        for name, text in (('chat', '聊天'), ('tools', '工具')):
            layout.addWidget(self._button(name, text))
        layout.addStretch(1)
        layout.addWidget(self._button('settings', '設定'))
        self.set_open(False)
        self.current = 'chat'
        self.set_current('chat')

    def _button(self, name, text):
        b = QPushButton()
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(text)
        b.setIconSize(QSize(18, 18))
        b.setProperty('label', text)
        b.clicked.connect(lambda: self.picked.emit(name))
        self.buttons[name] = b
        return b

    def set_open(self, opened: bool) -> None:
        self.opened = opened
        for b in self.buttons.values():
            b.setText('  ' + b.property('label') if opened else '')
        self.setFixedWidth(RAIL_OPEN if opened else RAIL)
        if opened:
            self.raise_()

    def set_current(self, name: str) -> None:
        self.current = name
        for key, b in self.buttons.items():
            b.setIcon(icon(key, (theme.ACCENT_DEEP if key == name else theme.MUTED) or '#888888'))
            b.setProperty('current', key == name)
            b.style().unpolish(b)
            b.style().polish(b)

    def enterEvent(self, event):  # noqa: N802
        self.set_open(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.set_open(False)
        super().leaveEvent(event)


# --------------------------------------------------------------------------- sidebar
class ConversationTree(QTreeWidget):
    """Projects with their conversations. Dropping a conversation on a project moves it there."""
    moved = Signal(str, str)

    def __init__(self):
        super().__init__()
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
        self.setFixedWidth(270)
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
        add = icon_button('＋', '新增專案')
        add.clicked.connect(window.add_project)
        head.addWidget(add)
        layout.addLayout(head)
        self.tree = ConversationTree()
        self.tree.itemClicked.connect(self._clicked)
        self.tree.moved.connect(window.move_conversation)
        layout.addWidget(self.tree, 1)

    def _clicked(self, item, _column):
        kind, value = item.data(0, ROLE) or ('', '')
        if kind == 'conv':
            self.window_.open_conversation(value)
        elif kind == 'project' and value:
            self.window_.show_project(value)

    def refresh(self) -> None:
        w = self.window_
        query = self.search.text().strip().lower()
        expanded = {self.tree.topLevelItem(i).data(0, ROLE)[1]: self.tree.topLevelItem(i).isExpanded()
                    for i in range(self.tree.topLevelItemCount())}
        rows = sorted(w.store.dispatch.rows.items(), key=lambda kv: kv[1]['updated_at'], reverse=True)
        groups: dict[str, list] = {}
        for key, row in rows:
            if query and query not in row['title'].lower():
                continue
            groups.setdefault(w.projects.project_of(row), []).append((key, row))
        self.tree.clear()
        entries = [(p['id'], p['name']) for p in w.projects.list()] + [(UNSORTED, '未分類')]
        for pid, name in entries:
            convs = groups.get(pid, [])
            if pid == UNSORTED and not convs:
                continue
            top = QTreeWidgetItem([name, str(len(convs)) if convs else ''])
            top.setData(0, ROLE, ('project', pid))
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsSelectable)
            if pid:
                top.setToolTip(0, w.projects.get(pid)['root'])
            self.tree.addTopLevelItem(top)
            for key, row in convs:
                badge = self._badge(row)
                child = QTreeWidgetItem([row['title'], badge])
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
            top.setExpanded(expanded.get(pid, True) or bool(query))

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
        lines = []
        if part.get('input'):
            lines += [f'{k}: {v}' for k, v in part['input'].items() if isinstance(v, (str, int, float, bool))]
        if part.get('output'):
            lines += ['', part['output']]
        if part.get('error'):
            lines += ['', part['error']]
        self.detail.setText('\n'.join(lines).strip())

    def _toggled(self, shown: bool) -> None:
        self.detail.setVisible(shown and bool(self.detail.text()))
        self.update_part(self.part)


class MessageWidget(QWidget):
    """One transcript item; rebuilt in place when its item changes."""

    def __init__(self, item: dict):
        super().__init__()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.parts: dict[str, QWidget] = {}
        self.item = item
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
            row.addWidget(bubble, 0)
            self.layout_.addLayout(row)
        elif item['kind'] == 'end':
            text = {'failed': '失敗' + (f"：{item['error']}" if item.get('error') else ''),
                    'interrupted': '已停止'}.get(item['outcome'], '')
            line = label(text, name='EndLine')
            line.setProperty('failed', item['outcome'] == 'failed')
            self.layout_.addWidget(line)
            self.setVisible(bool(text))   # a normal finish needs no line, nor its spacing
        else:
            self.update_item(item)

    def update_item(self, item: dict) -> None:
        self.item = item
        for part in item['parts']:
            widget = self.parts.get(part['key'])
            if part['type'] == 'text':
                if widget is None:
                    widget = QuestionText(part['text'])
                    self.parts[part['key']] = widget
                    self.layout_.addWidget(widget)
                elif widget.source != part['text']:
                    widget.source = part['text']
                    widget.refresh_theme()
            else:
                if widget is None:
                    widget = ToolRow(part)
                    self.parts[part['key']] = widget
                    self.layout_.addWidget(widget)
                else:
                    widget.update_part(part)
        if item.get('error') and 'error' not in self.parts:
            err = label('失敗：' + item['error'], name='EndLine')
            err.setProperty('failed', True)
            self.parts['error'] = err
            self.layout_.addWidget(err)


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
        self.agent_chip = QLabel()
        self.agent_chip.setObjectName('ChatChip')
        head.addWidget(self.agent_chip)
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
        self.composer = Composer(self, enter_sends=True)
        self.composer.submitted.connect(self.send)
        self.composer.stop.clicked.connect(lambda: self.window_.dispatcher.interrupt(self.key))
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
        self.update_row()
        self._fetch(None)

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
            widget = MessageWidget(item)
            self.widgets[item['id']] = widget
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
            widget = MessageWidget(item)
            self.widgets[item['id']] = widget
            self.items.insertWidget(self.items.indexOf(self.status), widget)
        else:
            self._pending_items.add(item['id'])
            if not self._flush.isActive():
                self._flush.start()

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
        pid = self.window_.projects.project_of(row)
        project = self.window_.projects.get(pid) if pid else None
        self.project_chip.setIcon(icon('folder', theme.MUTED or '#888888', 14))
        self.project_chip.setText(project['name'] if project else '未分類')
        self.project_chip.setToolTip(row.get('directory', ''))
        self.agent_chip.setText(row['source'])
        running = row['state'] == 'running' and not row.get('permissions') and not row.get('forms')
        self.composer.set_running(row['state'] == 'running')
        if not self.loading:
            text = status_text({**row, 'offline': self.window_.store.dispatch.offline})
            show = running or row['state'] in ('unknown',) or bool(self.window_.store.dispatch.offline and row['state'] != 'done')
            self.status.setText(text if show else '')
            self.status.setVisible(show)
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
        if text and self.key:
            self.window_.dispatcher.send(self.key, text)
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

    def _range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self.scroll.verticalScrollBar().setValue(maximum)


class Composer(QFrame):
    submitted = Signal()

    def __init__(self, parent=None, enter_sends: bool = True, max_lines: int = 10):
        super().__init__(parent)
        self.setObjectName('Composer')
        self.setMaximumWidth(820)
        self.drafts: dict[str, str] = {}
        self.draft_key = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(6)
        self.text = text_box(max_lines, enter_sends=enter_sends)
        self.text.submitted.connect(self.submitted.emit)
        layout.addWidget(self.text)
        self.bar = QHBoxLayout()
        self.bar.setSpacing(8)
        self.bar.addStretch(1)
        self.stop = button('■ 停止', danger=True)
        self.stop.hide()
        self.bar.addWidget(self.stop)
        self.send = button('送出', primary=True)
        self.send.setToolTip('Enter（Shift+Enter 換行）')
        self.send.clicked.connect(self.submitted.emit)
        self.bar.addWidget(self.send)
        layout.addLayout(self.bar)

    def set_running(self, running: bool) -> None:
        self.stop.setVisible(running)

    def save_draft(self) -> None:
        if self.draft_key:
            self.drafts[self.draft_key] = self.text.toPlainText()

    def load_draft(self, key: str) -> None:
        self.draft_key = key
        self.text.setPlainText(self.drafts.get(key, ''))

    def clear(self) -> None:
        self.text.clear()
        self.drafts.pop(self.draft_key, None)


# --------------------------------------------------------------------------- new conversation
class NewConversationView(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 32, 32, 32)
        outer.addStretch(2)
        column = QWidget()
        column.setMaximumWidth(760)
        lay = QVBoxLayout(column)
        lay.setSpacing(16)
        heading = QLabel('要做什麼？')
        heading.setObjectName('PageTitle')
        heading.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(heading)
        chips = QHBoxLayout()
        chips.addStretch(1)
        self.agent_buttons = {}
        for kind, name in AGENTS:
            b = button(name)
            b.setProperty('seg', True)
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=kind: self.pick_agent(k))
            chips.addWidget(b)
            self.agent_buttons[kind] = b
        chips.addStretch(1)
        lay.addLayout(chips)
        self.composer = Composer(self, enter_sends=True, max_lines=12)
        self.composer.submitted.connect(self.send)
        self.project = QComboBox()
        self.project.setMinimumWidth(200)
        self.project.currentIndexChanged.connect(lambda _i: self._project_changed())
        self.composer.bar.insertWidget(0, self.project)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('資料夾')
        self.folder.setMinimumWidth(220)
        self.composer.bar.insertWidget(1, self.folder)
        self.browse = icon_button('…', '選擇資料夾')
        self.browse.clicked.connect(self.pick_folder)
        self.composer.bar.insertWidget(2, self.browse)
        lay.addWidget(self.composer)
        self.where = label('', muted=True)
        self.where.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.where)
        self.error = label('', muted=True)
        self.error.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.error.hide()
        lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(column, 100)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        self.agent = 'opencode'

    def prepare(self, project: str | None = None) -> None:
        self.project.blockSignals(True)
        current = project if project is not None else self.project.currentData()
        self.project.clear()
        for p in self.window_.projects.list():
            self.project.addItem(icon('folder', theme.MUTED or '#888888', 14), p['name'], p['id'])
        self.project.addItem('未分類', UNSORTED)
        index = self.project.findData(current) if current is not None else 0
        self.project.setCurrentIndex(max(index, 0))
        self.project.blockSignals(False)
        if not self.folder.text() and self.window_.store.dispatch.recent:
            self.folder.setText(self.window_.store.dispatch.recent[0])
        self._project_changed()
        self.error.hide()
        self.composer.text.setFocus()

    def _project_changed(self) -> None:
        pid = self.project.currentData()
        project = self.window_.projects.get(pid) if pid else None
        self.folder.setVisible(project is None)
        self.browse.setVisible(project is None)
        if project:
            self.pick_agent(project.get('agent') or 'opencode')
        self.where.setText(project['root'] if project else '')
        self.where.setVisible(project is not None)
        if project is None:
            self.pick_agent(self.agent)

    def pick_agent(self, kind: str) -> None:
        ready = kind in self.window_.dispatcher.clients
        for key, b in self.agent_buttons.items():
            b.setChecked(key == kind)
            b.setEnabled(key in self.window_.dispatcher.clients or key == kind)
        self.agent = kind
        self.error.setText('' if ready else f'{dict(AGENTS)[kind]} 尚未支援。')
        self.error.setVisible(not ready)
        self.composer.send.setEnabled(ready)

    def pick_folder(self) -> bool:
        start = self.folder.text() if os.path.isdir(self.folder.text()) else os.path.expanduser('~')
        chosen = QFileDialog.getExistingDirectory(self, '選擇資料夾', start)
        if chosen:
            self.folder.setText(os.path.normpath(chosen))
        return bool(chosen)

    def send(self) -> None:
        text = self.composer.text.toPlainText().strip()
        if not text or self.agent not in self.window_.dispatcher.clients:
            return
        pid = self.project.currentData()
        project = self.window_.projects.get(pid) if pid else None
        if project:
            folder = project['root']
        else:
            if not self.folder.text().strip() and not self.pick_folder():
                return
            folder = self.folder.text().strip().strip('"')
        if not os.path.isdir(folder):
            self.error.setText('找不到這個資料夾。')
            self.error.show()
            return
        target = self.window_.target_for(self.agent)
        self.window_.dispatcher.start_task(os.path.normpath(folder), text, target['owner'],
                                           project=pid if project else None,
                                           started=self.window_.bridge.started.emit)
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


# --------------------------------------------------------------------------- window
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
        self.bridge = Bridge()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(RAIL, 0, 0, 0)   # the rail floats over this margin
        layout.setSpacing(0)
        self.sidebar = Sidebar(self)
        layout.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
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
        dispatcher.listeners.append(self.bridge.live.emit)
        self._refresh = QTimer(self, singleShot=True, interval=120)
        self._refresh.timeout.connect(self.refresh)
        self.refresh_theme()
        geometry = self.ui_state.get('chat_geometry')
        if isinstance(geometry, list) and len(geometry) == 4:
            self.setGeometry(*geometry)
        else:
            self.resize(1180, 780)
        self.show_new()

    # -- navigation --------------------------------------------------------------
    def add_page(self, name: str, widget: QWidget) -> None:
        """Tools / settings live in the window; without one, the rail asks open_page instead."""
        self.extra_pages[name] = widget
        self.pages.addWidget(widget)

    def _chat_mode(self) -> None:
        if self.sidebar.isHidden() or self.rail.current != 'chat':
            self.sidebar.show()
            self.rail.set_current('chat')
            self.page_changed.emit('chat')

    def show_new(self, project: str | None = None) -> None:
        self._chat_mode()
        self.current_key = ''
        self.new_view.prepare(project)
        self.pages.setCurrentWidget(self.new_view)
        self.sidebar.refresh()

    def open_conversation(self, key: str) -> None:
        if self.store.dispatch.get(key) is None:
            return
        self._chat_mode()
        self.current_key = key
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
        self.sidebar.hide()
        self.rail.set_current(name)
        self.pages.setCurrentWidget(self.extra_pages[name])
        self.page_changed.emit(name)

    def _rail_picked(self, name: str) -> None:
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

    def target_for(self, kind: str) -> dict:
        return next((t for t in self.targets() if t['client'] == kind), self.targets()[0])

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

    def refresh_theme(self) -> None:
        self.setStyleSheet(_style())
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
    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.rail.setGeometry(0, 0, self.rail.width(), self.height())
        self.rail.raise_()

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
