import threading

import pytest

from agentdock.dispatch.adapter import ClientError
from agentdock.dispatch.codex import CodexClient
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.store import DispatchStore


def test_terminal_error_recovers_and_late_completion_cannot_end_new_turn(monkeypatch):
    client = CodexClient()
    events, calls = [], []
    client.emit = lambda sid, event: events.append(event)
    monkeypatch.setattr(client, '_load', lambda row: None)
    monkeypatch.setattr(client, 'call', lambda method, params: calls.append((method, params)) or {})
    client._notification('turn/started', {'threadId': 's', 'turn': {'id': 'old'}})
    client._notification('error', {'threadId': 's', 'turnId': 'old', 'willRetry': True,
                                   'error': {'message': 'retrying'}})
    assert client.running('s')
    client._notification('error', {'threadId': 's', 'turnId': 'old', 'willRetry': False,
                                   'error': {'message': 'capacity'}})
    assert not client.running('s')
    assert events[-1]['type'] == 'session.execution.failed'
    client.prompt({'session_id': 's', 'model': 'other'}, 'again')
    assert calls[-1][0] == 'turn/start' and calls[-1][1]['model'] == 'other'
    client._notification('turn/started', {'threadId': 's', 'turn': {'id': 'new'}})
    client._notification('turn/completed', {'threadId': 's', 'turn': {'id': 'old', 'status': 'failed'}})
    assert client.running('s') and events[-1]['type'] == 'session.execution.started'
    client._notification('turn/completed', {'threadId': 's', 'turn': {'id': 'new', 'status': 'completed'}})
    assert not client.running('s')
    client._notification('turn/completed', {'threadId': 's', 'turn': {'id': 'old', 'status': 'failed'}})
    assert events[-1]['type'] == 'session.execution.succeeded'


@pytest.mark.parametrize('form', [False, True])
def test_failed_reply_keeps_request_until_user_retries(tmp_path, monkeypatch, form):
    client = CodexClient()
    store = DispatchStore(tmp_path, lambda *a: None)
    store.track('codex', 's', owner='o', title='t')
    notice = threading.Event()
    dispatcher = Dispatcher(store, notify=lambda text: notice.set(), adapters=[client])
    method = 'item/tool/requestUserInput' if form else 'item/commandExecution/requestApproval'
    client._server_request({'id': 1, 'method': method, 'params': {'threadId': 's', 'questions': []}})
    def fail(*args):
        raise ClientError('pipe unavailable')
    monkeypatch.setattr(client, '_respond', fail)
    reply = (lambda: dispatcher.reply_form('codex:s', 'r1', {'q': 'answer'})) if form else (
        lambda: dispatcher.reply_permission('codex:s', 'r1', 'once'))
    reply()
    assert notice.wait(2)
    assert 'r1' in client._requests
    assert 'r1' in store.get('codex:s')['forms' if form else 'permissions']
    sent = []
    monkeypatch.setattr(client, '_respond', lambda request, result: sent.append(result))
    row = store.get('codex:s')
    if form:
        client.reply_form(row, 'r1', {'q': 'answer'})
    else:
        client.reply_permission(row, 'r1', 'once')
    assert len(sent) == 1 and not client._requests
    assert not store.get('codex:s')['forms' if form else 'permissions']
    dispatcher.close()


def test_unexpected_send_exception_restores_final_state(tmp_path, monkeypatch):
    client = CodexClient()
    store = DispatchStore(tmp_path, lambda *a: None)
    store.track('codex', 's', owner='o', title='t', state='done')
    restored = threading.Event()
    dispatcher = Dispatcher(store, adapters=[client])
    def fail(*args):
        raise OSError('broken transport')
    monkeypatch.setattr(client, 'prompt', fail)
    dispatcher.send('codex:s', 'text', failed=restored.set)
    assert restored.wait(2)
    assert store.get('codex:s')['state'] == 'failed'
    assert 'broken transport' in store.get('codex:s')['error']
    dispatcher.close()


def test_codex_sessions_reads_all_pages(monkeypatch):
    client = CodexClient()
    calls = []
    def call(method, params):
        calls.append(params)
        if not params.get('cursor'):
            return {'data': [{'id': 'one'}], 'nextCursor': 'page2'}
        return {'data': [{'id': 'two'}, {'id': 'child', 'parentThreadId': 'one'}], 'nextCursor': None}
    monkeypatch.setattr(client, 'call', call)
    assert [s['id'] for s in client.sessions()] == ['one', 'two']
    assert calls[1]['cursor'] == 'page2'


def test_new_requests_preserve_existing_form_widget_and_answer(ui_app):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    from agentdock.ui.chat_conversation import ConversationView
    host = QWidget()
    host.resize(500, 500)
    view = SimpleNamespace(request_ids=(), permission_rows=[], forms={}, requests=QVBoxLayout(host),
                           use_web=False, request_area=host, _fit_requests=lambda: None)
    form = {'id': 'q', 'fields': [{'key': 'answer', 'description': 'Question', 'custom': True}]}
    row = {'source': 'Codex', 'permissions': {}, 'forms': {'q': form}}
    view.answer_form = lambda *a: None
    ConversationView._rebuild_requests(view, row, ((), ('q',)))
    original = view.forms['q']
    original.inputs[0][2].setPlainText('unfinished answer')
    row['forms']['q2'] = {**form, 'id': 'q2'}
    ConversationView._rebuild_requests(view, row, ((), ('q', 'q2')))
    assert view.forms['q'] is original
    assert original.answer() == {'answer': 'unfinished answer'}
    del row['forms']['q2']
    ConversationView._rebuild_requests(view, row, ((), ('q',)))
    assert view.forms['q'] is original and original.answer() == {'answer': 'unfinished answer'}
    host.close()
