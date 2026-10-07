from types import SimpleNamespace
from unittest.mock import Mock

from agentdock.qa.store import QuestionStore
from agentdock.ui.app import Dock
from test_agentchat import event, question


def test_attention_tracks_pending_and_unread_without_toasts(tmp_path):
    store = QuestionStore(tmp_path)
    dock = SimpleNamespace(store=store, ball=Mock(), icon=Mock(), tray=Mock(), qa=Mock(),
                           ui={'question_toast': True, 'auto_expand': True})
    done = store.receive('o', 'Agent', event())
    q = question(store, work_id='question')
    Dock._pending(dock, 1)
    dock.icon.set_attention.assert_called_with(True)
    dock.ball.set_attention.assert_called_with(True)
    store.mark_read('o', 'w', [done['id']])
    Dock._pending(dock, 1)
    dock.icon.set_attention.assert_called_with(True)
    store.cancel(q['id'])
    Dock._pending(dock, 0)
    dock.icon.set_attention.assert_called_with(False)
    dock.ball.set_attention.assert_called_with(False)
    Dock._on_new_message(dock, done)
    Dock._on_new_question(dock, q)
    dock.tray.showMessage.assert_not_called()
    dock.ball.show.assert_not_called()
    dock.ball.question_arrived.assert_not_called()


def test_both_launcher_windows_render_unread_dot():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QColor
    from agentdock.ui.card import FloatingCard
    from agentdock.ui.app import LauncherIcon
    app = QApplication.instance() or QApplication([])
    for cls in (FloatingCard, LauncherIcon):
        view = cls()
        view.set_mode('heart')
        view.set_attention(True)
        image = view.grab().toImage()
        x, y = view.width() // 2 + 14, view.height() // 2 - 12
        assert image.pixelColor(x, y) == QColor('#F04452')
        view.set_attention(False)
        assert view.grab().toImage().pixelColor(x, y) != QColor('#F04452')
        view.close()


def test_passive_visible_chat_stays_unread_until_active(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from agentdock.ui.qa_view import QaView
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive('o', 'Agent', event())
    view = QaView(store)
    monkeypatch.setattr(view, 'isActiveWindow', lambda: False)
    view.show()
    view.open_conversation('o', 'w')
    app.processEvents()
    store.flush_reads()
    assert store.conversations()[0]['state'] == 'unread'
    monkeypatch.setattr(view, 'isActiveWindow', lambda: True)
    view._mark_visible_read()
    store.flush_reads()
    assert store.conversations()[0]['state'] == ''
    view.close()


def test_codex_commentary_not_in_pages_but_completion_visible(tmp_path):
    from agentdock.chat_sync import codex_record
    from test_chat_sync import item, codex
    state = {}
    entries, _ = codex_record(item('a', 'AgentMessage', 'working', phase='commentary'), state)
    store = QuestionStore(tmp_path)
    store.import_chat('o', 'Codex', 's', 'Task', entries)
    assert store.conversation_page('o', 's')['entries'] == []
    entries, _ = codex_record(item('b', 'AgentMessage', 'done', phase='final_answer'), state)
    store.import_chat('o', 'Codex', 's', 'Task', entries)
    entries, _ = codex_record(codex('task_complete'), state)
    store.import_chat('o', 'Codex', 's', 'Task', entries)
    assert [e['text'] for e in store.conversation_page('o', 's')['entries']] == ['done']


def test_completion_time_controls_unread_after_later_activity(tmp_path):
    store = QuestionStore(tmp_path)
    store.import_chat('o', 'Agent', 's', 'Task', [
        dict(key='u',kind='user_message',text='continue',created_at='2026-10-07T12:00:05Z'),
        dict(key='a',kind='completed',text='done',created_at='2026-10-07T12:00:00Z',
             finished_at='2026-10-07T12:00:10Z')], live_since='2026-10-07T12:00:01Z')
    assert store.conversations()[0]['state'] == 'unread'
    assert store.conversations()[0]['updated_at'] == '2026-10-07T12:00:10Z'
