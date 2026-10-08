"""Latest task summaries with independent, inline question foldouts."""
import weakref
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from agentdock.ui.qa_view import QaView, button, label
from agentdock.ui.widgets import icon_button


class QuestionFoldout(QWidget):
    def __init__(self, inbox, question):
        super().__init__()
        self.inbox, self.question = inbox, question
        self.editor = None
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        title = question.get('group', {}).get('intro') or question['question'] or '待回答問題'
        self.toggle = button('▸ 待回答', flat=True)
        self.toggle.setToolTip(title)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.set_expanded)
        self.layout_box.addWidget(self.toggle)

    def set_expanded(self, expanded):
        if expanded:
            self.inbox.store.mark_question_opened(self.question['id'])
        if expanded and self.editor is None:
            self.editor = QaView(self.inbox.store, inline=True)
            self.editor.closed.connect(self.inbox.refresh)
            self.editor.answered.connect(self.inbox.answered.emit)
            self.editor.open_question(self.question['id'])
            self.layout_box.addWidget(self.editor)
        if self.editor:
            if not expanded:
                self.editor._flush()
            self.editor.setVisible(expanded)
        self.toggle.setText(('▾ ' if expanded else '▸ ') + self.toggle.text()[2:])

    def flush(self):
        if self.editor:
            self.editor._flush()


class AgentGroup(QWidget):
    def __init__(self, inbox, source):
        super().__init__()
        self.inbox, self.source = weakref.proxy(inbox), source
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = button('▾ ' + source, flat=True)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.items = QVBoxLayout(self.content)
        self.items.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded):
        if not expanded:
            for row in self.inbox.rows.values():
                if row.group is self:
                    for fold in row.foldouts.values():
                        fold.flush()
        self.toggle.setText(('▾ ' if expanded else '▸ ') + self.source)
        self.content.setVisible(expanded)
        QTimer.singleShot(0, self.inbox._mark_read)


class TaskRow(QFrame):
    def __init__(self, inbox, key):
        super().__init__()
        self.inbox, self.key = weakref.proxy(inbox), key
        self.setObjectName('Row')
        self.foldouts = {}
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 12, 12, 12)
        head = QHBoxLayout()
        self.title = label('')
        head.addWidget(self.title, 1)
        self.remove_button = remove = icon_button('×', '移除此任務（不取消 Agent 任務）', danger=True)
        remove.clicked.connect(self.remove)
        head.addWidget(remove)
        self.body.addLayout(head)
        self.status = label('', muted=True)
        self.body.addWidget(self.status)
        self.read_id = None

    def remove(self):
        # A pending question cannot be discarded by removing its task card.
        if self.foldouts:
            return
        self.inbox.store.remove_conversation(*self.key)
        self.inbox.refresh()

    def update_task(self, task):
        self.title.setText(task['work_title'])
        units = QaView._units(task['pending'])
        wanted = {unit[0]['id'] for unit in units}
        for qid in set(self.foldouts) - wanted:
            fold = self.foldouts.pop(qid)
            fold.flush()
            self.body.removeWidget(fold)
            fold.hide()
            fold.deleteLater()
        for unit in units:
            q = unit[0]
            if q['id'] not in self.foldouts:
                fold = QuestionFoldout(self.inbox, q)
                self.foldouts[q['id']] = fold
                self.body.addWidget(fold)
        latest = task['latest']
        self.read_id = None
        self.status.setVisible(not units)
        if units:
            self.status.setText('待回答')
        elif latest['kind'] == 'native':
            state = latest['state']
            text = {'running': '進行中（近期活動）', 'approval': '待授權 · 請回原客戶端確認',
                    'unknown': '狀態未知 · 已超過 2 分鐘未更新或 Inbox 已重啟',
                    'idle': '回合結束', 'ended': '工作階段結束',
                    'interrupted': '已中斷', 'error': '執行出錯'}[state]
            stamp = datetime.fromtimestamp(latest['at']).strftime('%H:%M:%S')
            if state == 'unknown' and latest.get('approvals'):
                text += '\n上次收到授權請求，是否已處理請至原客戶端確認'
            self.status.setText(f'{text}\n最近活動 {stamp}' +
                                (f"\n{latest['detail']}" if state == 'approval' and latest['detail'] else ''))
            if not latest['read']:
                self.read_id = latest['id']
        elif latest['kind'] == 'question':
            self.status.setText('等待 Agent 回報')
        else:
            self.status.setText({'completed': '已完成', 'failed': '失敗',
                                 'cancelled': '已取消'}[latest['kind']])
            if not latest['read']:
                self.read_id = latest['id']
        self.remove_button.setEnabled(not units)


class InboxView(QWidget):
    pending_changed = Signal(int)
    pending_items = Signal(list)
    answered = Signal()
    read_finished = Signal()

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.rows = {}
        self.groups = {}
        self._limit = 60
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label('Inbox · 問答與任務結果'))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.body = QWidget()
        self.items = QVBoxLayout(self.body)
        self.items.setContentsMargins(4, 4, 4, 4)
        self.empty = label('尚無待辦或結果通知。', muted=True)
        self.items.addWidget(self.empty)
        self.more = button('顯示更多任務', flat=True)
        self.more.clicked.connect(self.load_more)
        self.items.addWidget(self.more)
        self.items.addStretch()
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll)
        self.read_finished.connect(self.refresh)
        self.scroll.verticalScrollBar().valueChanged.connect(self._mark_read)
        self.refresh()

    def refresh(self):
        tasks = self.store.inbox_tasks()
        pending = [q for task in tasks for q in task['pending']]
        units = QaView._units(pending)
        self.pending_changed.emit(len(units))
        self.pending_items.emit([{'id': u[0]['id'], 'source': u[0]['source'], 'question': u[0]['question']} for u in units])
        shown = [t for i, t in enumerate(tasks) if i < self._limit or t['pending'] or t['state'] == 'approval']
        wanted = {(t['owner'], t['work_id']) for t in shown}
        for key in set(self.rows) - wanted:
            row = self.rows.pop(key)
            for fold in row.foldouts.values(): fold.flush()
            row.group.items.removeWidget(row)
            row.hide()
            row.deleteLater()
        # Keep existing rows in place, so incoming results never move an editor.
        for task in shown:
            key = task['owner'], task['work_id']
            source = task['source'] or 'Agent'
            if source not in self.groups:
                self.groups[source] = AgentGroup(self, source)
                self.items.insertWidget(self.items.indexOf(self.more), self.groups[source])
            group = self.groups[source]
            if key not in self.rows:
                self.rows[key] = TaskRow(self, key)
                self.rows[key].group = group
                group.items.addWidget(self.rows[key])
            row = self.rows[key]
            if row.group is not group:
                row.group.items.removeWidget(row)
                row.group = group
                group.items.addWidget(row)
            row.update_task(task)
        for group in self.groups.values():
            group.setVisible(group.items.count() > 0)
        self.empty.setVisible(not shown)
        self.more.setVisible(len(shown) < len(tasks))
        QTimer.singleShot(0, self._mark_read)

    def load_more(self):
        self._limit += 60
        self.refresh()

    def open_question(self, qid):
        self.refresh()
        for row in self.rows.values():
            for fold in row.foldouts.values():
                if any(q['id'] == qid for q in self.store.group_members(fold.question['id'])):
                    row.group.toggle.setChecked(True)
                    fold.toggle.setChecked(True)
                    self.scroll.ensureWidgetVisible(fold)
                    QTimer.singleShot(0, fold, lambda f=fold: self.scroll.ensureWidgetVisible(f))
                    return

    def show_first_pending(self):
        # Opening Inbox does not choose a question or disturb an existing draft.
        self.refresh()

    def notification_target(self, new_only=False):
        tasks = self.store.inbox_tasks()
        for task in tasks:
            if task['state'] == 'approval' and (not new_only or not task['latest']['read']):
                return ('task', task['owner'], task['work_id'])
        pending = [q for t in tasks for q in t['pending']]
        opened = self.store.opened_questions()
        new = [q for q in pending if q['id'] not in opened]
        if new:
            return ('question', max(new, key=lambda q: (q['created_at'], q['id']))['id'])
        if pending and not new_only:
            return ('question', min(pending, key=lambda q: (q['created_at'], q['id']))['id'])
        priority = {'failed': 0, 'completed': 1, 'cancelled': 2, 'native': 3}
        for task in sorted(tasks, key=lambda t: priority.get(t['latest']['kind'], 4)):
            latest = task['latest']
            if latest['kind'] != 'question' and not latest['read'] and task['state'] not in ('unknown', 'running'):
                return ('task', task['owner'], task['work_id'])
        return None

    def open_notification(self, target):
        if target[0] == 'question':
            self.open_question(target[1])
        else:
            self.open_conversation(*target[1:])

    def open_conversation(self, owner, work_id):
        for i, task in enumerate(self.store.inbox_tasks()):
            if (task['owner'], task['work_id']) == (owner, work_id):
                self._limit = max(self._limit, i + 1)
                break
        self.refresh()
        if (owner, work_id) in self.rows:
            row = self.rows[(owner, work_id)]
            row.group.toggle.setChecked(True)
            self.scroll.ensureWidgetVisible(row)
            QTimer.singleShot(0, row, lambda r=row: self.scroll.ensureWidgetVisible(r))

    def tick(self):
        if self.store.activity.rows:
            self.refresh()
        for row in self.rows.values():
            for fold in row.foldouts.values():
                if fold.editor and fold.toggle.isChecked(): fold.editor.tick()

    def _flush(self):
        for row in self.rows.values():
            for fold in row.foldouts.values(): fold.flush()

    def _mark_read(self, *_):
        if not self.isVisible() or not self.window().isActiveWindow(): return
        viewport = self.scroll.viewport()
        for row in self.rows.values():
            if row.read_id and row.status.isVisible() and viewport.rect().intersects(
                    row.status.rect().translated(row.status.mapTo(viewport, row.status.rect().topLeft()))):
                self.store.queue_mark_read(*row.key, [row.read_id], self.read_finished.emit)

    def showEvent(self, event):
        super().showEvent(event)
        self.window().installEventFilter(self)
        QTimer.singleShot(0, self._mark_read)

    def eventFilter(self, watched, event):
        if watched is self.window() and event.type() == QEvent.Type.WindowActivate:
            QTimer.singleShot(0, self._mark_read)
        return super().eventFilter(watched, event)
