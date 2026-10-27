"""The Git window of a project folder: changes with their diff, stage, commit, push / pull, branches.

Nothing runs on its own: every git command follows a click (or opening the window, which reads the status).
Laid out as in VS Code / GitHub Desktop: the commit box on top of the changed files, each file as its name
with the folder dimmed, a coloured status letter and its +/− lines; the diff as a web page in GitHub's look
(line numbers, tinted rows, syntax colours) when the web view is on, otherwise as coloured text.
"""
from __future__ import annotations

import html
import os
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
    """One changed file: status letter, name, dimmed folder, +/− lines; on hover a ＋ / − to (un)stage it.
    The group rows (已暫存 / 變更) get a ＋ / − for all their files."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(100, theme.px(28))

    @staticmethod
    def glyph_rect(rect: QRect) -> QRect:
        side = theme.px(20)
        return QRect(rect.right() - side - 6, rect.center().y() - side // 2, side, side)

    def _glyph(self, index) -> str:
        """＋ / − for the row, '' when it has nothing to act on."""
        item = self.dialog.changes.itemFromIndex(index)
        if item is None:
            return ''
        head = item.data(0, HEAD)
        if head is not None:
            return ('−' if head else '＋') if item.childCount() else ''
        data = item.data(0, ROLE)
        return ('−' if data[1] else '＋') if data else ''

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
        glyph = self._glyph(index) if hovered else ''
        if glyph:
            g = self.glyph_rect(option.rect)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.CARD))
            painter.drawRoundedRect(g, 5, 5)
            painter.setPen(ink)
            painter.drawText(g, Qt.AlignmentFlag.AlignCenter, glyph)
            right = g.left() - 6
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
        mark = 'U' if letter == '?' else letter
        painter.drawText(QRect(x, rect.top(), 16, rect.height()), Qt.AlignmentFlag.AlignVCenter, mark)
        x += 22
        if stats and not glyph:
            small = QFont(option.font)
            small.setPixelSize(theme.px(11))
            painter.setFont(small)
            added, removed = f'+{stats[0]}', f'−{stats[1]}'
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

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) \
                and self._glyph(index) and self.glyph_rect(option.rect).contains(event.position().toPoint()):
            if event.type() == QEvent.Type.MouseButtonRelease:
                self.dialog.glyph_clicked(self.dialog.changes.itemFromIndex(index))
            return True
        return super().editorEvent(event, model, option, index)


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
class GitDialog(QDialog):
    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.root = root
        self.status: gitops.Status | None = None
        self.stats: dict[tuple[str, bool], tuple[int, int]] = {}
        self.busy = 0
        self.relay = _Relay(self)
        self.relay.done.connect(lambda callback, result, error: callback(result, error))
        self.setWindowTitle(f'Git — {root}')
        self.resize(1080, 700)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.branch = QComboBox()
        self.branch.setMinimumWidth(180)
        self.branch.activated.connect(self._branch_picked)
        top.addWidget(self.branch)
        self.tracking = label('', muted=True)
        top.addWidget(self.tracking, 1)
        self.pull_button = button('拉取')
        self.pull_button.clicked.connect(lambda: self._run(lambda: gitops.pull(self.root), self._said, '拉取'))
        top.addWidget(self.pull_button)
        self.push_button = button('推送')
        self.push_button.clicked.connect(lambda: self._run(lambda: gitops.push(self.root), self._said, '推送'))
        top.addWidget(self.push_button)
        refresh = icon_button('⟳', '重新整理')
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        layout.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        # Write the message, commit, then the files: as in VS Code.
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText('提交訊息')
        self.message.setFixedHeight(theme.px(64))
        self.message.textChanged.connect(self._sync_buttons)
        column.addWidget(self.message)
        self.commit_button = button('提交', primary=True)
        self.commit_button.clicked.connect(self._commit)
        column.addWidget(self.commit_button)
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
        self.changes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.changes.customContextMenuRequested.connect(self._menu)
        self.changes.currentItemChanged.connect(lambda item, _old: self._show_diff(item))
        self.changes.itemDoubleClicked.connect(lambda item, _c: self._toggle([item]))
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
        split.setSizes([380, 700])
        layout.addWidget(split, 1)
        self.error = QLabel()
        self.error.setObjectName('ErrorText')
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)
        self.reload()

    def on_action(self, _action: str, _arg: str) -> None:
        """The diff page has no buttons; links in it are not followed."""

    # -- running git off the UI thread ------------------------------------------------
    def _run(self, job: Callable[[], object], then: Callable[[object], None], name: str = '') -> None:
        self.busy += 1
        self._sync_buttons()

        def work():
            try:
                result, error = job(), None
            except (gitops.GitError, OSError) as e:
                result, error = None, f'{name}失敗：{e}' if name else str(e)
            self.relay.done.emit(self._finished(then), result, error)
        threading.Thread(target=work, name='AgentDock git', daemon=True).start()

    def _finished(self, then: Callable[[object], None]) -> Callable[[object, object], None]:
        def finish(result, error):
            self.busy -= 1
            if error:
                self.error.setText(error)
                self.error.show()
            else:
                self.error.hide()
                then(result)
            self._sync_buttons()
        return finish

    def reload(self) -> None:
        self._run(lambda: (gitops.status(self.root), gitops.branches(self.root),
                           gitops.numstat(self.root, True), gitops.numstat(self.root, False)), self._loaded)

    def _said(self, _result) -> None:
        self.reload()

    def _paths_job(self, action, paths: list[str]) -> None:
        if paths:
            self._run(lambda: action(self.root, paths), lambda _r: self.reload())

    # -- showing -------------------------------------------------------------------------
    def _loaded(self, result) -> None:
        status, branches, staged_stats, work_stats = result
        selected = self._selected_key()
        self.status = status
        self.stats = {**{(p, True): s for p, s in staged_stats.items()}, **{(p, False): s for p, s in work_stats.items()}}
        self.branch.blockSignals(True)
        self.branch.clear()
        names = branches if status.branch in branches or not status.branch else [status.branch, *branches]
        for name in names:
            self.branch.addItem(name, name)
        if not status.branch:
            self.branch.insertItem(0, '（分離 HEAD）', '')
        self.branch.setCurrentIndex(max(0, self.branch.findData(status.branch)))
        self.branch.insertSeparator(self.branch.count())
        self.branch.addItem('新增分支…', '\0new')
        self.branch.blockSignals(False)
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
        if restore is not None:
            self.changes.setCurrentItem(restore)
        else:
            self._set_diff('', '', ' ')
        self._sync_buttons()

    def _selected_key(self):
        item = self.changes.currentItem()
        data = item.data(0, ROLE) if item else None
        return (data[0].path, data[1]) if data else None

    def _all(self) -> list[gitops.Change]:
        return list(self.status.changes) if self.status else []

    def _show_diff(self, item) -> None:
        data = item.data(0, ROLE) if item else None
        if not data:
            return
        change, staged = data
        self._run(lambda: gitops.diff(self.root, change, staged), lambda text: self._set_diff(text or '', change.path))

    def _set_diff(self, text: str, path: str, note: str = '') -> None:
        self.diff.setHtml(diff_html(text or note or '（沒有文字差異）', 4000))
        if self.web is not None:
            page = diff_page(text, path, note)
            if len(page) > 1_800_000:   # setHtml's limit: drop the colours of a huge diff
                page = diff_page('\n'.join(text.splitlines()[:800]), path + '.txt', note)
            self.web.setHtml(page, self.web.page_url)

    def _sync_buttons(self) -> None:
        idle = self.busy == 0
        staged = [c for c in self._all() if c.staged]
        branch = self.status.branch if self.status else ''
        self.commit_button.setEnabled(idle and bool(staged) and bool(self.message.toPlainText().strip()))
        self.commit_button.setText(f'提交 {len(staged)} 個檔案到 {branch}' if staged and branch else '提交')
        ahead = self.status.ahead if self.status else 0
        behind = self.status.behind if self.status else 0
        self.push_button.setEnabled(idle and bool(branch))
        self.push_button.setText(f'推送 {ahead} 個提交' if ahead else '推送')
        self.pull_button.setEnabled(idle and bool(self.status and self.status.upstream))
        self.pull_button.setText(f'拉取 {behind} 個提交' if behind else '拉取')
        # The thing to do next stands out: pull when behind, else push when ahead.
        for b, main in ((self.pull_button, bool(behind)), (self.push_button, bool(ahead) and not behind)):
            if b.property('primary') != main:
                b.setProperty('primary', main)
                b.style().unpolish(b)
                b.style().polish(b)
        self.branch.setEnabled(idle)
        self.changes.viewport().update()

    # -- actions ---------------------------------------------------------------------------
    def glyph_clicked(self, item) -> None:
        """The ＋ / − on a row: that file, or every file of a group."""
        head = item.data(0, HEAD)
        if head is None:
            self._toggle([item])
        elif head:
            self._paths_job(gitops.unstage, [c.path for c in self._all() if c.staged])
        else:
            self._paths_job(gitops.stage, [c.path for c in self._all() if c.unstaged])

    def _toggle(self, items) -> None:
        stage, unstage = [], []
        for item in items:
            data = item.data(0, ROLE)
            if data:
                (unstage if data[1] else stage).append(data[0].path)
        if stage or unstage:
            def job():
                gitops.stage(self.root, stage)
                gitops.unstage(self.root, unstage)
            self._run(job, lambda _r: self.reload())

    def _menu(self, pos) -> None:
        items = [i for i in self.changes.selectedItems() if i.data(0, ROLE)]
        if not items:
            return
        menu = QMenu(self)
        staged = [i for i in items if i.data(0, ROLE)[1]]
        unstaged = [i for i in items if not i.data(0, ROLE)[1]]
        if unstaged:
            menu.addAction('暫存', lambda: self._toggle(unstaged))
        if staged:
            menu.addAction('取消暫存', lambda: self._toggle(staged))
        if len(unstaged) == 1 and not staged:
            menu.addSeparator()
            menu.addAction('捨棄變更…', lambda: self._discard(unstaged[0].data(0, ROLE)[0]))
        menu.exec(self.changes.viewport().mapToGlobal(pos))

    def _discard(self, change: gitops.Change) -> None:
        what = '刪除這個新檔' if change.index == '?' else '捨棄這個檔案未暫存的變更'
        answer = QMessageBox.question(self, '捨棄變更', f'{what}？\n{change.path}\n\n無法復原。')
        if answer == QMessageBox.StandardButton.Yes:
            self._run(lambda: gitops.discard(self.root, change), lambda _r: self.reload())

    def _commit(self) -> None:
        message = self.message.toPlainText().strip()

        def done(_summary):
            self.message.clear()
            self.reload()
        self._run(lambda: gitops.commit(self.root, message), done, '提交')

    def _branch_picked(self, index: int) -> None:
        target = self.branch.itemData(index)
        current = self.status.branch if self.status else ''
        if target == '\0new':
            name, ok = QInputDialog.getText(self, '新增分支', '分支名稱')
            if ok and name.strip():
                self._run(lambda: gitops.checkout(self.root, name.strip(), create=True), self._said, '建立分支')
            else:
                self.branch.setCurrentIndex(max(0, self.branch.findData(current)))
        elif target and target != current:
            self._run(lambda: gitops.checkout(self.root, target), self._said, '切換分支')
