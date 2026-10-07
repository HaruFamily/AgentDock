import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QLabel
from agentdock.ui.inbox_view import InboxView
from agentdock.qa.store import QuestionStore
from test_agentchat import question, event, OWNER


def test_latest_only_and_restart(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive(OWNER, 'Codex', event('old', text='OLD_RESULT'))
    store.receive(OWNER, 'Codex', event('new', text='NEW_RESULT'))
    view = InboxView(QuestionStore(tmp_path))
    row = view.rows[(OWNER, 'w')]
    assert row.status.text() == '已完成'
    store.receive(OWNER, 'Codex', event('failed', kind='failed'))
    view.store = store
    view.refresh()
    assert row.status.text() == '失敗' and not row.status.toolTip()
    store.receive(OWNER, 'Codex', event('cancelled', kind='cancelled'))
    view.refresh()
    assert row.status.text() == '已取消' and not row.status.toolTip()
    assert not any('RESULT' in w.text() for w in row.findChildren(QLabel))
    assert not row.foldouts
    assert not any('歷史' in b.text() or '返回' in b.text() for b in view.findChildren(QPushButton))
    assert len(store.conversations()[0]['entries']) == 4
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_notification_priority_and_opened_questions_survive_restart(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    old = question(store, 'old', work_id='old-task')
    view = InboxView(store)
    view.open_question(old['id'])
    new = question(store, 'new', work_id='new-task')
    store.receive(OWNER, 'Codex', event('result', work_id='done'))
    target = view.notification_target()
    assert target == ('question', new['id'])
    view.open_notification(target)
    assert view.rows[(OWNER, 'new-task')].foldouts[new['id']].toggle.isChecked()
    assert QuestionStore(tmp_path).opened_questions() == {old['id'], new['id']}
    assert view.notification_target() == ('question', old['id'])
    store.answer(old['id'], {'selected': [], 'text': 'answer', 'notes': {}, 'attachment_ids': []})
    assert view.notification_target() == ('question', new['id'])
    store.answer(new['id'], {'selected': [], 'text': 'answer', 'notes': {}, 'attachment_ids': []})
    target = view.notification_target()
    assert target == ('task', OWNER, 'done')
    view._limit = 0
    view.refresh()
    view.open_notification(target)
    assert (OWNER, 'done') in view.rows
    row = view.rows[(OWNER, 'done')]
    assert row.group.toggle.isChecked()
    store.mark_read(OWNER, 'done', [row.read_id])
    assert view.notification_target() is None
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_both_small_icons_route_clicks_to_question(tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from agentdock.ui.app import Dock, LauncherIcon
    from agentdock.ui.card import FloatingCard
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    view = InboxView(store)
    ball, icon = FloatingCard(), LauncherIcon()
    ball.set_qa(view)
    ball.set_mode('heart')
    icon.set_mode('heart')
    dock = SimpleNamespace(ball=ball, icon=icon, qa=view)
    dock.summon = lambda: ball.set_mode('card')
    dock._open_notification = lambda: Dock._open_notification(dock)
    dock.toggle_card = lambda: Dock.toggle_card(dock)
    ball.launcher_clicked.connect(dock._open_notification)
    icon.mode_changed.connect(lambda mode: Dock._icon_clicked(dock, mode))
    ball.show()
    app.processEvents()
    QTest.mouseClick(ball, Qt.MouseButton.LeftButton, pos=ball.rect().center())
    fold = view.rows[(OWNER, 'w')].foldouts[q['id']]
    assert ball.page == 'qa' and fold.toggle.isChecked()
    fold.toggle.setChecked(False)
    ball.set_page('quota')
    icon.show()
    app.processEvents()
    QTest.mouseClick(icon, Qt.MouseButton.LeftButton, pos=icon.rect().center())
    assert ball.mode == 'card' and ball.page == 'qa' and fold.toggle.isChecked()
    ball.close()
    icon.close()
    ball.deleteLater()
    icon.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_foldouts_preserve_focus_draft_and_submit(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    view = InboxView(store)
    view.resize(600, 800)
    view.show()
    view.activateWindow()
    view.open_question(q['id'])
    app.processEvents()
    row = view.rows[(OWNER, 'w')]
    fold = row.foldouts[q['id']]
    editor = fold.editor
    text = editor.blocks[q['id']]['text']
    text.setPlainText('my draft')
    text.setFocus()
    app.processEvents()
    q2 = question(store, 'q2')
    view.refresh()
    app.processEvents()
    assert fold.editor is editor and fold.toggle.isChecked()
    assert text.toPlainText() == 'my draft'
    assert app.focusWidget() is text
    assert len(row.foldouts) == 2
    assert not row.foldouts[q2['id']].toggle.isChecked()
    store.activity.receive(OWNER, dict(client='codex', session_id='native', title='Native session', state='approval'))
    view._limit = 0
    view.refresh()
    app.processEvents()
    native = next(r for key, r in view.rows.items() if key[1].startswith('native:'))
    assert '待授權' in native.status.text() and not native.status.toolTip()
    assert app.focusWidget() is text and fold.editor is editor
    view._limit = 60
    fold.toggle.setChecked(False)
    assert store.get(q['id'])['draft']['text'] == 'my draft'
    fold.toggle.setChecked(True)
    editor._submit_now()
    assert q['id'] not in row.foldouts
    assert q2['id'] in row.foldouts
    view.open_question(q2['id'])
    row.foldouts[q2['id']].editor.blocks[q2['id']]['text'].setPlainText('answer')
    row.foldouts[q2['id']].editor._submit_now()
    assert not row.foldouts
    assert row.status.text() == '等待 Agent 回報'
    store.receive(OWNER, 'Codex', event(text='DONE'))
    view.refresh()
    assert row.status.text() == '已完成'
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pending_beyond_task_limit_and_cancel(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store, work_id='pending')
    for i in range(3):
        store.receive(OWNER, 'Codex', event(str(i), work_id=str(i)))
    view = InboxView(store)
    view._limit = 1
    view.refresh()
    assert (OWNER, 'pending') in view.rows
    view.open_question(q['id'])
    row = view.rows[(OWNER, 'pending')]
    row.foldouts[q['id']].editor._cancel([store.get(q['id'])])
    assert store.get(q['id'])['status'] == 'cancelled'
    assert (OWNER, 'pending') not in view.rows
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_only_visible_latest_result_marks_read(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive(OWNER, 'Codex', event('old', text='old'))
    store.receive(OWNER, 'Codex', event('new', text='new'))
    view = InboxView(store)
    reads = []
    store.queue_mark_read = lambda owner, work, ids, done: reads.extend(ids)
    view._mark_read()
    assert reads == []
    view.resize(600, 800)
    view.show()
    view.activateWindow()
    app.processEvents()
    view._mark_read()
    latest = store.inbox_tasks()[0]['latest']['id']
    assert reads and set(reads) == {latest}
    view.hide()
    reads.clear()
    view._mark_read()
    assert not reads
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_agent_groups_collapse_preserves_draft_and_unread(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    store.receive('b' * 64, 'OpenCode', event())
    view = InboxView(store)
    assert set(view.groups) == {'Codex', 'OpenCode'}
    view.open_question(q['id'])
    row = view.rows[(OWNER, 'w')]
    editor = row.foldouts[q['id']].editor
    editor.blocks[q['id']]['text'].setPlainText('saved draft')
    group = view.groups['Codex']
    group.toggle.setChecked(False)
    assert store.get(q['id'])['draft']['text'] == 'saved draft'
    question(store, 'next')
    view.refresh()
    assert not group.toggle.isChecked()
    assert row.foldouts[q['id']].editor is editor
    assert group.content.isHidden()
    reads = []
    store.queue_mark_read = lambda owner, work, ids, done: reads.extend(ids)
    view.groups['OpenCode'].toggle.setChecked(False)
    view.resize(600, 800)
    view.show()
    view.activateWindow()
    app.processEvents()
    view._mark_read()
    assert not reads
    view.groups['OpenCode'].toggle.setChecked(True)
    app.processEvents()
    assert reads
    view.open_question(q['id'])
    assert group.toggle.isChecked()
    assert editor.blocks[q['id']]['text'].toPlainText() == 'saved draft'
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
