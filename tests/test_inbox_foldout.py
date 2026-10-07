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
    assert not any('RESULT' in w.text() for w in row.findChildren(QLabel))
    assert not row.foldouts
    assert not any('歷史' in b.text() or '返回' in b.text() for b in view.findChildren(QPushButton))
    assert len(store.conversations()[0]['entries']) == 2
    view.close()
    view.deleteLater()
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
    assert row.status.text() == '進行中'
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
