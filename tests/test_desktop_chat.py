from types import SimpleNamespace
from pathlib import Path
import pytest

from agentdock import desktop_chat
from agentdock.chat_sync import ChatSync, Source, sources
from agentdock.qa.store import QuestionStore


def cache(text="reply", updated="2026-10-07T12:00:00Z", stop="end_turn"):
    return {"conversationUuid": "session", "product": "hub", "tree": {
        "kind": "hub_transcript", "messages": [
            {"uuid": "u", "sender": "human", "created_at": "2026-10-07T11:00:00Z",
             "content": [{"type": "text", "text": "question"}]},
            {"uuid": "a", "sender": "assistant", "created_at": "2026-10-07T11:00:01Z",
             "updated_at": updated, "stop_reason": stop,
             "content": [{"type": "thinking", "thinking": "private"},
                         {"type": "tool_use", "name": "hidden"}, {"type": "text", "text": text}]}]}}


def test_stream_completion_replay_restart_and_cache_eviction(tmp_path, monkeypatch):
    store = QuestionStore(tmp_path / "dock")
    source = Source("claude-desktop", tmp_path / "source", "Desktop", "owner")
    current = [cache("partial", stop=None)]
    stamp = [1]
    monkeypatch.setattr(desktop_chat, "signature", lambda p: tuple(stamp))
    monkeypatch.setattr(desktop_chat, "read_cache", lambda p: (current, tuple(stamp)))
    sync = ChatSync(store, tmp_path / "dock", lambda: [source])
    sync.session_since = "2026-10-07T11:30:00Z"
    sync.live_since = sync.notify_since = sync.session_since
    notifications = []
    store.subscribe(lambda kind, value: notifications.append(kind))
    sync.scan()
    entries = store.conversations()[0]["entries"]
    assert [e["text"] for e in entries] == ["partial"]
    current[:] = [cache()]
    stamp[0] += 1
    sync.scan()
    assert [e["text"] for e in store.conversations()[0]["entries"]] == ["reply"]
    assert notifications.count("new-message") == 1
    sync.scan()
    assert len(store.conversations()[0]["entries"]) == 1
    assert notifications.count("new-message") == 1
    restarted = ChatSync(store, tmp_path / "dock", lambda: [source])
    restarted.session_since = "2026-10-07T11:30:00Z"
    restarted.scan()
    assert len(store.conversations()[0]["entries"]) == 1
    current.clear()  # Cache absence must not hide an already imported conversation.
    stamp[0] += 1
    restarted.scan()
    assert store.conversations()[0]["entries"][0]["text"] == "reply"


def test_unknown_format_retries_without_advancing_signature(tmp_path, monkeypatch):
    store = QuestionStore(tmp_path / "dock")
    source = Source("claude-desktop", tmp_path, "Desktop", "owner")
    value = cache()
    value["tree"]["kind"] = "future-format"
    monkeypatch.setattr(desktop_chat, "signature", lambda p: (1,))
    monkeypatch.setattr(desktop_chat, "read_cache", lambda p: ([value], (1,)))
    sync = ChatSync(store, tmp_path / "dock", lambda: [source])
    sync.scan()
    assert str(tmp_path) in sync.errors
    assert str(tmp_path) not in sync._signatures
    value["tree"]["kind"] = "hub_transcript"
    sync.scan()
    assert not sync.errors
    assert len(store.conversations()) == 1


def test_discover_store_and_registered_profiles_once(tmp_path):
    root = tmp_path / "AppData/Local/Packages/Claude_test/LocalCache/Roaming/Claude"
    (root / "IndexedDB/https_claude.ai_0.indexeddb.leveldb").mkdir(parents=True)
    profile = SimpleNamespace(kind="claude-desktop", path=str(root / "claude_desktop_config.json"),
                              id="desktop", name="Desktop")
    found = [s for s in sources([profile], home=tmp_path) if s.kind == "claude-desktop"]
    assert len(found) == 1
    assert found[0].root == root / "IndexedDB"


def test_snapshot_changes_are_rejected_before_parse(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "000001.log").write_bytes(b"test")
    stamps = iter([(("000001.log", 4, 1),), (("000001.log", 8, 2),)])
    monkeypatch.setattr(desktop_chat, "signature", lambda p: next(stamps))
    monkeypatch.setattr(desktop_chat.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(OSError, match="changed"):
        desktop_chat.read_cache(root)
    assert (root / "000001.log").read_bytes() == b"test"
    assert not list(tmp_path.glob("agentdock-desktop-*"))


def test_latest_leveldb_versions_include_deletions_before_deserialization():
    from agentdock._vendor.chromium.ccl_chromium_indexeddb import IndexedDb
    from agentdock._vendor.chromium.storage_formats.ccl_leveldb import Record, KeyState, FileType
    old = Record(b"key" + b"12345678", b"old", 1, KeyState.Live, FileType.Ldb, "test", 0, False)
    deleted = Record(b"key", b"", 2, KeyState.Deleted, FileType.Log, "test", 0, False)
    db = object.__new__(IndexedDb)
    db._db = SimpleNamespace(iterate_records_raw=lambda: iter([deleted, old]))
    db._cache_records()
    assert len(db._fetched_records) == 1
    assert db._fetched_records[0].key == b"key"
    assert db._fetched_records[0].state == KeyState.Deleted


def test_scope_and_content_filtering():
    value = cache()
    sid, title, entries = desktop_chat.messages(value)
    assert sid == "session" and title == "question"
    assert [e["text"] for e, _ in entries] == ["question", "reply"]
    value["product"] = "unknown-product"
    assert desktop_chat.messages(value) is None


def test_snapshot_tombstones_and_deleted_values_never_import(tmp_path, monkeypatch):
    from agentdock._vendor.chromium import ccl_chromium_indexeddb as indexeddb
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(desktop_chat.tempfile, "tempdir", str(tmp_path))
    def record(key, value, live=True):
        return SimpleNamespace(key=SimpleNamespace(raw_key=key), value=value, is_live=live)
    def store(records):
        return SimpleNamespace(iterate_records=lambda: iter(records))
    stores = {"trees": store([record(b"good", cache()), record(b"evicted", cache()),
                               record(b"deleted", cache(), False)]),
              "meta": store([record(b"evicted", {"tombstone": True})])}
    class FakeDB(dict):
        closed = False
        def close(self):
            self.closed = True
    wrapper = FakeDB({"claude-conversation-store": stores})
    monkeypatch.setattr(indexeddb, "WrappedIndexDB", lambda *args: wrapper)
    values, _ = desktop_chat.read_cache(root)
    assert values == [cache()]
    assert wrapper.closed
    assert not list(tmp_path.glob("agentdock-desktop-*"))


def test_old_history_skipped_and_failed_source_keeps_history(tmp_path, monkeypatch):
    store = QuestionStore(tmp_path / "dock")
    source = Source("claude-desktop", tmp_path, "Desktop", "owner")
    monkeypatch.setattr(desktop_chat, "signature", lambda p: (1,))
    monkeypatch.setattr(desktop_chat, "read_cache", lambda p: ([cache()], (1,)))
    sync = ChatSync(store, tmp_path / "dock", lambda: [source])
    sync.session_since = "2026-10-08T00:00:00Z"
    sync.scan()
    assert not store.conversations()
    sync.session_since = None
    sync._signatures.clear()
    sync.scan()
    assert len(store.conversations()) == 1
    def fail(p):
        raise OSError("unavailable")
    monkeypatch.setattr(desktop_chat, "signature", fail)
    sync.scan()
    assert len(store.conversations()) == 1
    assert str(tmp_path) in sync.errors


def test_desktop_mcp_link_preserves_question_owner(tmp_path, monkeypatch):
    from agentdock.qa.schema import AskInput
    value = cache()
    value['tree']['messages'][1]['content'].append(dict(type='tool_use', name='mcp__agentchat__ask_user',
        input=dict(work_id='work', request_key='question')))
    store = QuestionStore(tmp_path / 'dock')
    payload = AskInput(request_key='question', work_id='work', work_title='Task', question='Choose').model_dump()
    own = store.create('mcp-owner', 'Desktop', payload)
    other = store.create('other-owner', 'Other', payload)
    source = Source('claude-desktop', tmp_path, 'Desktop', 'native-owner', 'mcp-owner')
    monkeypatch.setattr(desktop_chat, 'signature', lambda p: (1,))
    monkeypatch.setattr(desktop_chat, 'read_cache', lambda p: ([value], (1,)))
    ChatSync(store, tmp_path / 'dock', lambda: [source]).scan()
    native = store.conversation_page('native-owner', 'session')['entries']
    assert any(e['id'] == own['id'] for e in native)
    assert not any(e['id'] == other['id'] for e in native)
    assert store.get(own['id'], 'mcp-owner')['status'] == 'pending'
    assert store.get(own['id'], 'mcp-owner')['work_id'] == 'work'


def test_cowork_result_filters_internal_rows_and_finalizes_same_message():
    rows = [
        dict(type='user', uuid='u', message=dict(content='question')),
        dict(type='user', uuid='internal', isSynthetic=True, message=dict(content='do not show')),
        dict(type='assistant', uuid='child', parent_tool_use_id='t', message=dict(content='child')),
        dict(type='assistant', uuid='a', message=dict(content=[dict(type='text', text='reply')], stop_reason=None)),
        dict(type='result', uuid='result', is_error=False, result='reply'),
    ]
    value = dict(product='cowork', conversationUuid='s', tree=dict(kind='cowork_remote', events=[
        dict(kind='message', seq=i, payload={**r, 'timestamp':f'2026-10-07T12:00:0{i}Z'}) for i,r in enumerate(rows)]))
    _, _, entries = desktop_chat.messages(value)
    assert [e['text'] for e,_ in entries] == ['question', 'reply', 'reply']
    assert entries[-1][0]['key'] == entries[-2][0]['key'] == 'a'
    assert entries[-1][0]['kind'] == 'completed'
    assert entries[-1][1] == '2026-10-07T12:00:04.000Z'


def test_legacy_selected_branch_excludes_siblings():
    first, answer = cache()['tree']['messages']
    answer['parent_message_uuid'] = 'u'
    sibling = {**answer, 'uuid':'sibling', 'content':[dict(type='text',text='not selected')]}
    value = dict(product='chat', conversationUuid='s', tree=dict(name='Original title',
        current_leaf_message_uuid='a', chat_messages=[first, sibling, answer]))
    _, title, entries = desktop_chat.messages(value)
    assert title == 'Original title'
    assert [e['key'] for e,_ in entries] == ['u','a']


def test_desktop_image_only_and_missing_images(tmp_path):
    import base64
    png = b'\x89PNG\r\n\x1a\n' + b'0' * 20
    value = cache()
    value['tree']['messages'] = [dict(uuid='image',sender='human',created_at='2026-10-07T12:00:00Z',content=[
        dict(type='image',file=dict(type='image/png',base64=base64.b64encode(png).decode())),
        dict(type='image',file=dict(type='image/png',base64=''))])]
    _,_,entries = desktop_chat.messages(value)
    store = QuestionStore(tmp_path)
    store.import_chat('o','Desktop','s','Images',[e for e,_ in entries])
    images = store.conversation_page('o','s')['entries'][0]['images']
    assert images[0]['id']
    assert images[1]['unavailable']


def test_tool_narration_hidden_consistently_and_completion_visible(tmp_path):
    from agentdock.chat_sync import claude_record
    value = cache(stop='tool_use')
    entries = [e for e,_ in desktop_chat.messages(value)[2]]
    store = QuestionStore(tmp_path)
    store.import_chat('o','Desktop','s','Task',entries)
    assert [e['text'] for e in store.conversation_page('o','s')['entries']] == ['question']
    state = {}
    rows,_ = claude_record(dict(type='assistant',uuid='a',timestamp='2026-10-07T12:00:00Z',
        message=dict(content=[dict(type='text',text='working')],stop_reason='tool_use')),state)
    assert rows[0]['phase'] == 'commentary'
    rows,_ = claude_record(dict(type='result',uuid='r',created_at='2026-10-07T12:00:01Z',is_error=True),state)
    assert rows[0]['kind'] == 'failed'


def test_one_unknown_desktop_conversation_does_not_block_others(tmp_path, monkeypatch):
    good = cache()
    bad = {**cache(), 'conversationUuid':'bad','tree':dict(kind='future')}
    monkeypatch.setattr(desktop_chat, 'signature', lambda p: (1,))
    monkeypatch.setattr(desktop_chat, 'read_cache', lambda p: ([bad,good], (1,)))
    store=QuestionStore(tmp_path/'dock')
    sync=ChatSync(store,tmp_path/'dock',lambda:[Source('claude-desktop',tmp_path,'Desktop','o')])
    sync.scan()
    assert store.conversations()[0]['work_id']=='session'
    assert str(tmp_path) in sync.errors


def test_recent_claude_file_mtime_lag_does_not_skip_new_message(tmp_path):
    import os, time, json
    from agentdock.chat_sync import iso
    root=tmp_path/'claude'
    path=root/'projects'/'p'/'s.jsonl'
    path.parent.mkdir(parents=True)
    store=QuestionStore(tmp_path/'dock')
    store.begin_chat_session()
    path.write_text(json.dumps(dict(type='assistant',uuid='a',sessionId='s',timestamp=iso(time.time()+1),
        message=dict(content='new',stop_reason='end_turn')))+'\n',encoding='utf-8')
    lag=time.time()-0.5
    os.utime(path,(lag,lag))
    ChatSync(store,tmp_path/'dock',lambda:[Source('claude-code',root,'Claude Code','o')]).scan()
    assert store.conversations()[0]['entries'][0]['text']=='new'
