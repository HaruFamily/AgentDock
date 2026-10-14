import json

import pytest

from agentdock.activity_hook import conversation_link
from agentdock.broker import Broker
from agentdock.qa.store import QuestionStore
from test_agentchat import OWNER, event, question


def test_session_groups_followups_without_changing_protocol_ids(tmp_path):
    store = QuestionStore(tmp_path)
    first = store.receive(OWNER, 'OpenCode', event(work_id='first'))
    second = store.receive(OWNER, 'OpenCode', event(work_id='second'))
    session = 'session:opencode:s1'
    for work in ('first', 'second'):
        store.bind_conversation(OWNER, work, session)
    assert len(store.inbox_tasks()) == 1
    assert store.inbox_tasks()[0]['latest']['id'] == second['id']
    assert store.receive(OWNER, 'OpenCode', event(work_id='first'))['id'] == first['id']
    assert store.mark_read(OWNER, session, [second['id']])
    store = QuestionStore(tmp_path)
    assert len(store.inbox_tasks()) == 1
    assert store.inbox_tasks()[0]['latest']['read'] is True
    store.bind_conversation('b' * 64, 'first', session)
    store.receive('b' * 64, 'OpenCode', event(work_id='first'))
    store.bind_conversation(OWNER, 'third', 'session:opencode:s2')
    store.receive(OWNER, 'OpenCode', event(work_id='third'))
    assert len(store.inbox_tasks()) == 3
    with pytest.raises(ValueError):
        store.bind_conversation(OWNER, 'first', 'session:opencode:s2')
    store.remove_conversation(OWNER, session)
    assert len(store.inbox_tasks()) == 2


def test_group_pending_running_and_native_state(tmp_path):
    store = QuestionStore(tmp_path)
    session = 'session:opencode:s1'
    for work in ('q', 'result'):
        store.bind_conversation(OWNER, work, session)
    q = question(store, work_id='q')
    with pytest.raises(ValueError, match='待回答'):
        store.receive(OWNER, 'OpenCode', event(work_id='result'))
    store.answer(q['id'], {'text': 'yes'})
    assert store.inbox_tasks()[0]['state'] == 'running'
    store.receive(OWNER, 'OpenCode', event(work_id='result'))
    assert store.inbox_tasks()[0]['state'] == 'unread'
    store.activity.receive(OWNER, dict(client='opencode', session_id='s1', title='native', state='idle'))
    assert len(store.inbox_tasks()) == 1
    store.activity.receive(OWNER, dict(client='opencode', session_id='s1', title='native', state='approval'))
    assert len(store.inbox_tasks()) == 1
    assert any(t['state'] == 'approval' for t in store.inbox_tasks())
    assert store.mark_read(OWNER, session, [store.inbox_tasks()[0]['latest']['id']])
    store.remove_conversation(OWNER, session)
    assert not store.inbox_tasks()


def test_link_endpoint_and_hook_allowlist(tmp_path):
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    headers = {'host': '127.0.0.1:1234', 'authorization': f'Bearer {broker.token}', 'x-agentdock-owner': OWNER}
    raw = dict(session_id='full-session-id', hook_event_name='PreToolUse',
               tool_name='mcp__inbox__report_to_user', tool_input={'work_id': 'work', 'text': 'private'})
    link = conversation_link('codex', raw)
    assert link == {'work_id': 'work', 'conversation_id': 'session:codex:full-session-id'}
    assert conversation_link('codex', {**raw, 'tool_name': 'other_report_to_user'}) is None
    assert conversation_link('codex', {**raw, 'agent_id': 'child'}) is None
    body = json.dumps(link).encode()
    assert broker.handle('POST', '/conversation-links', {}, body)[0] == 403
    assert broker.handle('POST', '/conversation-links', headers, body)[0] == 200
    assert store._chat_key(OWNER, 'work') == (OWNER, link['conversation_id'])
    assert store._chat_key('b' * 64, 'work') == ('b' * 64, 'work')


def test_late_link_preserves_question_editor(tmp_path):
    from PySide6.QtWidgets import QApplication
    from agentdock.ui.inbox_view import InboxView
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    view = InboxView(store)
    view.open_question(q['id'])
    row = view.rows[(OWNER, 'w')]
    editor = row.foldouts[q['id']].editor
    store.bind_conversation(OWNER, 'w', 'session:codex:s1')
    view.refresh()
    assert view.rows[(OWNER, 'session:codex:s1')] is row
    assert row.foldouts[q['id']].editor is editor
    view.close()
    view.deleteLater()


def test_mcp_report_passes_explicit_session_to_broker(tmp_path, monkeypatch):
    import asyncio
    from agentdock import mcp_server
    from agentdock.client import BrokerClient
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    endpoint = broker.start()
    monkeypatch.setenv('AGENTDOCK_SOURCE', 'OpenCode')
    monkeypatch.setattr(mcp_server, 'ensure_app', lambda: BrokerClient(endpoint, OWNER))
    try:
        for work in ('first', 'second'):
            result = asyncio.run(mcp_server.report_to_user('done', work, work, 'completed', 'result', None,
                                                          conversation_id='full-session'))
            assert not result.isError
        assert len(store.inbox_tasks()) == 1
        assert store.inbox_tasks()[0]['work_id'] == 'session:opencode:full-session'
    finally:
        broker.close()
