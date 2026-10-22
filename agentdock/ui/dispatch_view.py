"""Sending: the new-task composer and the controls on a sent task's card.

Every approval, answer and stop is a click here; nothing is decided automatically.
"""
from __future__ import annotations

import os
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QPlainTextEdit, QSizePolicy,
                               QVBoxLayout, QWidget)

from agentdock.ui.qa_view import AutoGrowText, FullRowCheckBox, FullRowRadioButton, button, label
from agentdock.ui.question_text import QuestionText
from agentdock.ui.widgets import ElidedLabel, icon_button

STATUS = {'running': '進行中', 'unknown': '狀態未知', 'done': '已完成', 'failed': '失敗', 'interrupted': '已停止'}


class ChatInput(AutoGrowText):
    """Pasted files become their paths (the Agent reads them); Enter can send, Shift+Enter breaks a line."""
    submitted = Signal()

    def __init__(self, max_lines: int, enter_sends: bool = False):
        super().__init__(max_lines=max_lines)
        self.enter_sends = enter_sends
        # A fixed-height box must not make its card soak up the list's spare space.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if source.hasUrls() and all(u.isLocalFile() for u in source.urls()):
            self.insertPlainText(' '.join(f'"{os.path.normpath(u.toLocalFile())}"' for u in source.urls()))
            return
        if source.hasImage():
            return  # attachments are not supported yet
        QPlainTextEdit.insertFromMimeData(self, source)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        if self.enter_sends and enter and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


def text_box(max_lines: int, enter_sends: bool = False) -> ChatInput:
    return ChatInput(max_lines, enter_sends)


def submit_shortcut(widget: QWidget, action) -> None:
    for keys in ('Ctrl+Return', 'Ctrl+Enter'):
        shortcut = QShortcut(QKeySequence(keys), widget, activated=action)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)


def status_text(row: dict) -> str:
    if row.get('permissions'):
        text = '待授權'
    elif row.get('forms'):
        text = '待回答'
    else:
        text = STATUS.get(row['state'], row['state'])
        if row['state'] == 'running' and row.get('doing'):
            text += f" · {row['doing']}"
        if row['state'] == 'failed' and row.get('error'):
            text += f"：{row['error']}"
    if row.get('offline') and row['state'] in ('running', 'unknown'):
        text += f"\n{row['offline']}"
    return text


class Composer(QWidget):
    """New task: which Agent, which folder, what to do."""
    sent = Signal()

    def __init__(self, inbox):
        super().__init__()
        self.inbox = weakref.proxy(inbox)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 8)
        top = QHBoxLayout()
        self.target = QComboBox()
        top.addWidget(self.target)
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.lineEdit().setPlaceholderText('資料夾')
        self.folder.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        top.addWidget(self.folder, 1)
        browse = icon_button('…', '選擇資料夾')
        browse.clicked.connect(self.browse)
        top.addWidget(browse)
        layout.addLayout(top)
        self.text = text_box(10)
        layout.addWidget(self.text)
        bottom = QHBoxLayout()
        self.error = label('', muted=True)
        self.error.hide()
        bottom.addWidget(self.error, 1)
        self.send_button = button('送出', primary=True)
        self.send_button.setToolTip('Ctrl+Enter')
        self.send_button.clicked.connect(self.send)
        bottom.addWidget(self.send_button)
        layout.addLayout(bottom)
        submit_shortcut(self, self.send)

    def prepare(self) -> None:
        current = self.target.currentData()
        self.target.clear()
        for target in self.inbox.targets():
            self.target.addItem(target['label'], target)
        index = max(0, next((i for i in range(self.target.count())
                             if (self.target.itemData(i) or {}).get('owner') == (current or {}).get('owner')), 0))
        self.target.setCurrentIndex(index)
        self.target.setVisible(self.target.count() > 1)
        typed = self.folder.currentText()
        self.folder.clear()
        self.folder.addItems(self.inbox.store.dispatch.recent)
        self.folder.setCurrentText(typed or (self.inbox.store.dispatch.recent or [''])[0])
        self.error.hide()
        self.text.setFocus()

    def browse(self) -> bool:
        start = self.folder.currentText() if os.path.isdir(self.folder.currentText()) else os.path.expanduser('~')
        chosen = QFileDialog.getExistingDirectory(self, '選擇資料夾', start)
        if chosen:
            self.folder.setCurrentText(os.path.normpath(chosen))
        return bool(chosen)

    def send(self) -> None:
        target = self.target.currentData()
        text = self.text.toPlainText().strip()
        if text and not self.folder.currentText().strip() and not self.browse():
            return  # no folder yet: pick one instead of failing
        folder = self.folder.currentText().strip().strip('"')
        problem = ('沒有可用的 Agent。' if not target else '找不到這個資料夾。' if not os.path.isdir(folder)
                   else '' if text else '內容是空的。')
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        self.inbox.dispatcher.start_task(os.path.normpath(folder), text, target['owner'])
        self.text.clear()
        self.error.hide()
        self.sent.emit()


class FormBox(QWidget):
    """A question form from the client (OpenCode's question tool)."""

    def __init__(self, panel, form: dict):
        super().__init__()
        self.panel, self.form = panel, form
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        self.inputs = []
        for field in form.get('fields', []):
            title = field.get('title') or ''
            question = field.get('description') or ''
            if title and title != question:
                layout.addWidget(label(title, name='FieldLabel'))
            if question:
                layout.addWidget(QuestionText(question))
            options = field.get('options') or []
            multiple = field.get('type') == 'array'
            group = QButtonGroup(self)
            group.setExclusive(not multiple)
            choices = []
            for option in options:
                text = option.get('label') or str(option.get('value', ''))
                if option.get('description'):
                    text += f" — {option['description']}"
                choice = FullRowCheckBox(text) if multiple else FullRowRadioButton(text)
                choice.setProperty('value', option.get('value', option.get('label')))
                group.addButton(choice)
                layout.addWidget(choice)
                choices.append(choice)
            custom = None
            if field.get('custom', not options) or not options:
                custom = text_box(6)
                layout.addWidget(custom)
            self.inputs.append((field, choices, custom, multiple))
        row = QHBoxLayout()
        row.addStretch()
        skip = button('略過', flat=True)
        skip.clicked.connect(lambda: self.panel.answer_form(self.form['id'], None))
        row.addWidget(skip)
        submit = button('回答', primary=True)
        submit.setToolTip('Ctrl+Enter')
        submit.clicked.connect(self.submit)
        row.addWidget(submit)
        layout.addLayout(row)
        submit_shortcut(self, self.submit)

    def answer(self) -> dict | None:
        answer = {}
        for field, choices, custom, multiple in self.inputs:
            picked = [c.property('value') for c in choices if c.isChecked()]
            typed = custom.toPlainText().strip() if custom else ''
            if multiple:
                value = picked + ([typed] if typed else [])
                if not value:
                    return None
            else:
                value = typed or (picked[0] if picked else '')
                if value == '':
                    return None
            answer[field['key']] = value
        return answer

    def submit(self) -> None:
        answer = self.answer()
        if answer is not None:
            self.panel.answer_form(self.form['id'], answer)


class DispatchPanel(QWidget):
    """The sent session on a task card: last exchange, approvals, questions and the reply box."""

    def __init__(self, inbox, key: str):
        super().__init__()
        self.inbox = inbox if isinstance(inbox, weakref.ProxyTypes) else weakref.proxy(inbox)
        self.key = key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        self.prompt = ElidedLabel('')
        self.prompt.setProperty('muted', True)
        self.prompt.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.prompt)
        self.reply = QuestionText('')
        layout.addWidget(self.reply)
        self.requests = QVBoxLayout()
        layout.addLayout(self.requests)
        self.request_ids: tuple = ()
        self.forms: dict[str, FormBox] = {}
        self.permission_rows: list[QWidget] = []
        box = QHBoxLayout()
        self.text = text_box(8)
        box.addWidget(self.text, 1)
        self.stop = button('停止', danger=True, flat=True)
        self.stop.clicked.connect(lambda: self.inbox.dispatcher.interrupt(self.key))
        box.addWidget(self.stop, 0, Qt.AlignmentFlag.AlignBottom)
        send = button('送出', primary=True)
        send.setToolTip('Ctrl+Enter')
        send.clicked.connect(self.send)
        box.addWidget(send, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(box)
        submit_shortcut(self.text, self.send)
        self.row: dict = {}

    def update_row(self, row: dict) -> None:
        self.row = row
        self.prompt.setText('› ' + ' '.join(row.get('prompt', '').split()))
        self.prompt.setVisible(bool(row.get('prompt')))
        if self.reply.source != row.get('reply', ''):
            self.reply.source = row.get('reply', '')
            self.reply.refresh_theme()
        self.reply.setVisible(bool(row.get('reply')))
        self.stop.setVisible(row['state'] == 'running')
        ids = (tuple(row.get('permissions', {})), tuple(row.get('forms', {})))
        if ids != self.request_ids:
            self._rebuild_requests(row, ids)

    def _rebuild_requests(self, row: dict, ids: tuple) -> None:
        self.request_ids = ids
        for widget in self.permission_rows:
            self.requests.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self.permission_rows = []
        for pid, permission in row.get('permissions', {}).items():
            widget = QWidget()
            lay = QVBoxLayout(widget)
            lay.setContentsMargins(0, 6, 0, 6)
            resources = '\n'.join(permission.get('resources', []))
            lay.addWidget(label(permission.get('action', ''), name='FieldLabel'))
            if resources:
                lay.addWidget(label(resources))
            buttons = QHBoxLayout()
            buttons.addStretch()
            for text, decision, kind in (('拒絕', 'reject', 'danger'), ('一律允許', 'always', 'flat'),
                                         ('允許', 'once', 'primary')):
                b = button(text, **{kind: True})
                b.clicked.connect(lambda _c=False, p=pid, d=decision: self.decide(p, d))
                buttons.addWidget(b)
            lay.addLayout(buttons)
            self.requests.addWidget(widget)
            self.permission_rows.append(widget)
        for fid in set(self.forms) - set(row.get('forms', {})):
            box = self.forms.pop(fid)
            self.requests.removeWidget(box)
            box.hide()
            box.deleteLater()
        for fid, form in row.get('forms', {}).items():
            if fid not in self.forms:
                self.forms[fid] = FormBox(self, form)
                self.requests.addWidget(self.forms[fid])

    def decide(self, request_id: str, decision: str) -> None:
        self.inbox.dispatcher.reply_permission(self.key, request_id, decision)

    def answer_form(self, form_id: str, answer: dict | None) -> None:
        self.inbox.dispatcher.reply_form(self.key, form_id, answer)

    def send(self) -> None:
        text = self.text.toPlainText().strip()
        if not text:
            return
        self.inbox.dispatcher.send(self.key, text)
        self.text.clear()

    def busy(self) -> bool:
        """Typing or answering here must survive refreshes and block removal."""
        return bool(self.text.toPlainText().strip() or self.forms or self.permission_rows)
