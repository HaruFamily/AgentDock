import base64
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from agentdock.qa.store import QuestionStore
from agentdock.ui.qa_view import QaView, ClickableImage


def test_native_image_only_and_answer_thumbnails(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(0xff00ff)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, 'PNG')
    encoded = base64.b64encode(bytes(buf.data())).decode()
    store = QuestionStore(tmp_path)
    view = QaView(store)
    q = store.create('q', 'Agent', dict(request_key='q', work_id='w', work_title='Q', question='Image?', mode='text', images=[], options=[]))
    upload = store.add_upload(q['id'], 'answer.png', bytes(buf.data()))
    store.answer(q['id'], dict(attachment_ids=[upload['id']]))
    view.open_conversation('q', 'w')
    answer_row = view._timeline_widgets[q['id'] + '/answer'][0]
    assert len(answer_row.findChildren(ClickableImage, 'ChatImage')) == 1
    view.close()








def test_inbox_cards_use_full_width(tmp_path):
    app = QApplication.instance() or QApplication([])
    view = QaView(QuestionStore(tmp_path))
    for side in ('left', 'right'):
        row, _ = view._chat_bubble(dict(side=side, kind='progress', text='hello', created_at='2026-10-07T12:00:00Z'))
        layout = row.layout()
        assert layout.count() == 1 and layout.spacing() == 0
    view.close()
