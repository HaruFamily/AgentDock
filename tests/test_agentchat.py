import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from agentdock.broker import Broker
from agentdock.qa.store import QuestionStore
from agentdock.ui.qa_view import QaView

OWNER = "a" * 64


def event(key="e", kind="completed", work_id="w", text="Result"):
    return dict(request_key=key, work_id=work_id, work_title="Task", kind=kind, text=text)


def question(store, key="q", owner=OWNER, work_id="w"):
    return store.create(owner, "Codex", dict(request_key=key, work_id=work_id, work_title="Task",
                        question="Choose?", mode="text", images=[], options=[]))


def test_states_isolation_persistence_and_retries(tmp_path):
    store = QuestionStore(tmp_path)
    started = store.receive(OWNER, "Codex", event("start", "completed", text="Original instruction"))
    assert store.conversations()[0]["state"] == "unread"
    q = question(store)
    assert store.conversations()[0]["state"] == "waiting"
    with pytest.raises(ValueError, match="待回答"):
        store.receive(OWNER, "Codex", event())
    store.answer(q["id"], {"text": "Do it"})
    assert store.conversations()[0]["state"] == "running"
    done = store.receive(OWNER, "Codex", event())
    assert store.conversations()[0]["state"] == "unread"
    assert store.receive(OWNER, "Codex", event())["id"] == done["id"]
    with pytest.raises(ValueError, match="request_key"):
        store.receive(OWNER, "Codex", event(text="Different result"))
    other = store.receive("b" * 64, "Codex", event())
    store.receive(OWNER, "Codex", event(work_id="other"))
    assert len(store.conversations()) == 3
    store.mark_read("b" * 64, "w", [done["id"]])
    assert next(w for w in store.conversations() if w["owner"] == OWNER and w["work_id"] == "w")["state"] == "unread"
    store.mark_read(OWNER, "w", [done["id"]])
    again = QuestionStore(tmp_path)
    work = next(w for w in again.conversations() if w["owner"] == OWNER and w["work_id"] == "w")
    assert work["state"] == "" and len(work["entries"]) == 3
    assert any(w["state"] == "unread" for w in again.conversations())
    store.receive(OWNER, "Codex", event("new-start", "completed"))
    assert next(w for w in QuestionStore(tmp_path).conversations() if w["owner"] == OWNER and w["work_id"] == "w")["state"] == "unread"


def test_remove_does_not_answer_or_cancel_and_new_event_recreates(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    store.remove_conversation(OWNER, "w")
    assert not store.conversations() and not store.visible_questions()
    assert store.get(q["id"], OWNER)["status"] == "pending"
    assert "answer" not in store.get(q["id"])
    again = QuestionStore(tmp_path)
    assert not again.conversations()
    again.receive(OWNER, "Codex", event("later", "completed"))
    assert len(again.conversations()) == 1
    assert len(again.conversations()[0]["entries"]) == 1


def test_cancel_does_not_start_and_legacy_questions_appear(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    store.cancel(q["id"])
    assert store.conversations()[0]["state"] == ""
    assert QuestionStore(tmp_path).conversations()[0]["entries"][0]["status"] == "cancelled"


def test_retry_question_restores_removed_region_without_new_question(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    store.remove_conversation(OWNER, "w")
    # Polling does not resurrect a removed conversation.
    store.touch(q["id"])
    assert not store.conversations()
    assert question(store)["id"] == q["id"]
    assert len(store.list()) == 1
    assert store.conversations()[0]["state"] == "waiting"


def test_open_conversation_starts_at_bottom_and_reads_visible_completion(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive(OWNER, "Codex", event("start", "completed", text="Long task\n" * 100))
    store.receive(OWNER, "Codex", event())
    view = QaView(store)
    view.resize(450, 300)
    view.show()
    view.open_conversation(OWNER, "w")
    app.processEvents()
    scroll = view.timeline_scroll.verticalScrollBar()
    assert scroll.value() == scroll.maximum()
    app.processEvents()
    store.flush_reads()
    assert store.conversations()[0]["state"] == ""
    view.close()


def test_timeline_resize_keeps_wrap_width_stable_and_settles(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive(OWNER, 'Codex', event(text='A wrapped message with several words. ' * 12))
    view = QaView(store)
    view.resize(460, 900)
    view.show()
    view.open_conversation(OWNER, 'w')
    for _ in range(10): app.processEvents()
    width = view.timeline_scroll.viewport().width()
    for height in (300, 900, 280, 800, 320):
        view.resize(460, height)
        for _ in range(10): app.processEvents()
        assert view.timeline_scroll.viewport().width() == width
        geometry = view.timeline_scroll.widget().geometry()
        for _ in range(10): app.processEvents()
        assert view.timeline_scroll.widget().geometry() == geometry
    view.close()




def test_broker_receive_validation_and_no_answer_endpoint(tmp_path):
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    headers = {"authorization": f"Bearer {broker.token}", "host": "127.0.0.1:1234", "x-agentdock-owner": OWNER}
    raw = json.dumps({"source": "Codex", "input": event()}).encode()
    assert broker.handle("POST", "/chat/events", {**headers, "origin": "https://example.com"}, raw)[0] == 403
    assert broker.handle("POST", "/chat/events", headers, raw)[0] == 200
    assert broker.handle("POST", "/chat/events", headers, raw)[0] == 200
    assert len(store.conversations()[0]["entries"]) == 1
    invalid = json.dumps({"source": "Codex", "input": event(kind="answer")}).encode()
    assert broker.handle("POST", "/chat/events", headers, invalid)[0] == 400
    blank = json.dumps({"source": "Codex", "input": event(text=" ")}).encode()
    assert broker.handle("POST", "/chat/events", headers, blank)[0] == 400
    assert broker.handle("POST", "/chat/answer", headers, raw)[0] == 404


def test_conversation_ui_read_and_focus(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    store.receive(OWNER, "Codex", event(text="<b>literal result</b>"))
    view = QaView(store)
    view.resize(550, 600)
    view.open_conversation(OWNER, "w")
    app.processEvents()
    assert store.conversations()[0]["state"] == "unread"  # hidden page is not read
    view.show()
    view.activateWindow()
    app.processEvents()
    app.processEvents()
    store.flush_reads()
    assert store.conversations()[0]["state"] == ""
    labels = view.timeline.findChildren(QLabel)
    from PySide6.QtGui import QTextDocument
    result = next(w for w in labels if 'literal result' in w.text())
    rendered = QTextDocument()
    rendered.setHtml(result.text())
    assert rendered.toPlainText() == "<b>literal result</b>"
    assert result.textFormat() == Qt.TextFormat.RichText
    q = question(store)
    view.open_question(q["id"])
    editor = view.blocks[q["id"]]["text"]
    editor.setPlainText("Still typing")
    editor.setFocus()
    store.receive(OWNER, "Codex", event("update", "completed", work_id="other-work", text="Background report"))
    question(store, "q2")
    view.refresh()
    view.show_first_pending()
    assert view.active == q["id"] and view.blocks[q["id"]]["text"] is editor
    assert editor.toPlainText() == "Still typing"
    view.open_conversation(OWNER, "w")
    assert store.get(q["id"])["draft"]["text"] == "Still typing"
    assert any(b.text() == "回答問題" for b in view.timeline.findChildren(QPushButton))
    view.close()


def test_report_tool_real_stdio(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    broker.start()
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "AGENTDOCK_DATA_DIR": str(tmp_path), "AGENTDOCK_NO_LAUNCH": "1",
           "AGENTDOCK_SOURCE": "Reporter", "AGENTDOCK_CLIENT_ID": "report-test", "PYTHONPATH": str(root)}

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentdock.mcp_server"], env=env, cwd=str(root))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                assert "report_to_user" in {t.name for t in (await session.list_tools()).tools}
                first = await session.call_tool("report_to_user", event("start", "completed", text="Actual task"))
                assert not first.isError
                result = await session.call_tool("report_to_user", event())
                retry = await session.call_tool("report_to_user", event())
                assert not result.isError and result.content[0].text == retry.content[0].text
                assert json.loads(result.content[0].text)["status"] == "received"
    try:
        asyncio.run(run())
        assert store.conversations()[0]["state"] == "unread"
        assert store.conversations()[0]["source"] == "Reporter"
        assert len(store.conversations()[0]["entries"]) == 2
    finally:
        broker.close()


def test_agent_groups_and_chat_bubble_sides(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    store.answer(q["id"], {"text": "請保留目前的選取"})
    store.receive(OWNER, "OpenCode", event())
    store.receive(OWNER, "OpenCode", event(work_id="other"))
    store.receive("b" * 64, "Codex", event())
    view = QaView(store)
    view.resize(500, 700)
    view.show()
    app.processEvents()
    headings = [w.text() for w in view.list_body.findChildren(QLabel) if w.objectName() == "AgentHeading"]
    assert headings.count("OpenCode") == 1 and headings.count("Codex") == 1
    view.open_conversation(OWNER, "w")
    app.processEvents()
    rows = [w for w in view.timeline.findChildren(QWidget) if w.objectName() == "ChatMessage"]
    assert [w.property("messageKind") for w in rows] == ["question", "answer", "completed"]
    for row in rows:
        bubble = row.findChild(QWidget, "ChatBubble")
        assert bubble.x() == 0 and bubble.width() == row.width()
    next_q = question(store, "next")
    view.refresh()
    view.open_question(next_q["id"])
    view.blocks[next_q["id"]]["text"].setPlainText("下一個回答")
    view._submit(store.group_members(next_q["id"]))
    assert view.stack.currentIndex() == 2
    assert view.conversation == (OWNER, "w")
    assert any("下一個回答" in w.text() for w in view.timeline.findChildren(QLabel))
    view.open_question(next_q["id"])
    assert view.blocks[next_q["id"]]["text"].toPlainText() == "下一個回答"
    view.close()
