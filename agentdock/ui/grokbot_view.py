"""Account-backed MCP management, isolated from local-file transactions."""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from agentdock import grokbot


class RemoteEditor(QDialog):
    def __init__(self, parent, name='', entry=None):
        super().__init__(parent)
        self.setWindowTitle('遠端 MCP')
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        self.name.setReadOnly(bool(name))
        self.url = QLineEdit((entry or {}).get('url', ''))
        self.kind = QComboBox()
        self.kind.addItems(['http', 'sse'])
        self.kind.setCurrentText((entry or {}).get('type', 'http'))
        self.headers = QPlainTextEdit(json.dumps((entry or {}).get('headers', {}), ensure_ascii=False, indent=2))
        self.headers.setMaximumHeight(140)
        form.addRow('名稱', self.name)
        form.addRow('網址', self.url)
        form.addRow('類型', self.kind)
        form.addRow('Headers', self.headers)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self):
        entry = {'url': self.url.text().strip(), 'type': self.kind.currentText()}
        headers = json.loads(self.headers.toPlainText())
        if headers:
            entry['headers'] = headers
        return self.name.text().strip(), grokbot.remote_entry(entry)

    def accept(self):
        try:
            self.value()
        except (ValueError, grokbot.GrokBotError) as e:
            QMessageBox.warning(self, '遠端 MCP', str(e))
            return
        super().accept()


class GrokBotMcpDialog(QDialog):
    _finished = Signal(object, object)

    def __init__(self, parent, library, profile, backup_dir, client=None):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle('GrokBot 帳號 MCP')
        self.resize(620, 440)
        self.library, self.profile, self.backup_dir = library, profile, backup_dir
        self.client = client or grokbot.Client()
        self.snapshot = None
        self.busy = False
        self._completion = None
        layout = QVBoxLayout(self)
        self.account = QLabel()
        self.account.setTextFormat(Qt.TextFormat.PlainText)
        self.account.setWordWrap(True)
        layout.addWidget(self.account)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['名稱', '接入'])
        layout.addWidget(self.tree)
        controls = QHBoxLayout()
        self.refresh_btn = QPushButton('重新整理')
        self.add_btn = QPushButton('新增')
        self.edit_btn = QPushButton('編輯')
        self.remove_btn = QPushButton('移除')
        self.restore_btn = QPushButton('從備份還原')
        self.buttons = [self.refresh_btn, self.add_btn, self.edit_btn, self.remove_btn, self.restore_btn]
        for button in self.buttons:
            controls.addWidget(button)
        layout.addLayout(controls)
        row = QHBoxLayout()
        self.library_choice = QComboBox()
        for entry in library.entries():
            if entry['type'] == 'remote':
                self.library_choice.addItem(entry['key'], entry)
        self.import_btn = QPushButton('從 MCP 庫加入')
        self.buttons.append(self.import_btn)
        row.addWidget(self.library_choice, 1)
        row.addWidget(self.import_btn)
        layout.addLayout(row)
        self.refresh_btn.clicked.connect(self.reload)
        self.add_btn.clicked.connect(lambda: self.edit(False))
        self.edit_btn.clicked.connect(lambda: self.edit(True))
        self.remove_btn.clicked.connect(self.remove)
        self.restore_btn.clicked.connect(self.restore)
        self.import_btn.clicked.connect(self.import_entry)
        self._finished.connect(self._done)
        self.tree.itemSelectionChanged.connect(self._buttons)
        self.reload()

    def _buttons(self):
        for button in self.buttons:
            button.setEnabled(not self.busy and (button is self.refresh_btn or self.snapshot is not None))
        self.import_btn.setEnabled(not self.busy and self.snapshot is not None and self.library_choice.count() > 0)
        editable = False
        name = self.selected()
        if self.snapshot is not None and name:
            try:
                grokbot.remote_entry(self.snapshot.config['mcpServers'][name])
                editable = self.snapshot.metadata.get(name, {}).get('pluginId') in (None, 0, '0')
            except grokbot.GrokBotError:
                pass
        self.edit_btn.setEnabled(not self.busy and editable)
        self.remove_btn.setEnabled(not self.busy and editable)

    def _run(self, work, completion, message):
        if self.busy:
            return
        self.busy = True
        self._completion = completion
        self.status.setText(message)
        self._buttons()

        def run():
            try:
                result, error = work(), None
            except grokbot.GrokBotError as e:
                result, error = None, str(e)
            except Exception:
                result, error = None, 'GrokBot 操作失敗，請重新整理。'
            self._finished.emit(result, error)

        threading.Thread(target=run, daemon=True).start()

    def _done(self, result, error):
        self.busy = False
        completion, self._completion = self._completion, None
        if error:
            self.snapshot = None
            self.account.clear()
            self.tree.clear()
            self.status.setText(error)
        elif completion:
            completion(result)
        self._buttons()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def reload(self):
        self._run(self.client.snapshot, self.loaded, '正在讀取帳號 MCP…')

    def loaded(self, snapshot):
        self.snapshot = snapshot
        self.account.setText(snapshot.identity.label or '目前登入帳號')
        self.tree.clear()
        for name, entry in snapshot.config['mcpServers'].items():
            plugin = snapshot.metadata.get(name, {}).get('pluginId') not in (None, 0, '0')
            kind = '外掛管理' if plugin else '遠端 MCP' if 'url' in entry else '雲端指令'
            self.tree.addTopLevelItem(QTreeWidgetItem([name, kind]))
        self.status.setText('變更會同步到此帳號的客戶端。雲端指令與外掛由 GrokBot 管理。')

    def selected(self):
        item = self.tree.currentItem()
        return item.text(0) if item else None

    def edit(self, existing):
        name = self.selected() if existing else ''
        if existing and not name:
            return
        dialog = RemoteEditor(self, name or '', self.snapshot.config['mcpServers'].get(name))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, entry = dialog.value()
            self.propose([{'name': name, 'op': 'update' if existing else 'add', 'entry': entry}])

    def remove(self):
        name = self.selected()
        if name:
            self.propose([{'name': name, 'op': 'remove'}])

    def import_entry(self):
        entry = self.library_choice.currentData()
        if not entry:
            return
        try:
            rendered = self.library.spec(entry, self.profile)
            self.propose([{'name': entry['key'], 'op': 'add', 'entry': rendered}])
        except (ValueError, grokbot.GrokBotError) as e:
            self.status.setText(str(e))

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, '選擇 GrokBot 備份', str(self.backup_dir), 'JSON (*.json)')
        if not path:
            return
        try:
            from pathlib import Path
            backup = grokbot._json_file(Path(path))
            self.confirm(grokbot.prepare_restore(self.snapshot, backup))
        except grokbot.GrokBotError as e:
            self.status.setText(str(e))

    def propose(self, operations):
        try:
            change = grokbot.prepare(self.snapshot, operations)
        except grokbot.GrokBotError as e:
            self.status.setText(str(e))
            return
        self.confirm(change)

    def confirm(self, change):
        if change.config == change.original.config:
            self.status.setText('沒有需要變更的設定。')
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle('確認 GrokBot 變更')
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText('\n'.join(change.summary))
        dialog.setInformativeText('原設定會先備份。網址與 Headers 會存到帳號服務，並影響使用此帳號的客戶端。\n'
                                  '請勿同時在其他客戶端修改 MCP；服務不提供原子衝突鎖定。')
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Ok).setText('確認套用')
        if dialog.exec() != QMessageBox.StandardButton.Ok:
            return
        self._run(lambda: grokbot.apply(self.client, change, self.backup_dir), self.applied, '正在備份並套用…')

    def applied(self, backup):
        self.snapshot = None
        self.tree.clear()
        self.status.setText(f'已套用並備份：{backup}\n在 GrokBot 重新整理 MCP 後生效。')
