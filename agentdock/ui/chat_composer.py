"""The message box: text, attachments, / and @ suggestions, model / effort / mode chips."""
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
from agentdock.ui.chat_common import COLUMN, ChipButton, ROLE, RoundButton, icon, paint_decor


class SettingsBar(QObject):
    """Model, reasoning effort and permission mode for one conversation; '' leaves the client's default.

    Chips as in desktop chat apps: `model_chip`, then `effort_chip` (what the chosen model offers), and
    `mode_chip`; the
    composer places them."""
    changed = Signal(str, str)   # field, value
    DEFAULTS = {'model': '預設模型', 'effort': '預設推理', 'mode': '預設權限'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_chip = ChipButton()
        self.effort_chip = ChipButton('gauge')   # the gauge says what a bare 中 / 高 is about
        self.mode_chip = ChipButton('shield')
        for chip in (self.model_chip, self.effort_chip, self.mode_chip):
            chip.hide()
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

    def _default(self, field: str) -> str:
        """What the Agent really uses for `field` when nothing is chosen, if it said (one of the listed ids)."""
        if field == 'mode':
            value = str(self.options.get('mode_default') or '')
        else:
            value = (self.options.get('defaults') or {}).get(field) or ''
            if isinstance(value, dict):   # efforts per model
                value = value.get(self._shown('model')) or ''
            value = str(value)
        return value if value in [i for i, _t in self.choices[field]] else ''

    def _shown(self, field: str) -> str:
        return self.current[field] or self._default(field)

    def _label(self, field: str) -> str:
        value = self._shown(field)
        return next((t for i, t in self.choices[field] if i == value), '') if value else ''

    def _default_text(self, field: str) -> str:
        """Only when the Agent does not say what its default is. OpenCode without a variant names that
        state itself (effort_unset: the model reasons its own way)."""
        if field == 'effort' and self.options.get('effort_unset'):
            return str(self.options['effort_unset'])
        return self.DEFAULTS[field]

    def _render(self) -> None:
        self.model_chip.setText(self._label('model') or self._default_text('model'))
        self.model_chip.setVisible(bool(self.choices['model']))
        self.effort_chip.setText(self._label('effort') or self._default_text('effort'))
        self.effort_chip.setVisible(bool(self.choices['effort']))
        self.mode_chip.setText(self._label('mode') or self._default_text('mode'))
        self.mode_chip.setVisible(bool(self.choices['mode']))
        self._menus()

    def _menus(self) -> None:
        from PySide6.QtGui import QActionGroup
        for chip, field in ((self.model_chip, 'model'), (self.effort_chip, 'effort'), (self.mode_chip, 'mode')):
            menu = chip.menu_
            menu.clear()
            if not self.choices[field]:
                continue
            group = QActionGroup(menu)
            # A known default is just one of the choices; only an unknown one gets its own entry.
            entries = self.choices[field] if self._default(field) else [('', self._default_text(field))] + self.choices[field]
            for item_id, text in entries:
                action = menu.addAction(text, lambda f=field, v=item_id: self.pick(f, v))
                action.setCheckable(True)
                action.setChecked(item_id == self._shown(field))
                group.addAction(action)

    def pick(self, field: str, value: str) -> None:
        """The user chose `value` for `field` (from a chip's menu)."""
        if self._shown(field) == value:
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


IMAGE_TYPES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')


def image_thumb(path: str, height: int):
    """A small picture of an image file (at most `height` px tall, sharp on high-DPI screens), else None."""
    if not path.lower().endswith(IMAGE_TYPES) or not os.path.isfile(path):
        return None
    from PySide6.QtGui import QGuiApplication, QPixmap
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    pixmap = pixmap.scaledToHeight(round(height * ratio), Qt.TransformationMode.SmoothTransformation)
    if pixmap.width() > height * 3 * ratio:
        pixmap = pixmap.scaledToWidth(round(height * 3 * ratio), Qt.TransformationMode.SmoothTransformation)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class Composer(QFrame):
    """The message box: text, attachments, the conversation's settings, send and stop."""
    submitted = Signal()

    def __init__(self, parent=None, enter_sends: bool = True, max_lines: int = 10, min_lines: int = 1):
        super().__init__(parent)
        self.setObjectName('Composer')
        self.setMaximumWidth(COLUMN)
        self.setAcceptDrops(True)
        self.drafts: dict[str, tuple[str, list[str]]] = {}
        self.draft_key = ''
        self.files: list[str] = []
        self.attach_dir: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 8, 8)
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
        self.bar.addWidget(self.settings.effort_chip)
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

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        paint_decor(self, 'box', event, 26)

    def restyle(self) -> None:
        self.layout().setContentsMargins(*((24, 22, 22, 20) if theme.decorated('box') else (14, 10, 8, 8)))
        self.attach.setIcon(icon('plus', theme.MUTED or '#888888', 18))
        for chip in (self.settings.model_chip, self.settings.effort_chip, self.settings.mode_chip, self.send, self.stop):
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
            thumb = image_thumb(path, 56)
            if thumb is not None:   # a picture shows itself, not its file name
                chip = QPushButton('✕')
                chip.setIcon(QIcon(thumb))
                chip.setIconSize(thumb.size() / thumb.devicePixelRatio())
                chip.setObjectName('ImageChip')
            else:
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
