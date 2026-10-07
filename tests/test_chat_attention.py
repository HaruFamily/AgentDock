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
