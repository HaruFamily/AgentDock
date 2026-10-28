"""Read-only project changes: branch, staged/worktree files and diffs."""
from __future__ import annotations

import html
import os
import subprocess
import threading
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QSplitter, QStyle, QStyledItemDelegate, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from agentdock import gitops
from agentdock.ui import theme, web_transcript
from agentdock.ui.chat_messages import diff_html
from agentdock.ui.qa_view import button, label
from agentdock.ui.widgets import icon_button

ROLE = Qt.ItemDataRole.UserRole
HEAD = Qt.ItemDataRole.UserRole + 1   # on the two group rows: True for 已暫存, False for 變更
LETTERS = {'M': '修改', 'A': '新增', 'D': '刪除', 'R': '改名', 'C': '複製', 'U': '衝突', '?': '新檔', 'T': '類型'}
MAX_LINES = 3000


def letter_colour(letter: str) -> QColor:
    name = {'M': 'WARN_INK', 'T': 'WARN_INK', 'A': 'OK', '?': 'OK', 'D': 'DANGER', 'U': 'DANGER'}.get(letter, 'MUTED')
    return QColor(getattr(theme, name) or '#888888')


class _Relay(QObject):
    done = Signal(object, object, object)   # callback, result, error


# --------------------------------------------------------------------------- the file list
class ChangeDelegate(QStyledItemDelegate):
    """Read-only file rows with status, path and added/removed line counts."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(100, theme.px(28))

    def paint(self, painter, option, index):
        item = self.dialog.changes.itemFromIndex(index)
        if item is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 1, -4, -1)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        head = item.data(0, HEAD)
        if (selected and head is None) or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT_SOFT if selected and head is None else theme.HOVER))
            painter.drawRoundedRect(rect, 7, 7)
        font = QFont(option.font)
        muted, ink = QColor(theme.MUTED), QColor(theme.INK)
        right = rect.right() - 8
        if head is not None:
            font.setBold(True)
            font.setPixelSize(theme.px(12))
            painter.setFont(font)
            painter.setPen(muted)
            painter.drawText(rect.adjusted(8, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                             f"{'已暫存' if head else '變更'}　{item.childCount()}")
            painter.restore()
            return
        change, staged = item.data(0, ROLE)
        letter = change.index if staged else change.work
        stats = self.dialog.stats.get((change.path, staged))
        x = rect.left() + 8
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(letter_colour(letter))
        mark = letter
        painter.drawText(QRect(x, rect.top(), 16, rect.height()), Qt.AlignmentFlag.AlignVCenter, mark)
        x += 22
        if stats:
            small = QFont(option.font)
            small.setPixelSize(theme.px(11))
            painter.setFont(small)
            added, removed = f'+{stats[0]}', f'-{stats[1]}'
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(added) + metrics.horizontalAdvance(removed) + 6
            painter.setPen(QColor(theme.OK))
            painter.drawText(QRect(right - width, rect.top(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter, added)
            painter.setPen(QColor(theme.DANGER))
            painter.drawText(QRect(right - metrics.horizontalAdvance(removed), rect.top(), width, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter, removed)
            right -= width + 10
        folder, name = os.path.split(change.path)
        if change.old and staged:
            name = f'{os.path.basename(change.old)} → {name}'
        painter.setFont(font)
        metrics = painter.fontMetrics()
        name = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, max(20, right - x))
        painter.setPen(ink)
        painter.drawText(QRect(x, rect.top(), right - x, rect.height()), Qt.AlignmentFlag.AlignVCenter, name)
        x += metrics.horizontalAdvance(name) + 8
        if folder and x < right - 20:
            font.setBold(False)
            font.setPixelSize(theme.px(11))
            painter.setFont(font)
            painter.setPen(muted)
            text = painter.fontMetrics().elidedText(folder, Qt.TextElideMode.ElideLeft, right - x)
            painter.drawText(QRect(x, rect.top(), right - x, rect.height()), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

# --------------------------------------------------------------------------- the diff page
def diff_rows(diff: str, path: str) -> tuple[str, int, int]:
    """Table rows in GitHub's look (old / new line numbers, sign, code) and the lines added / removed."""
    rows, code, kinds = [], [], []
    old = new = 0
    added = removed = 0
    for line in diff.splitlines()[:MAX_LINES]:
        if line.startswith(('diff --git', 'index ', '--- ', '+++ ', 'new file', 'deleted file', 'similarity',
                            'rename ', 'old mode', 'new mode')):
            continue
        if line.startswith('@@'):
            try:
                spans = line.split('@@')[1].split()
                old = int(spans[0][1:].split(',')[0])
                new = int(spans[1][1:].split(',')[0])
            except (IndexError, ValueError):
                pass
            kinds.append(('hunk', line, '', ''))
            continue
        if line.startswith('\\'):
            kinds.append(('meta', line, '', ''))
            continue
        sign, text = (line[:1], line[1:]) if line[:1] in '+- ' and line else (' ', line)
        if sign == '+':
            new = new or 1
            kinds.append(('add', text, '', str(new)))
            new += 1
            added += 1
        elif sign == '-':
            kinds.append(('del', text, str(old), ''))
            old += 1
            removed += 1
        else:
            kinds.append(('ctx', text, str(old), str(new)))
            old += 1
            new += 1
        code.append(text)
    coloured = _colour(code, path)
    at = 0
    for kind, text, a, b in kinds:
        if kind in ('hunk', 'meta'):
            rows.append(f'<tr class="{kind}"><td class="ln"></td><td class="ln"></td>'
                        f'<td colspan="2">{html.escape(text)}</td></tr>')
            continue
        body = coloured[at] if at < len(coloured) else html.escape(text)
        at += 1
        sign = {'add': '+', 'del': '−'}.get(kind, '')
        rows.append(f'<tr class="{kind}"><td class="ln">{a}</td><td class="ln">{b}</td>'
                    f'<td class="sign">{sign}</td><td class="code">{body or " "}</td></tr>')
    return ''.join(rows), added, removed


def _colour(lines: list[str], path: str) -> list[str]:
    """Syntax colours for the diff's code lines (one pass, so a multi-line string stays coloured)."""
    if not lines or len(lines) > 2000:
        return [html.escape(line) for line in lines]
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_for_filename
        out = highlight('\n'.join(lines) + '\n', get_lexer_for_filename(path, stripnl=False, ensurenl=True),
                        HtmlFormatter(nowrap=True)).split('\n')
        return out[:len(lines)] if len(out) >= len(lines) else [html.escape(line) for line in lines]
    except Exception:
        return [html.escape(line) for line in lines]


def diff_page(diff: str, path: str, note: str = '') -> str:
    t = theme
    dark = t.is_dark()
    rows, added, removed = diff_rows(diff, path) if diff else ('', 0, 0)
    try:
        from pygments.formatters import HtmlFormatter
        hl = HtmlFormatter(style='monokai' if dark else 'friendly').get_style_defs('.code')
    except Exception:
        hl = ''
    green, red, blue = ('46,160,67', '248,81,73', '56,139,253')
    folder, name = os.path.split(path)
    body = (f'<table class="diff">{rows}</table>' if rows else
            f'<div class="empty">{html.escape(note or "沒有文字差異")}</div>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
:root {{ color-scheme: {'dark' if dark else 'light'}; }}
html, body {{ margin: 0; background: {t.CARD}; color: {t.INK}; }}
body {{ font: 12.5px/1.6 "Cascadia Mono", Consolas, monospace; }}
.head {{ position: sticky; top: 0; z-index: 1; background: {t.CARD}; border-bottom: 1px solid {t.LINE};
         padding: 8px 14px; font: 600 13px "Segoe UI", "Microsoft JhengHei UI", sans-serif; }}
.head .dir {{ color: {t.MUTED}; font-weight: 400; margin-left: 6px; }}
.head .add {{ color: {t.OK}; margin-left: 10px; }} .head .del {{ color: {t.DANGER}; margin-left: 6px; }}
table.diff {{ border-collapse: collapse; min-width: 100%; }}
td {{ padding: 0; vertical-align: top; }}
td.ln {{ min-width: 40px; padding: 0 8px; text-align: right; color: {t.DIM}; user-select: none; }}
td.sign {{ width: 14px; padding-left: 6px; color: {t.MUTED}; user-select: none; }}
td.code {{ white-space: pre; padding-right: 16px; }}
tr.add td {{ background: rgba({green},.12); }} tr.add td.ln {{ background: rgba({green},.22); }}
tr.del td {{ background: rgba({red},.12); }} tr.del td.ln {{ background: rgba({red},.22); }}
tr.hunk td {{ background: rgba({blue},.10); color: {t.MUTED}; padding-top: 3px; padding-bottom: 3px; }}
tr.hunk td[colspan] {{ padding-left: 8px; }}
tr.meta td {{ color: {t.MUTED}; }}
.empty {{ padding: 24px; color: {t.MUTED}; font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif; }}
{hl}
.code {{ background: transparent; }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{ background: {t.LINE}; border-radius: 5px; border: 2px solid {t.CARD}; }}
</style></head><body>
<div class="head">{html.escape(name)}<span class="dir">{html.escape(folder)}</span>
{f'<span class="add">+{added}</span><span class="del">−{removed}</span>' if rows else ''}</div>
{body}</body></html>"""


# --------------------------------------------------------------------------- the window
class GitView(QWidget):
    """Read-only changes shared by the project page and standalone viewer."""

    def __init__(self, root: str = '', external=None, embedded: bool = False):
        super().__init__()
        self.external = external
        self.embedded = embedded
        self._generation = 0
        self.directory = root
        self.restyle()
        self.root = root
        self.status: gitops.Status | None = None
        self.stats: dict[tuple[str, bool], tuple[int, int]] = {}
        self.busy = 0
        self.relay = _Relay(self)
        self.relay.done.connect(lambda callback, result, error: callback(result, error))
        self.setWindowTitle(f'專案變更 — {root}')
        self._diff_serial = 0
        self.resize(1080, 700)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        self.branch = label('', wrap=False)
        self.branch.setAccessibleName('目前分支')
        heading = QVBoxLayout()
        heading.setSpacing(3)
        heading.addWidget(label('Git 變更', name='GroupTitle'))
        meta = QHBoxLayout()
        meta.addWidget(self.branch)
        self.tracking = label('', muted=True)
        meta.addWidget(self.tracking, 1)
        heading.addLayout(meta)
        top.addLayout(heading, 1)
        self.external_button = button('開啟 Git 工具')
        self.external_button.clicked.connect(self._external)
        top.addWidget(self.external_button)
        refresh = button('重新整理', flat=True)
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        layout.addLayout(top)

        split = self.split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        self.changes = QTreeWidget()
        self.changes.setObjectName('GitChanges')
        self.changes.setHeaderHidden(True)
        self.changes.setRootIsDecorated(False)
        self.changes.setIndentation(0)
        self.changes.setMouseTracking(True)
        self.changes.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.changes.setItemDelegate(ChangeDelegate(self))
        palette = self.changes.palette()   # the delegate paints the selection; the style's stays off
        for role in ('Highlight', 'Accent'):
            if hasattr(QPalette.ColorRole, role):
                palette.setColor(getattr(QPalette.ColorRole, role), QColor(0, 0, 0, 0))
        self.changes.setPalette(palette)
        self.changes.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.changes.currentItemChanged.connect(lambda item, _old: self._show_diff(item))
        column.addWidget(self.changes, 1)
        split.addWidget(left)
        # The diff: a web page in GitHub's look; coloured text when the web view is off (and for find in tests).
        self.diff = QTextBrowser()
        self.diff.setOpenLinks(False)
        self.diff.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        self.web = None
        if web_transcript.ENABLED:
            self.web = web_transcript.make_view(self)
            split.addWidget(self.web)
            self.diff.setParent(self)   # kept up to date, never shown
            self.diff.hide()
            self.web.setHtml(diff_page('', '', ' '), self.web.page_url)
        else:
            split.addWidget(self.diff)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        split.setSizes([240, 460])
        self.empty = label('載入中…', muted=True)
        self.empty.setContentsMargins(0, 12, 0, 12)
        layout.addWidget(self.empty)
        layout.addWidget(split, 1)
        self.error = QLabel()
        self.error.setObjectName('ErrorText')
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)
        self._show_content(False)
        if root:
            self.reload()

    def _show_content(self, visible: bool, note: str = '') -> None:
        self.split.setVisible(visible)
        self.empty.setVisible(not visible)
        self.empty.setText(note or '載入中…')
        if self.embedded:
            self.split.setFixedHeight(300 if self.width() >= 650 else 420)

    def on_action(self, _action: str, _arg: str) -> None:
        """The diff page has no buttons; links in it are not followed."""

    # -- running git off the UI thread ------------------------------------------------
    def _run(self, job: Callable[[], object], then: Callable[[object], None], name: str = '') -> None:
        generation = self._generation
        self.busy += 1
        self._sync_buttons()

        def work():
            try:
                result, error = job(), None
            except (gitops.GitError, OSError) as e:
                result, error = None, f'{name}失敗：{e}' if name else str(e)
            try:
                self.relay.done.emit(self._finished(then, generation), result, error)
            except RuntimeError:
                pass  # the page/window was destroyed while the read was pending
        threading.Thread(target=work, name='AgentDock git', daemon=True).start()

    def _finished(self, then: Callable[[object], None], generation: int) -> Callable[[object, object], None]:
        def finish(result, error):
            self.busy -= 1
            if generation != self._generation:
                return
            if error:
                if self.status is None:
                    self._show_content(False, "無法讀取 Git 狀態")
                self.error.setText(error)
                self.error.show()
            else:
                self.error.hide()
                then(result)
            self._sync_buttons()
        return finish

    def restyle(self) -> None:
        from agentdock.ui.chat_common import _style
        self.setStyleSheet(_style() + f'''
            QTreeWidget#GitChanges, QTextBrowser {{
                background: {theme.CARD}; color: {theme.INK};
                border: 1px solid {theme.LINE}; border-radius: 6px;
            }}
        ''')

    def set_root(self, directory: str, force: bool = False) -> None:
        if directory != self.directory or force:
            self.directory = directory
            self.reload()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, 'split'):
            self.split.setOrientation(Qt.Orientation.Vertical if self.width() < 650 else Qt.Orientation.Horizontal)
            if self.embedded:
                self.split.setFixedHeight(420 if self.width() < 650 else 300)

    def reload(self) -> None:
        self._generation += 1
        self._diff_serial += 1
        directory = self.directory
        self.status = None
        self.changes.clear()
        self.branch.setText('')
        self.tracking.setText('')
        self._show_content(False)
        self.external_button.setEnabled(False)
        self._set_diff('', '', '')
        def read():
            root = gitops.repo_root(directory)
            return (root, gitops.status(root), gitops.numstat(root, True), gitops.numstat(root, False)) if root else None
        self._run(read, self._loaded)

    def _external(self) -> None:
        if self.external:
            self.external('git', self.root)
        else:
            from agentdock.ui.external_tools import open_target
            open_target('git', self.root)

    # -- showing -------------------------------------------------------------------------
    def _loaded(self, result) -> None:
        if result is None:
            self.root = ''
            self.tracking.setText('')
            self._show_content(False, '此資料夾不是 Git 儲存庫')
            return
        self.root, status, staged_stats, work_stats = result
        self.external_button.setEnabled(True)
        selected = self._selected_key()
        self.status = status
        self._show_content(bool(status.changes), '沒有未提交的變更')
        self.stats = {**{(p, True): s for p, s in staged_stats.items()}, **{(p, False): s for p, s in work_stats.items()}}
        self.branch.setText(status.branch or '分離 HEAD')
        # Where pushing goes, said plainly: master → origin/main ↑2.
        if status.upstream:
            parts = [f'→ {status.upstream}']
            if status.ahead:
                parts.append(f'↑{status.ahead}')
            if status.behind:
                parts.append(f'↓{status.behind}')
        else:
            parts = ['沒有上游分支']
        self.tracking.setText('  '.join(parts))
        self.changes.clear()
        restore = None
        for staged in (True, False):
            group = [c for c in status.changes if (c.staged if staged else c.unstaged)]
            if not group:
                continue
            top = QTreeWidgetItem([f"{'已暫存' if staged else '變更'}（{len(group)}）"])
            top.setData(0, HEAD, staged)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.changes.addTopLevelItem(top)
            for change in group:
                letter = change.index if staged else change.work
                item = QTreeWidgetItem([f'{change.path}    {LETTERS.get(letter, letter)}'])
                item.setData(0, ROLE, (change, staged))
                item.setToolTip(0, f'{change.path}　{LETTERS.get(letter, letter)}')
                top.addChild(item)
                if selected == (change.path, staged):
                    restore = item
            top.setExpanded(True)
        if restore is None and self.changes.topLevelItemCount():
            restore = self.changes.topLevelItem(0).child(0)
        if restore is not None:
            self.changes.setCurrentItem(restore)
        else:
            self._set_diff('', '', '沒有未提交的變更' if not status.changes else '選擇檔案查看差異')
        self._sync_buttons()

    def _selected_key(self):
        item = self.changes.currentItem()
        data = item.data(0, ROLE) if item else None
        return (data[0].path, data[1]) if data else None

    def _all(self) -> list[gitops.Change]:
        return list(self.status.changes) if self.status else []

    def _show_diff(self, item) -> None:
        self._diff_serial += 1
        serial = self._diff_serial
        data = item.data(0, ROLE) if item else None
        if not data:
            self._set_diff('', '', '')
            return
        change, staged = data
        root = self.root
        self._run(lambda: gitops.diff(root, change, staged),
                  lambda text: serial == self._diff_serial and self._set_diff(text or '', change.path))

    def _set_diff(self, text: str, path: str, note: str = '') -> None:
        self.diff.setHtml(diff_html(text or note or '（沒有文字差異）', 4000))
        if self.web is not None:
            page = diff_page(text, path, note)
            if len(page) > 1_800_000:   # setHtml's limit: drop the colours of a huge diff
                page = diff_page('\n'.join(text.splitlines()[:800]), path + '.txt', note)
            self.web.setHtml(page, self.web.page_url)

    def _sync_buttons(self) -> None:
        self.changes.viewport().update()


class GitDialog(QDialog):
    """Optional independent window using the same read-only panel."""
    def __init__(self, root: str, external=None):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.setProperty('cute_plain', True)
        self.setWindowTitle(f'專案變更 — {root}')
        self.resize(1080, 700)
        self._view = GitView(root, external)
        QVBoxLayout(self).addWidget(self._view)
        self.restyle()

    def __getattr__(self, name):
        view = self.__dict__.get('_view')
        if view is not None:
            return getattr(view, name)
        raise AttributeError(name)

    def restyle(self):
        from agentdock.ui.chat_common import _style
        self.setStyleSheet(_style())
        self._view.restyle()
