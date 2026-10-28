"""Messages drawn with Qt widgets (used when the web page is off): text, code, tools, thinking."""
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
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
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
from agentdock.ui.chat_common import GAP, copy_to_clipboard, fold_mark, hidden, mini_button, paint_decor, split_code


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
        self.toggle.clicked.connect(lambda: setattr(self, 'touched', True))
        self.touched = False   # once clicked, the reader decides whether it is open
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.text = QuestionText('')
        self.text.setObjectName('Reasoning')
        self.text.hide()
        layout.addWidget(self.text)
        self.update_part(part)

    def update_part(self, part: dict) -> None:
        self.part = part
        self.toggle.setText(fold_mark(self.toggle, self.toggle.isChecked()) + '思考過程')
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

    def set_live(self, live: bool) -> None:
        """Open while the Agent is thinking, closed once the answer is done (unless the reader chose)."""
        if not self.touched and self.toggle.isChecked() != live:
            self.toggle.setChecked(live)


class ToolRow(QWidget):
    def __init__(self, part: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        # Several tools in a row fold into one line ('3 個工具') on the first of them: see set_fold.
        self.summary = QPushButton()
        self.summary.setObjectName('ToolRow')
        self.summary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.summary.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.summary.hide()
        self.summary.clicked.connect(lambda: self.on_summary and self.on_summary())
        layout.addWidget(self.summary, 0, Qt.AlignmentFlag.AlignLeft)
        self.on_summary = None
        self.heads = False    # shows the summary line of its run
        self.folded = False   # its own line is folded into the summary
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
        head = tool_name(part.get('name') or 'tool')
        text = f'{head}  {summary}' if summary else head
        if len(text) > 90:
            text = text[:89] + '…'
        self.arrow = fold_mark(self.toggle, self.toggle.isChecked())
        self.toggle.setText(f'{self.arrow}{text}{mark}')
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
        self._details()

    def _details(self) -> None:
        opened = self.toggle.isChecked() and not self.folded
        self.detail.setVisible(opened and bool(self.detail.text()))
        self.diff.setVisible(opened and bool(self.diff_text))

    def _toggled(self, _shown: bool) -> None:
        self.update_part(self.part)

    def set_fold(self, run: list | None, folded: bool) -> None:
        """run: the tools this row heads (two or more) or None; folded: its own line stays inside the summary."""
        self.heads = bool(run)
        if run:
            states = {r.part.get('status', '') for r in run}
            mark = ' ✕' if 'error' in states else ' …' if states & {'running', 'pending'} else ''
            self.summary.setText(f'{fold_mark(self.summary, not folded)}{len(run)} 個工具{mark}')
        self.summary.setVisible(self.heads)
        self.folded = folded
        self.toggle.setVisible(not folded)
        self._details()


class CodeBlock(QFrame):
    """A fenced code block: monospace, unwrapped (scrolls sideways), with its own copy button."""

    def __init__(self, language: str, code: str):
        super().__init__()
        self.setObjectName('CodeBlock')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(*((16, 8, 10, 2) if theme.decorated('code') else (10, 2, 4, 0)))
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

    def paintEvent(self, event):  # noqa: N802
        if theme.decorated('code'):
            paint_decor(self, 'code', event)
            return
        super().paintEvent(event)

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
        self.layout_.setContentsMargins(0, 4, 0, 0)   # a little apart from tool lines above it
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
    edit_submitted = Signal(str, str)   # message id, the new text
    retry_requested = Signal()
    fork_requested = Signal(str)   # message id

    def __init__(self, item: dict, resolve=None):
        super().__init__()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.parts: dict[str, QWidget] = {}
        self.item = item
        self.actions: QWidget | None = None
        self.retry: QPushButton | None = None
        # Steps of one answer (thinking, tools, text) are drawn as one card: see ConversationView._regroup.
        self.joined = (False, False)
        self.folded = False   # only tools folded into a summary shown in an earlier step
        self.copy_text = None
        self.process: QPushButton | None = None   # '處理了 N 個步驟' on the first step of a finished answer
        self.on_process = None
        self.rule: QFrame | None = None           # between the folded steps and the answer
        self.editor: QWidget | None = None        # the user's message being edited in place
        self.keep_place = lambda change: change()  # the view keeps this message where it is on screen
        if item['kind'] != 'assistant':
            self.layout_.setContentsMargins(0, GAP, 0, 0)
        if item['kind'] == 'user':
            row = QHBoxLayout()
            row.addStretch(1)
            bubble = self.bubble = Bubble()
            self.bubble_row = row
            bubble.setObjectName('UserBubble')
            bubble.setMaximumWidth(620)
            bubble.setMinimumHeight(36)   # a one-line bubble stays round (radius at most half the height)
            inner = QVBoxLayout(bubble)
            inner.setContentsMargins(*((16, 10, 16, 11) if theme.decorated('bubble') else (14, 10, 14, 10)))
            # Not the shared label(): its ignored width policy would collapse a bubble to one column.
            self.user_text = QLabel(item['text'])
            self.user_text.setWordWrap(True)
            self.user_text.setTextFormat(Qt.TextFormat.PlainText)
            self.user_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.user_text.setMaximumWidth(590)
            # A wrapped label guesses a narrow width; start from the longest line instead, measured in the
            # size the style sheet will give it (not applied yet: the bubble has no window).
            from PySide6.QtGui import QFont, QFontMetrics
            font = QFont(QApplication.font())
            font.setPixelSize(theme.px(13))
            metrics = QFontMetrics(font)
            longest = max((metrics.horizontalAdvance(line) for line in item['text'].splitlines()), default=0)
            self.user_text.setMinimumWidth(min(longest + 8, 560))
            inner.addWidget(self.user_text)
            self.user_text.setVisible(bool(item['text']))
            if item.get('files'):
                for name in item['files']:
                    path = resolve(name) if resolve else ''
                    pixmap = QPixmap(path) if path else QPixmap()
                    from agentdock.ui.qa_view import ClickableImage
                    attachment = ClickableImage() if not pixmap.isNull() else QLabel()
                    if not pixmap.isNull():
                        attachment.setPixmap(pixmap.scaled(
                            360, 240, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))
                        attachment.setAccessibleName(name)
                        attachment.setCursor(Qt.CursorShape.PointingHandCursor)
                        from agentdock.ui.external_tools import open_target
                        attachment.clicked.connect(lambda p=path: open_target('image', p))
                    else:
                        attachment.setText(f'附件：{name}')
                        attachment.setObjectName('FileNote')
                        attachment.setWordWrap(True)
                    inner.addWidget(attachment)
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
            self._margins()
            self.update_item(item)

    def _margins(self) -> None:
        top, bottom = self.joined
        gap = 0 if top else GAP
        if theme.decorated('reply'):   # room for the card and its avatar
            self.layout_.setContentsMargins(54, gap + (4 if top else 14), 18, 4 if bottom else 12)
        else:
            self.layout_.setContentsMargins(0, gap if not top else 6, 0, 0)

    def set_join(self, top: bool, bottom: bool) -> None:
        """Part of one answer with the step above / below: no gap, no avatar, no copy row in between."""
        if (top, bottom) == self.joined:
            return
        self.joined = (top, bottom)
        self._margins()
        if self.actions is not None:
            policy = self.actions.sizePolicy()
            policy.setRetainSizeWhenHidden(not bottom)
            self.actions.setSizePolicy(policy)
            if bottom:
                self.actions.hide()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self.item.get('kind') == 'assistant' and theme.decorated('reply'):
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QPainter
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            top, bottom = self.joined
            rect = QRectF(self.rect()).adjusted(0, 0 if top else GAP, 0, 0)
            theme.decorate('reply', painter, rect, top, bottom)
            painter.end()

    def _add_actions(self, right: bool, edit: bool) -> None:
        """Copy (and edit) under a message, shown while the pointer is over it; the space stays reserved."""
        self.actions = QWidget()
        row = QHBoxLayout(self.actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        if right:
            row.addStretch(1)
        copy = mini_button('複製')
        copy.clicked.connect(lambda: copy_to_clipboard(self.copy_text() if self.copy_text else self.plain_text(), copy))
        row.addWidget(copy)
        if edit:
            change = mini_button('編輯')
            # No focus: hiding a focused button would hand focus on and scroll the list to it.
            change.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            change.clicked.connect(self.start_edit)
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

    def start_edit(self) -> None:
        """The bubble turns into a box holding the message; sending replaces it and everything after it."""
        if self.editor is not None:
            self.editor_box.setFocus()
            return
        self.keep_place(self._open_editor)

    def _open_editor(self) -> None:
        self.editor = Bubble()
        self.editor.setObjectName('UserBubble')
        inner = QVBoxLayout(self.editor)
        inner.setContentsMargins(*((16, 10, 16, 10) if theme.decorated('bubble') else (14, 10, 14, 10)))
        inner.setSpacing(6)
        self.editor_box = text_box(12, enter_sends=True)
        self.editor_box.setObjectName('EditBox')
        self.editor_box.setPlainText(self.item.get('text', ''))
        self.editor_box.submitted.connect(self._send_edit)
        self.editor_box.key_hook = lambda e: e.key() == Qt.Key.Key_Escape and (self.cancel_edit() or True)
        inner.addWidget(self.editor_box)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = mini_button('取消')
        cancel.clicked.connect(self.cancel_edit)
        send = mini_button('送出')
        send.clicked.connect(self._send_edit)
        buttons.addWidget(cancel)
        buttons.addWidget(send)
        inner.addLayout(buttons)
        self.bubble.hide()
        if self.actions is not None:
            self.actions.hide()
        self.bubble_row.insertWidget(self.bubble_row.indexOf(self.bubble), self.editor, 6)
        self.editor_box.setFocus()
        cursor = self.editor_box.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor_box.setTextCursor(cursor)

    def cancel_edit(self) -> None:
        if self.editor is not None:
            self.keep_place(self._close_editor)

    def _close_editor(self) -> None:
        self.bubble_row.removeWidget(self.editor)
        self.editor.hide()
        self.editor.deleteLater()
        self.editor = None
        self.bubble.show()

    def _send_edit(self) -> None:
        text = self.editor_box.toPlainText().strip() if self.editor is not None else ''
        if text:
            self.cancel_edit()
            self.edit_submitted.emit(self.item['id'], text)

    def plain_text(self) -> str:
        if self.item['kind'] == 'user':
            return self.item.get('text', '')
        return '\n\n'.join(p['text'] for p in self.item.get('parts', []) if p['type'] == 'text' and p['text'])

    def enterEvent(self, event):  # noqa: N802
        if self.actions is not None and not self.joined[1] and self.editor is None:
            self.actions.show()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        if self.actions is not None:
            self.actions.hide()
        super().leaveEvent(event)

    def set_process(self, steps: int, opened: bool) -> None:
        """The line the steps of a finished answer fold into (0 steps: none)."""
        if not steps:
            if self.process is not None:
                self.process.hide()
            return
        if self.process is None:
            self.process = QPushButton()
            self.process.setObjectName('ToolRow')
            self.process.setCursor(Qt.CursorShape.PointingHandCursor)
            self.process.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            self.process.clicked.connect(lambda: self.on_process and self.on_process())
            self.layout_.insertWidget(0, self.process, 0, Qt.AlignmentFlag.AlignLeft)
        self.process.setText(f'{fold_mark(self.process, opened)}處理了 {steps} 個步驟')
        self.process.show()

    def set_rule(self, before: str | None) -> None:
        """A thin rule above the part `before` (the start of the answer), or none."""
        if before is None or before not in self.parts:
            if self.rule is not None:
                self.rule.hide()
            return
        if self.rule is None:
            self.rule = QFrame()
            self.rule.setObjectName('ProcessRule')
            self.rule.setFixedHeight(1)
        else:
            self.layout_.removeWidget(self.rule)
        self.layout_.insertWidget(self.layout_.indexOf(self.parts[before]), self.rule)
        self.rule.show()

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
                widget.setVisible(bool(str(part.get('text') or '').strip()))   # Codex often keeps it hidden
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
        # A step with nothing to show yet (an empty thought, a reply not started) gets no empty card.
        if not self.has_content():
            self.hide()
        elif self.isHidden() and not self.folded and self.parentWidget() is not None:
            self.show()

    def has_content(self) -> bool:
        if self.item.get('error'):
            return True
        for part in self.item.get('parts', []):
            if part['type'] in ('text', 'reasoning'):
                if str(part.get('text') or '').strip():
                    return True
            else:
                return True
        return False


# --------------------------------------------------------------------------- new conversation
class Bubble(QFrame):
    """The user's own message; the local look may paint it (draw_bubble)."""

    def paintEvent(self, event):  # noqa: N802
        if theme.decorated('bubble'):
            paint_decor(self, 'bubble', event)
            return
        super().paintEvent(event)
