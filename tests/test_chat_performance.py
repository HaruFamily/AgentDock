"""Bounded work assertions, rather than machine-dependent timing thresholds."""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget

from agentdock.files import atomic_json
from agentdock.qa.store import QuestionStore
from agentdock.ui.qa_view import QaView


def incoming(store, owner, source, session, title, entries):
    for e in entries:
        store.receive(owner, source, dict(request_key=e['key'], work_id=session, work_title=title,
                                          kind='completed', text=e['text']))


def populated(tmp_path, tasks=40, messages=200, text="History " * 200):
    rows = [dict(id=f"{w}:{i}", owner="owner", work_id=str(w), work_title=f"Task {w}", source="Codex",
                 request_key=f"{w}:{i}", kind="completed", text=text,
                 created_at=f"2000-10-07T12:{i // 60:02}:{i % 60:02}.000Z", read=True, external=False)
            for w in range(tasks) for i in range(messages)]
    atomic_json(tmp_path / "chat.json", dict(events=rows, hidden_questions=[]))
    return QuestionStore(tmp_path)


def test_large_history_ui_uses_only_summary_and_one_page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = populated(tmp_path, tasks=80)
    def full_export_forbidden():
        raise AssertionError("UI must not copy every conversation's contents")
    monkeypatch.setattr(store, "conversations", full_export_forbidden)
    assert all("entries" not in s for s in store.conversation_summaries())
    view = QaView(store)
    assert len(view.list_body.findChildren(QWidget, "Row")) == 60
    view.resize(600, 600)
    view.show()
    view.open_conversation("owner", "0")
    app.processEvents()
    assert len(view._timeline_widgets) == view.PAGE_SIZE
    assert "0:199" in view._timeline_widgets and "0:0" not in view._timeline_widgets
    first_rows = {key: value[0] for key, value in view._timeline_widgets.items()}
    scroll = view.timeline_scroll
    view.refresh()
    app.processEvents()
    assert view.timeline_scroll is scroll
    assert {key: value[0] for key, value in view._timeline_widgets.items()} == first_rows
    view.close()


def test_history_pages_remain_anchored_while_new_messages_arrive(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = populated(tmp_path, tasks=1, messages=100, text="short")
    view = QaView(store)
    view.open_conversation("owner", "0")
    view._history_page("older")
    page = set(view._timeline_widgets)
    assert page == {f"0:{i}" for i in range(40, 70)}
    incoming(store, "owner", "Codex", "0", "Task 0", [
        dict(key="new", kind="completed", text="new", created_at="2026-10-07T13:00:00.000Z")])
    view.refresh()
    assert set(view._timeline_widgets) == page
    seen = set(page)
    while view._older.isEnabled():
        view._history_page("older")
        seen.update(view._timeline_widgets)
        assert len(view._timeline_widgets) <= view.PAGE_SIZE
    assert {f"0:{i}" for i in range(70)} <= seen
    view._history_page("latest")
    assert any(m[2]["text"] == "new" for m in view._timeline_widgets.values())
    app.processEvents()
    view.close()




def test_already_read_scrolling_does_not_save_or_copy_archive(tmp_path, monkeypatch):
    store = populated(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Read receipts for already-read messages must do no writes/copies")
    monkeypatch.setattr("agentdock.qa.store.atomic_json", forbidden)
    monkeypatch.setattr("agentdock.qa.store.copy.deepcopy", forbidden)
    assert not store.mark_read("owner", "0", ["0:199"])
    assert not store.mark_read("owner", "0", [])


def test_ui_snapshot_does_not_wait_for_background_disk_commit(tmp_path, monkeypatch):
    store = populated(tmp_path, tasks=2, messages=40, text="short")
    before = store.conversation_page("owner", "0")
    committing, release, read_done = threading.Event(), threading.Event(), threading.Event()
    original = atomic_json
    def slow_save(path, data):
        committing.set()
        assert release.wait(5)
        original(path, data)
    monkeypatch.setattr("agentdock.qa.store.atomic_json", slow_save)
    writer = threading.Thread(target=lambda: incoming(store, "owner", "Codex", "0", "Task 0", [
        dict(key="new", kind="completed", text="new reply", created_at="2026-10-07T13:00:00.000Z")]))
    writer.start()
    assert committing.wait(2)
    values = []
    def read():
        values.extend([store.conversation_summaries(), store.conversation_page("owner", "0"), store.visible_questions()])
        read_done.set()
    reader = threading.Thread(target=read)
    reader.start()
    try:
        assert read_done.wait(1), "UI was blocked by background disk I/O"
        assert values[1] == before
    finally:
        release.set()
        writer.join(5)
        reader.join(5)
    assert any(e["text"] == "new reply" for e in store.conversation_page("owner", "0")["entries"])


def test_open_at_bottom_follow_new_text_but_preserve_manual_scroll(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = populated(tmp_path, tasks=1, messages=35, text="line\n" * 20)
    view = QaView(store)
    view.resize(500, 400)
    view.show()
    view.open_conversation("owner", "0")
    app.processEvents()
    bar = view.timeline_scroll.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    incoming(store, "owner", "Codex", "0", "Task 0", [dict(
        key="latest", kind="completed", text="new\n" * 20, created_at="2026-10-07T13:00:00.000Z")])
    view.refresh()
    app.processEvents()
    assert bar.value() == bar.maximum()
    bar.setValue(bar.maximum() // 3)
    position = bar.value()
    incoming(store, "owner", "Codex", "0", "Task 0", [dict(
        key="latest-2", kind="completed", text="streamed\n" * 35, created_at="2026-10-07T13:00:00.000Z")])
    view.refresh()
    app.processEvents()
    assert bar.value() == position
    view._history_page("older")
    app.processEvents()
    assert bar.value() == 0
    view._history_page("latest")
    app.processEvents()
    assert bar.value() == bar.maximum()
    view.close()
