import json
import sqlite3

from agentdock.chat_sync import ChatSync, Source
from agentdock.qa.store import QuestionStore
from test_chat_sync import setup_codex, write_lines, item


def test_archived_and_deleted_codex_do_not_reappear_from_leftover_files(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    write_lines(path, item("u1", "UserMessage", "hello"))
    sync.scan()
    assert len(store.conversations()) == 1
    db_path = path.parents[1] / "state_5.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE threads SET archived=1")
    sync.scan()
    assert not store.conversations()
    assert path.exists()  # The leftover file must not be used as a fallback.
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE threads SET archived=0")
    sync.scan()
    assert len(store.conversations()) == 1
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM threads")
    sync.scan()
    assert not store.conversations()
    # Hiding source-deleted chats does not erase the saved history.
    assert store._chat["events"]


def test_unavailable_index_does_not_hide_or_resurrect_chats(tmp_path):
    store, sync, path = setup_codex(tmp_path)
    write_lines(path, item("u1", "UserMessage", "hello"))
    sync.scan()
    db_path = path.parents[1] / "state_5.sqlite"
    db_path.rename(db_path.with_suffix(".unavailable"))
    sync.scan()
    assert sync.errors and len(store.conversations()) == 1


def test_claude_deleted_file_is_hidden_but_missing_root_is_not_deletion(tmp_path):
    root = tmp_path / "claude"
    path = root / "projects" / "project" / "session.jsonl"
    write_lines(path, dict(type="user", uuid="u", sessionId="s", timestamp="2026-10-07T12:00:00Z",
                          message=dict(content="hi")))
    store = QuestionStore(tmp_path / "dock")
    sync = ChatSync(store, tmp_path / "dock", lambda: [Source("claude-code", root, "Claude Code", "o")])
    sync.scan()
    assert len(store.conversations()) == 1
    (root / "projects").rename(root / "unavailable")
    sync.scan()
    assert sync.errors and len(store.conversations()) == 1
    (root / "unavailable").rename(root / "projects")
    path.unlink()
    sync.scan()
    assert not store.conversations()


def test_source_removal_hides_question_without_cancelling_or_answering(tmp_path):
    store = QuestionStore(tmp_path)
    q = store.create("mcp", "Codex", dict(request_key="q", work_id="w", work_title="Task",
                                         question="Choose?", mode="text", images=[], options=[]))
    store.import_chat("native", "Codex", "s", "Task", [], [q["id"]])
    store.set_source_sessions("native", set())
    assert not store.conversations() and not store.visible_questions()
    assert store.get(q["id"], "mcp")["status"] == "pending"
    assert "answer" not in store.get(q["id"], "mcp")
    store.set_source_sessions("native", {"s"})
    assert store.conversations()[0]["state"] == "waiting"


def test_opencode_archived_and_deleted_sessions_are_hidden(tmp_path):
    root = tmp_path / "opencode"
    root.mkdir()
    path = root / "opencode.db"
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE session(id,title,time_updated,parent_id,time_archived);
        CREATE TABLE message(id,session_id,time_created,time_updated,data);
        CREATE TABLE part(id,message_id,session_id,time_created,time_updated,data);''')
        db.execute("INSERT INTO session VALUES('s','Task',1,NULL,NULL)")
        db.execute("INSERT INTO message VALUES('u','s',1,1,?)", (json.dumps(dict(role="user")),))
        db.execute("INSERT INTO part VALUES('p','u','s',1,1,?)", (json.dumps(dict(type="text", text="hello")),))
    store = QuestionStore(tmp_path / "dock")
    sync = ChatSync(store, tmp_path / "dock", lambda: [Source("opencode", root, "OpenCode", "o")])
    sync.scan()
    assert len(store.conversations()) == 1
    with sqlite3.connect(path) as db:
        db.execute("UPDATE session SET time_archived=2")
    sync.scan()
    assert not store.conversations()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE session SET time_archived=NULL")
    sync.scan()
    assert len(store.conversations()) == 1
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM session")
    sync.scan()
    assert not store.conversations()
