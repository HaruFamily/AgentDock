import json
import sqlite3
from pathlib import Path

from agentdock.chat_sync import ChatSync, Source, codex_record, claude_record, iso, question_refs
from agentdock.qa.schema import AskInput
from agentdock.qa.store import QuestionStore


def write_lines(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        for row in rows:
            f.write(json.dumps(row).encode() + b"\n")


def codex(typ, timestamp="2026-10-07T12:00:00Z", **payload):
    return dict(type="event_msg", timestamp=timestamp, payload=dict(type=typ, **payload))


def item(uid, kind, text, **more):
    return codex("item_completed", item=dict(type=kind, id=uid, content=[dict(type="Text", text=text)], **more))


def setup_codex(tmp_path):
    root = tmp_path / "codex"
    transcript = root / "sessions" / "today.jsonl"
    write_lines(transcript, dict(type="session_meta", payload=dict(id="session-1", source="cli")))
    with sqlite3.connect(root / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id,title,name,rollout_path,history_mode,archived)")
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?,0)",
                   ("session-1", "first prompt", "Real task title", str(transcript), "paginated"))
    db.close()
    store = QuestionStore(tmp_path / "dock")
    sync = ChatSync(store, tmp_path / "dock", lambda: [Source("codex", root, "Codex", "owner")])
    sync.live_since = "2026-10-07T11:00:00.000Z"
    sync.notify_since = sync.live_since
    return store, sync, transcript


def test_codex_native_messages_lifecycle_restart_and_title(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    signals = []
    store.subscribe(lambda kind, value: signals.append((kind, value)))
    write_lines(path, codex("task_started", turn_id="turn"), item("u1", "UserMessage", "original input"),
                item("a1", "AgentMessage", "exact reply", phase="final_answer"),
                dict(type="response_item", timestamp="2026-10-07T12:00:00Z",
                     payload=dict(type="message", role="assistant", content=[dict(type="output_text", text="duplicate")])) )
    sync.scan()
    conv = store.conversations()[0]
    assert conv["state"] == "running"  # A final-phase message alone is not a completed turn.
    assert conv["work_title"] == "Real task title"
    assert [e["text"] for e in conv["entries"] if e["text"]] == ["original input", "exact reply"]
    write_lines(path, codex("task_complete", "2026-10-07T12:00:01Z", turn_id="turn", last_agent_message="exact reply"))
    sync.scan()
    conv = store.conversations()[0]
    assert conv["state"] == "unread"
    assert len([e for e in conv["entries"] if e["text"] == "exact reply"]) == 1
    assert len([s for s, _ in signals if s == "new-message"]) == 1
    sync.scan()
    assert len([s for s, _ in signals if s == "new-message"]) == 1
    # Crash between the chat commit and checkpoint commit: replay must not notify again.
    sync.state["files"][str(path)]["offset"] = 0
    sync.scan()
    assert len([s for s, _ in signals if s == "new-message"]) == 1
    store.mark_read("owner", "session-1", [e["id"] for e in conv["entries"]])
    restarted = ChatSync(store, tmp_path / "dock", sync.get_sources)
    restarted.scan()
    assert store.conversations()[0]["state"] == ""
    with sqlite3.connect(path.parents[1] / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET name='Renamed'")
    restarted.scan()
    assert store.conversations()[0]["work_title"] == "Renamed"


def test_partial_lines_removal_and_replay(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    line = json.dumps(item("u1", "UserMessage", "hello")).encode()
    with path.open("ab") as f:
        f.write(line[:25])
    sync.scan()
    assert store.conversations() == []
    with path.open("ab") as f:
        f.write(line[25:] + b"\n")
    sync.scan()
    assert len(store.conversations()) == 1
    store.remove_conversation("owner", "session-1")
    sync.state["files"][str(path)]["offset"] = 0  # crash/replay does not resurrect removed messages
    sync.scan()
    assert store.conversations() == []
    write_lines(path, item("a1", "AgentMessage", "new reply"))
    sync.scan()
    assert [e["text"] for e in store.conversations()[0]["entries"]] == ["new reply"]


def test_removed_stream_completion_returns_once(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    write_lines(path, item("a1", "AgentMessage", "reply"))
    sync.scan()
    store.remove_conversation("owner", "session-1")
    write_lines(path, codex("task_complete", turn_id="turn"))
    sync.scan()
    assert store.conversations()[0]["state"] == "unread"
    store.remove_conversation("owner", "session-1")
    sync.state["files"][str(path)]["offset"] = 0
    sync.scan()
    assert store.conversations() == []


def test_replaced_transcript_replays_new_native_ids(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    write_lines(path, item("u1", "UserMessage", "one"))
    sync.scan()
    replacement = path.with_suffix(".replacement")
    write_lines(replacement, item("u2", "UserMessage", "two"))
    replacement.replace(path)
    sync.scan()
    assert {e["text"] for e in store.conversations()[0]["entries"]} == {"one", "two"}


def test_native_identity_stable_when_profile_added(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agentdock.chat_sync import sources
    root = tmp_path / ".codex"
    root.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(root))
    before = next(s for s in sources(home=tmp_path) if s.kind == "codex")
    profile = SimpleNamespace(kind="codex", path=str(root / "config.toml"), name="My Codex", id="profile")
    after = next(s for s in sources([profile], home=tmp_path) if s.kind == "codex")
    assert before.owner == after.owner
    assert after.name == "My Codex" and after.mcp_owner


def test_claude_global_config_resolves_transcripts_and_deduplicates(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agentdock.chat_sync import sources
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = tmp_path / ".claude"
    (root / "projects").mkdir(parents=True)
    profile = SimpleNamespace(kind="claude-code", path=str(tmp_path / ".claude.json"), name="Claude Code", id="profile")
    found = [s for s in sources([profile], home=tmp_path) if s.kind == "claude-code"]
    assert len(found) == 1 and found[0].root == root and found[0].mcp_owner
    custom = tmp_path / "custom-claude"
    custom.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    found = [s for s in sources([profile], home=tmp_path) if s.kind == "claude-code"]
    assert len(found) == 1 and found[0].root == custom


def test_ui_updates_stream_and_read_status(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from agentdock.ui.qa_view import QaView
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    entry = dict(key="a", kind="progress", text="partial", created_at="2026-10-07T12:00:00.000Z")
    store.import_chat("o", "Codex", "s", "Title", [entry])
    view = QaView(store)
    view.resize(600, 400)
    view.show()
    view.open_conversation("o", "s")
    app.processEvents()
    store.import_chat("o", "Codex", "s", "New title", [{**entry, "kind": "completed", "text": "final reply"}])
    view.refresh()
    app.processEvents()
    store.flush_reads()
    from PySide6.QtGui import QTextDocument
    labels = []
    for widget in view.timeline.findChildren(QLabel):
        rendered = QTextDocument()
        rendered.setHtml(widget.text())
        labels.append(rendered.toPlainText())
    assert "final reply" in labels and "partial" not in labels
    assert "Codex · New title" in labels
    assert store.conversations()[0]["state"] == ""
    view.close()


def test_history_silent_and_failed_import_retries(tmp_path, monkeypatch):
    store, sync, path = setup_codex(tmp_path)
    sync.live_since = "2026-10-08T00:00:00.000Z"
    write_lines(path, item("a1", "AgentMessage", "old reply"), codex("task_complete", turn_id="t"))
    original = store.import_chat
    def fail(*a, **kw):
        raise OSError("disk unavailable")
    monkeypatch.setattr(store, "import_chat", fail)
    sync.scan()
    assert str(path) not in sync.state["files"]
    monkeypatch.setattr(store, "import_chat", original)
    signals = []
    store.subscribe(lambda k, v: signals.append(k))
    sync.scan()
    assert store.conversations()[0]["state"] == ""
    assert "new-message" not in signals


def test_actual_result_id_links_questions_without_rewriting_protocol(tmp_path):
    store = QuestionStore(tmp_path)
    q = store.create("mcp-owner", "Codex", AskInput(request_key="q", work_id="invented", work_title="wrong",
                                                    question="Choose?", mode="text").model_dump())
    store.receive("mcp-owner", "Codex", dict(request_key="r", work_id="invented", work_title="wrong",
                                            kind="progress", text="manually copied"))
    store.import_chat("native", "Codex", "real-id", "Correct title", [dict(key="u", kind="user_message",
                      text="actual prompt", created_at="2026-10-07T12:00:00.000Z")], [q["id"]], "2026-10-07T11:00:00.000Z")
    work = store.conversations()
    assert len(work) == 1 and work[0]["state"] == "waiting"
    assert work[0]["work_title"] == "Correct title"
    assert not any(e.get("text") == "manually copied" for e in work[0]["entries"])
    store.answer(q["id"], dict(text="answer"))
    assert store.get(q["id"], "mcp-owner")["answer"]["text"] == "answer"
    assert store.conversations()[0]["state"] == "running"
    store.remove_conversation("native", "real-id")
    assert store.conversations() == []
    assert store.get(q["id"], "mcp-owner")["status"] == "answered"


def test_claude_filters_internal_content_and_stop_reason():
    state = {}
    base = dict(sessionId="session", timestamp="2026-10-07T12:00:00Z")
    events, _ = claude_record(dict(base, type="user", uuid="u", message=dict(content="hello")), state)
    assert events[0]["kind"] == "user_message"
    events, links = claude_record(dict(base, type="user", uuid="tool", message=dict(content=[
        dict(type="tool_result", content='{"request_id":"question-id"}') ])), state)
    assert not events and links == ["question-id"]
    events, _ = claude_record(dict(base, type="assistant", uuid="a", message=dict(stop_reason="end_turn", content=[
        dict(type="thinking", thinking="private"), dict(type="text", text="visible")])), state)
    assert events[0]["kind"] == "completed" and events[0]["text"] == "visible"
    events, _ = claude_record(dict(base, type="user", isMeta=True, uuid="internal", message=dict(content="injected")), state)
    assert not events
    claude_record(dict(type="ai-title", sessionId="session", aiTitle="Native title"), state)
    assert state["title"] == "Native title"
    claude_record(dict(type="custom-title", sessionId="session", customTitle="User title"), state)
    claude_record(dict(type="ai-title", sessionId="session", aiTitle="AI title"), state)
    assert state["title"] == "User title"


def test_opencode_streaming_readonly_and_completion(tmp_path):
    root = tmp_path / "opencode"
    root.mkdir()
    path = root / "opencode.db"
    timestamp = 1791374400000
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE session(id,title,time_updated,parent_id,time_archived);
        CREATE TABLE message(id,session_id,time_created,time_updated,data);
        CREATE TABLE part(id,message_id,session_id,time_created,time_updated,data);''')
        db.execute("INSERT INTO session VALUES('s','Prefab',?,NULL,NULL)", (timestamp,))
        for uid, role, text in [("u", "user", "request"), ("a", "assistant", "partial")]:
            db.execute("INSERT INTO message VALUES(?,?,?,?,?)", (uid, "s", timestamp, timestamp, json.dumps(dict(role=role))))
            db.execute("INSERT INTO part VALUES(?,?,?,?,?,?)", (uid, uid, "s", timestamp, timestamp,
                                                                 json.dumps(dict(type="text", text=text))))
    store = QuestionStore(tmp_path / "dock")
    sync = ChatSync(store, tmp_path / "dock", lambda: [Source("opencode", root, "OpenCode", "owner")])
    sync.live_since = iso(timestamp - 1000)
    sync.notify_since = sync.live_since
    sync.scan()
    assert store.conversations()[0]["state"] == "running"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE part SET data=?,time_updated=? WHERE id='a'", (json.dumps(dict(type="text", text="full reply")), timestamp+1))
        db.execute("UPDATE message SET data=? WHERE id='a'", (json.dumps(dict(role="assistant", finish="stop", time=dict(completed=timestamp+1))),))
    sync.scan()
    work = store.conversations()[0]
    assert work["state"] == "unread"
    assert len(work["entries"]) == 2
    assert next(e for e in work["entries"] if e["kind"] == "completed")["text"] == "full reply"


def test_pending_question_call_links_before_tool_returns(tmp_path):
    refs = question_refs("exec", 'text(await tools.mcp__agentchat__ask_user({request_key:"q", work_id:"w", question:"test?"}));')
    assert refs == [dict(request_key="q", work_id="w")]
    assert not question_refs("exec", 'text("tools.mcp__agentchat__ask_user({request_key:\\"q\\",work_id:\\"w\\"})");')
    assert not question_refs("exec", 'tools.mcp__agentchat__ask_user({request_key: variable, work_id:"w"})')
    store = QuestionStore(tmp_path)
    store.import_chat("owner", "Codex", "real", "Title", [], refs)
    q = store.create("owner", "Codex", AskInput(request_key="q", work_id="w", work_title="Wrong", question="test?").model_dump())
    work = store.conversations()[0]
    assert work["work_id"] == "real" and work["state"] == "waiting"
    assert store.get(q["id"], "owner")["work_id"] == "w"
    # No cross-owner link, even with identical tool arguments.
    store.create("other", "Codex", AskInput(request_key="q", work_id="w", work_title="Other", question="test?").model_dump())
    assert len(store.conversations()) == 2


def test_unknown_schema_isolated_from_other_sources(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    with sqlite3.connect(root / "opencode.db") as db:
        db.execute("CREATE TABLE unsupported(x)")
    store, sync, path = setup_codex(tmp_path)
    write_lines(path, item("user", "UserMessage", "still works"))
    valid = sync.get_sources()[0]
    sync.get_sources = lambda: [Source("opencode", root, "OpenCode", "bad"), valid]
    sync.scan()
    assert str(root) in sync.errors
    assert store.conversations()[0]["entries"][0]["text"] == "still works"


def test_completion_updated_without_part_change(tmp_path):
    root = tmp_path / "opencode"
    root.mkdir()
    path = root / "opencode.db"
    now = 1791374400000
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE session(id,title,time_updated,parent_id,time_archived);
        CREATE TABLE message(id,session_id,time_created,time_updated,data);
        CREATE TABLE part(id,message_id,session_id,time_created,time_updated,data);''')
        db.execute("INSERT INTO session VALUES('s','Task',?,NULL,NULL)", (now,))
        db.execute("INSERT INTO message VALUES('a','s',?,?,?)", (now, now, json.dumps(dict(role="assistant"))))
        db.execute("INSERT INTO part VALUES('p','a','s',?,?,?)", (now, now, json.dumps(dict(type="text", text="reply"))))
    store = QuestionStore(tmp_path / "dock")
    sync = ChatSync(store, tmp_path / "dock", lambda: [Source("opencode", root, "OpenCode", "owner")])
    sync.live_since = sync.notify_since = iso(now - 1000)
    sync.scan()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE message SET time_updated=?,data=?", (now+1, json.dumps(dict(role="assistant", finish="stop", time=dict(completed=now+1)))))
    sync.scan()
    assert store.conversations()[0]["state"] == "unread"
