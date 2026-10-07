import json
import os
import sqlite3
import time

from agentdock.chat_sync import ChatSync, Source, iso
from agentdock.qa.store import QuestionStore
from test_chat_sync import setup_codex, write_lines, item
from test_agentchat import event, question


def test_startup_hides_history_but_preserves_pending_and_new_reports(tmp_path):
    old = QuestionStore(tmp_path)
    old.receive('o', 'Codex', event())
    q = question(old, work_id='pending')
    store = QuestionStore(tmp_path)
    store.begin_chat_session()
    assert [c['work_id'] for c in store.conversations()] == ['pending']
    store.set_source_sessions(q['owner'], set())
    assert store.visible_questions()[0]['id'] == q['id']
    store.receive('o', 'Codex', event('new'))
    assert len(store.conversations()) == 2
    assert len(store.conversation_page('o', 'w')['entries']) == 2
    store.answer(q['id'], {'text': 'yes'})
    assert store.get(q['id'])['status'] == 'answered'
    restarted = QuestionStore(tmp_path)
    restarted.begin_chat_session()
    assert not restarted.conversations()
    assert len(restarted._chat['events']) == 2


def test_codex_startup_skips_old_records_and_accepts_live_append(tmp_path):
    store, _, path = setup_codex(tmp_path)
    write_lines(path, item('old', 'UserMessage', 'old input'))
    os.utime(path, (time.time() - 10, time.time() - 10))
    store.begin_chat_session()
    sync = ChatSync(store, store.directory, lambda: [Source('codex', path.parents[1], 'Codex', 'o')])
    sync.scan()
    assert not store.conversations()
    row = item('new', 'AgentMessage', 'live reply')
    row['timestamp'] = iso(time.time() + 1)
    write_lines(path, row)
    sync.scan()
    assert [e['text'] for e in store.conversations()[0]['entries']] == ['live reply']
    restarted = QuestionStore(store.directory)
    restarted.begin_chat_session()
    again = ChatSync(restarted, store.directory, sync.get_sources)
    again.scan()
    assert not restarted.conversations()


def test_opencode_old_message_completing_during_run_is_received(tmp_path):
    root = tmp_path / 'opencode'
    root.mkdir()
    db = sqlite3.connect(root / 'opencode.db')
    db.executescript('''CREATE TABLE session(id,title,time_updated,parent_id,time_archived);
    CREATE TABLE message(id,session_id,time_created,time_updated,data);
    CREATE TABLE part(id,message_id,session_id,time_created,time_updated,data);''')
    db.execute("INSERT INTO session VALUES('s','Task',1,NULL,NULL)")
    db.execute("INSERT INTO message VALUES('a','s',1,1,?)", (json.dumps({'role': 'assistant'}),))
    db.execute("INSERT INTO part VALUES('p','a','s',1,1,?)", (json.dumps({'type': 'text', 'text': 'stream'}),))
    db.commit()
    store = QuestionStore(tmp_path / 'dock')
    store.begin_chat_session()
    sync = ChatSync(store, store.directory, lambda: [Source('opencode', root, 'OpenCode', 'o')])
    sync.scan()
    assert not store.conversations()
    now = int((time.time() + 1) * 1000)
    db.execute('UPDATE message SET time_updated=?,data=?', (now, json.dumps({'role': 'assistant', 'finish': 'stop', 'time': {'completed': now}})))
    db.commit()
    sync.scan()
    assert store.conversations()[0]['state'] == 'unread'
    assert store.conversations()[0]['entries'][0]['text'] == 'stream'
    db.close()


def test_claude_first_scan_filters_old_messages_even_in_recently_touched_file(tmp_path):
    root = tmp_path / 'claude'
    path = root / 'projects' / 'p' / 'session.jsonl'
    store = QuestionStore(tmp_path / 'dock')
    store.begin_chat_session()
    write_lines(path, dict(type='user', uuid='old', sessionId='s', timestamp='2020-01-01T00:00:00Z', message={'content': 'old'}),
                dict(type='assistant', uuid='new', sessionId='s', timestamp=iso(time.time() + 1), message={'content': 'new', 'stop_reason': 'end_turn'}))
    sync = ChatSync(store, store.directory, lambda: [Source('claude-code', root, 'Claude Code', 'o')])
    sync.scan()
    assert [e['text'] for e in store.conversations()[0]['entries']] == ['new']
