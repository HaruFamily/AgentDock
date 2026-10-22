"""The Git window of a project folder: changes with their diff, stage, commit, push / pull, branches.

Nothing runs on its own: every git command follows a click (or opening the window, which reads the status).
"""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from agentdock import gitops
from agentdock.ui.chat_window import diff_html
from agentdock.ui.qa_view import button, label

ROLE = Qt.ItemDataRole.UserRole
LETTERS = {'M': '修改', 'A': '新增', 'D': '刪除', 'R': '改名', 'C': '複製', 'U': '衝突', '?': '新檔', 'T': '類型'}


class _Relay(QObject):
    done = Signal(object, object, object)   # callback, result, error


class GitDialog(QDialog):
    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.root = root
        self.status: gitops.Status | None = None
        self.busy = 0
        self.relay = _Relay(self)
        self.relay.done.connect(lambda callback, result, error: callback(result, error))
        self.setWindowTitle(f'Git — {root}')
        self.resize(1000, 680)
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
        refresh = button('重新整理', flat=True)
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        layout.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        self.changes = QTreeWidget()
        self.changes.setHeaderHidden(True)
        self.changes.setRootIsDecorated(True)
        self.changes.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.changes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.changes.customContextMenuRequested.connect(self._menu)
        self.changes.currentItemChanged.connect(lambda item, _old: self._show_diff(item))
        self.changes.itemDoubleClicked.connect(lambda item, _c: self._toggle([item]))
        column.addWidget(self.changes, 1)
        stage_row = QHBoxLayout()
        self.stage_all = button('全部暫存', flat=True)
        self.stage_all.clicked.connect(lambda: self._paths_job(gitops.stage, [c.path for c in self._all()
                                                                              if c.unstaged]))
        stage_row.addWidget(self.stage_all)
        self.unstage_all = button('全部取消暫存', flat=True)
        self.unstage_all.clicked.connect(lambda: self._paths_job(gitops.unstage, [c.path for c in self._all()
                                                                                  if c.staged]))
        stage_row.addWidget(self.unstage_all)
        stage_row.addStretch(1)
        column.addLayout(stage_row)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText('提交訊息')
        self.message.setFixedHeight(84)
        self.message.textChanged.connect(self._sync_buttons)
        column.addWidget(self.message)
        commit_row = QHBoxLayout()
        commit_row.addStretch(1)
        self.commit_button = button('提交', primary=True)
        self.commit_button.clicked.connect(self._commit)
        commit_row.addWidget(self.commit_button)
        column.addLayout(commit_row)
        split.addWidget(left)
        self.diff = QTextBrowser()
        self.diff.setOpenLinks(False)
        self.diff.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        split.addWidget(self.diff)
        split.setSizes([360, 640])
        layout.addWidget(split, 1)
        self.error = QLabel()
        self.error.setObjectName('ErrorText')
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)
        self.reload()

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
        self._run(lambda: (gitops.status(self.root), gitops.branches(self.root)), self._loaded)

    def _said(self, _result) -> None:
        self.reload()

    def _paths_job(self, action, paths: list[str]) -> None:
        if paths:
            self._run(lambda: action(self.root, paths), lambda _r: self.reload())

    # -- showing -------------------------------------------------------------------------
    def _loaded(self, result) -> None:
        status, branches = result
        selected = self._selected_key()
        self.status = status
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
        parts = []
        if status.upstream:
            parts.append(status.upstream)
            if status.ahead:
                parts.append(f'↑{status.ahead}')
            if status.behind:
                parts.append(f'↓{status.behind}')
        else:
            parts.append('沒有上游分支')
        self.tracking.setText('  '.join(parts))
        self.changes.clear()
        restore = None
        for title, staged in (('已暫存', True), ('變更', False)):
            group = [c for c in status.changes if (c.staged if staged else c.unstaged)]
            top = QTreeWidgetItem([f'{title}（{len(group)}）'])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.changes.addTopLevelItem(top)
            for change in group:
                letter = change.index if staged else change.work
                text = f'{change.path}    {LETTERS.get(letter, letter)}'
                if change.old and staged:
                    text = f'{change.old} → {change.path}    改名'
                item = QTreeWidgetItem([text])
                item.setData(0, ROLE, (change, staged))
                top.addChild(item)
                if selected == (change.path, staged):
                    restore = item
            top.setExpanded(True)
        if restore is not None:
            self.changes.setCurrentItem(restore)
        else:
            self.diff.clear()
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
        self._run(lambda: gitops.diff(self.root, change, staged),
                  lambda text: self.diff.setHtml(diff_html(text or '（沒有文字差異）', 4000)))

    def _sync_buttons(self) -> None:
        idle = self.busy == 0
        staged = any(c.staged for c in self._all())
        self.commit_button.setEnabled(idle and staged and bool(self.message.toPlainText().strip()))
        self.stage_all.setEnabled(idle and any(c.unstaged for c in self._all()))
        self.unstage_all.setEnabled(idle and staged)
        self.push_button.setEnabled(idle and bool(self.status and self.status.branch))
        self.pull_button.setEnabled(idle and bool(self.status and self.status.upstream))
        self.branch.setEnabled(idle)

    # -- actions ---------------------------------------------------------------------------
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
