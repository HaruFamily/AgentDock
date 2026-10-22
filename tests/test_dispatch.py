"""Sending tasks to OpenCode 2.x: a fake service that replays the recorded V2 event shapes."""
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentdock.dispatch import opencode
from agentdock.dispatch.opencode import OpenCodeError, OpenCodeService
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.store import DispatchStore, owner_for
from agentdock.qa.store import QuestionStore

PASSWORD = 'secret-password'
OWNER = owner_for('profile-1', 'opencode')


class FakeOpenCode:
    """Just enough of the OpenCode V2 API, with the event payloads seen from opencode 2.0.32."""

    def __init__(self, version='2.0.32'):
        self.version = version
        self.sessions, self.permissions, self.forms, self.messages = {}, {}, {}, {}
        self.active, self.listeners, self.calls = set(), [], []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _auth(self):
                import base64
                expected = 'Basic ' + base64.b64encode(f'opencode:{PASSWORD}'.encode()).decode()
                if self.headers.get('Authorization') != expected:
                    self.send_response(401)
                    self.end_headers()
                    return False
                return True

            def _json(self, code, body=None):
                raw = b'' if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if not self._auth():
                    return
                path = self.path.split('?')[0]
                if path == '/api/event':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    events = queue.Queue()
                    with fake.lock:
                        fake.listeners.append(events)
                    try:
                        self._send({'type': 'server.connected', 'data': {}})
                        while True:
                            try:
                                event = events.get(timeout=0.2)
                            except queue.Empty:
                                self.wfile.write(b': heartbeat\n\n')
                                self.wfile.flush()
                                continue
                            if event is None:
                                return
                            self._send(event)
                    except OSError:
                        return
                    finally:
                        with fake.lock:
                            fake.listeners.remove(events)
                parts = path.strip('/').split('/')
                if path == '/api/info':
                    return self._json(200, {'version': fake.version})
                if path == '/api/session/active':
                    return self._json(200, {'data': {s: {'type': 'running'} for s in fake.active}})
                if parts[:2] == ['api', 'session'] and len(parts) >= 3:
                    sid = parts[2]
                    if sid not in fake.sessions:
                        return self._json(404, {'message': 'not found'})
                    if len(parts) == 3:
                        return self._json(200, {'data': fake.sessions[sid]})
                    if parts[3] == 'permission':
                        return self._json(200, {'data': list(fake.permissions.get(sid, {}).values())})
                    if parts[3] == 'form':
                        return self._json(200, {'data': list(fake.forms.get(sid, {}).values())})
                    if parts[3] == 'message':
                        return self._json(200, {'data': list(reversed(fake.messages.get(sid, [])))})
                self._json(404, {'message': 'no route'})

            def _send(self, event):
                self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                self.wfile.flush()

            def _body(self):
                length = int(self.headers.get('Content-Length') or 0)
                return json.loads(self.rfile.read(length) or b'{}')

            def do_POST(self):
                if not self._auth():
                    return
                body = self._body()
                parts = self.path.split('?')[0].strip('/').split('/')
                fake.calls.append((self.command, '/'.join(parts), body))
                if parts == ['api', 'session']:
                    sid = f'ses_{len(fake.sessions) + 1}'
                    fake.sessions[sid] = {'id': sid, 'title': body.get('title'), 'location': body.get('location')}
                    return self._json(200, {'data': fake.sessions[sid]})
                sid = parts[2]
                if parts[3] == 'prompt':
                    fake.prompt(sid, body['text'])
                    return self._json(200, {'data': {'id': 'msg_u', 'type': 'user'}})
                if parts[3] == 'interrupt':
                    fake.finish(sid, 'interrupted', {'reason': 'user'})
                    return self._json(200, {'interrupted': True})
                if parts[3] == 'permission':
                    request = parts[4]
                    fake.permissions[sid].pop(request)
                    fake.emit('permission.replied', {'sessionID': sid, 'requestID': request, 'reply': body['decision']})
                    if body['decision'] == 'reject':
                        fake.finish(sid, 'failed', {'error': {'type': 'permission', 'message': '已拒絕'}})
                    else:
                        fake.reply(sid, 'allowed')
                    return self._json(200)
                if parts[3] == 'form':
                    fake.forms[sid].pop(parts[4])
                    fake.emit('form.replied', {'id': parts[4], 'sessionID': sid, 'answer': body['answer']})
                    fake.reply(sid, 'answer ' + json.dumps(body['answer']))
                    return self._json(200)
                self._json(404, {'message': 'no route'})

            def do_DELETE(self):
                if not self._auth():
                    return
                parts = self.path.split('?')[0].strip('/').split('/')
                fake.calls.append((self.command, '/'.join(parts), None))
                if len(parts) == 3:   # api/session/{id}: delete the session
                    fake.sessions.pop(parts[2], None)
                    return self._json(200, {'data': True})
                sid, fid = parts[2], parts[4]
                fake.forms[sid].pop(fid)
                fake.emit('form.cancelled', {'id': fid, 'sessionID': sid})
                fake.finish(sid, 'succeeded', {})
                self._json(200)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def emit(self, kind, data):
        with self.lock:
            for listener in self.listeners:
                listener.put({'id': f'evt_{time.time_ns()}', 'type': kind, 'data': data})

    def prompt(self, sid, text):
        uid = f'msg_{time.time_ns()}'
        self.messages.setdefault(sid, []).append({'id': uid, 'type': 'user', 'text': text})
        self.active.add(sid)
        self.sessions[sid].pop('outcome', None)
        self.emit('session.execution.started', {'sessionID': sid})
        self.emit('session.inbox.enqueued', {'sessionID': sid, 'inboxID': uid,
                                             'item': {'type': 'user', 'payload': {'text': text}}})
        if text.startswith('permission'):
            request = {'id': 'per_1', 'sessionID': sid, 'action': 'shell', 'resources': ['npm test']}
            self.permissions.setdefault(sid, {})['per_1'] = request
            self.emit('session.tool.input.started', {'sessionID': sid, 'name': 'shell'})
            self.emit('permission.asked', request)
        elif text.startswith('form'):
            form = {'id': 'frm_1', 'sessionID': sid, 'fields': [
                {'key': 'q0', 'title': 'Env', 'description': 'Where?', 'type': 'string', 'custom': True,
                 'options': [{'value': 'staging', 'label': 'staging'}, {'value': 'prod', 'label': 'prod'}]}]}
            self.forms.setdefault(sid, {})['frm_1'] = form
            self.emit('form.created', {'form': form})
        elif text.startswith('fail'):
            self.finish(sid, 'failed', {'error': {'type': 'provider.auth', 'message': 'HTTP 403'}})
        elif text.startswith('slow'):
            pass
        else:
            self.reply(sid, 'ok: ' + text)

    def reply(self, sid, text):
        message = f'msg_{time.time_ns()}'
        self.messages.setdefault(sid, []).append({'id': message, 'type': 'assistant',
                                                  'content': [{'type': 'text', 'text': text}]})
        self.emit('session.step.started', {'sessionID': sid, 'assistantMessageID': message})
        self.emit('session.text.delta', {'sessionID': sid, 'assistantMessageID': message, 'ordinal': 0,
                                         'delta': text[:3]})
        self.emit('session.text.ended', {'sessionID': sid, 'assistantMessageID': message, 'ordinal': 0, 'text': text})
        self.finish(sid, 'succeeded', {})

    def finish(self, sid, outcome, extra):
        self.active.discard(sid)
        self.sessions[sid]['outcome'] = outcome
        idle = {'id': f'msg_{time.time_ns()}', 'type': 'idle', 'outcome': outcome}
        if extra.get('error'):
            idle['error'] = extra['error']
        self.messages.setdefault(sid, []).append(idle)
        self.emit(f'session.execution.{outcome}', {'sessionID': sid, **extra})

    def close(self):
        with self.lock:
            for listener in self.listeners:
                listener.put(None)
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeOpenCode()
    yield server
    server.close()


def wait(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError('condition not reached')


def dispatcher(tmp_path, fake, store=None):
    store = store or QuestionStore(tmp_path)
    notes = []
    sender = Dispatcher(store.dispatch, notify=notes.append,
                        connect=lambda start: OpenCodeService(fake.url, PASSWORD, fake.version))
    return store, sender, notes


def only(store):
    rows = list(store.dispatch.rows.values())
    assert len(rows) == 1
    return rows[0]


def test_send_reply_and_inbox_card(tmp_path, fake):
    from agentdock.ui.notifications import notice_for
    store, sender, notes = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), '整理 README', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['state'] == 'done')
    row = only(store)
    assert row['reply'] == 'ok: 整理 README' and row['prompt'] == '整理 README' and not row['read']
    assert fake.sessions['ses_1']['location'] == {'directory': str(tmp_path)}
    task = store.inbox_tasks()[0]
    assert (task['owner'], task['work_id'], task['source']) == (OWNER, 'session:opencode:ses_1', 'OpenCode')
    assert task['state'] == 'unread'
    assert notice_for(store.inbox_tasks()).counts == (('completed', 1),)
    assert store.mark_read(OWNER, 'session:opencode:ses_1', [row['id']])
    assert store.inbox_tasks()[0]['state'] == '' and notice_for(store.inbox_tasks()).total == 0
    sender.send('opencode:ses_1', '再補一段')
    wait(lambda: only(store)['reply'] == 'ok: 再補一段' and only(store)['state'] == 'done')
    assert store.dispatch.recent == [str(tmp_path)] and notes == []
    sender.close()


def test_permission_needs_a_click_and_is_forwarded(tmp_path, fake):
    from agentdock.ui.notifications import notice_for
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'permission please', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['permissions'])
    task = store.inbox_tasks()[0]
    assert task['state'] == 'approval' and task['latest']['approvals'] == {'per_1': 'shell：npm test'}
    assert notice_for(store.inbox_tasks()).counts == (('approval', 1),)
    time.sleep(0.3)
    assert only(store)['state'] == 'running'  # nothing approves by itself
    sender.reply_permission('opencode:ses_1', 'per_1', 'once')
    wait(lambda: only(store)['state'] == 'done')
    assert ('POST', 'api/session/ses_1/permission/per_1/reply', {'decision': 'once'}) in fake.calls
    assert only(store)['permissions'] == {} and only(store)['reply'] == 'allowed'


def test_rejected_permission_and_failure_message(tmp_path, fake):
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'permission please', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['permissions'])
    sender.reply_permission('opencode:ses_1', 'per_1', 'reject')
    wait(lambda: only(store)['state'] == 'failed')
    assert only(store)['error'] == '已拒絕'
    sender.send('opencode:ses_1', 'fail now')
    wait(lambda: only(store)['state'] == 'failed' and only(store)['error'] == 'HTTP 403')


def test_question_form_answer_and_skip(tmp_path, fake):
    from agentdock.ui.notifications import notice_for
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'form please', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['forms'])
    assert store.inbox_tasks()[0]['state'] == 'waiting'
    assert notice_for(store.inbox_tasks()).counts == (('question', 1),)
    sender.reply_form('opencode:ses_1', 'frm_1', {'q0': 'staging'})
    wait(lambda: only(store)['state'] == 'done')
    assert only(store)['reply'] == 'answer {"q0": "staging"}' and only(store)['forms'] == {}
    sender.send('opencode:ses_1', 'form again')
    wait(lambda: only(store)['forms'])
    sender.reply_form('opencode:ses_1', 'frm_1', None)
    wait(lambda: only(store)['state'] == 'done' and not only(store)['forms'])
    assert ('DELETE', 'api/session/ses_1/form/frm_1', None) in fake.calls


def test_stop_running_task(tmp_path, fake):
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'slow work', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['state'] == 'running')
    assert store.inbox_tasks()[0]['state'] == 'running'
    sender.interrupt('opencode:ses_1')
    wait(lambda: only(store)['state'] == 'interrupted')


def test_restart_is_unknown_until_the_service_confirms(tmp_path, fake):
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'slow work', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['state'] == 'running')
    sender.close()
    restarted = QuestionStore(tmp_path)
    # Finished while AgentDock was closed.
    fake.reply('ses_1', 'finished later')
    assert only(restarted)['state'] == 'unknown'
    assert restarted.inbox_tasks()[0]['state'] == 'unknown'
    _, again, _ = dispatcher(tmp_path, fake, restarted)
    again.resume()
    wait(lambda: only(restarted)['state'] == 'done')
    assert only(restarted)['reply'] == 'finished later' and not only(restarted)['read']


def test_offline_service_keeps_cards_and_reports(tmp_path):
    store = QuestionStore(tmp_path)
    store.dispatch.track('opencode', 'ses_9', owner=OWNER, title='old', state='running')
    calls = []

    def refuse(start):
        calls.append(start)
        raise OpenCodeError('OpenCode 服務未執行。')
    sender = Dispatcher(store.dispatch, connect=refuse)
    sender.resume()
    wait(lambda: store.dispatch.offline == 'OpenCode 服務未執行。')
    sender.close()
    assert calls[0] is False  # a restart never starts the client by itself
    assert store.inbox_tasks()[0]['latest']['offline'] == 'OpenCode 服務未執行。'


def test_adopting_a_native_session_replaces_its_activity_card(tmp_path, fake):
    store, sender, _ = dispatcher(tmp_path, fake)
    fake.sessions['ses_tui'] = {'id': 'ses_tui', 'title': 'TUI work', 'location': {'directory': 'D:/x'}}
    fake.messages['ses_tui'] = [{'id': 'm1', 'type': 'user', 'text': 'from tui'},
                                {'id': 'm2', 'type': 'assistant', 'content': [{'type': 'text', 'text': 'tui answer'}]}]
    fake.sessions['ses_tui']['outcome'] = 'succeeded'
    store.activity.receive(OWNER, {'client': 'opencode', 'session_id': 'ses_tui', 'state': 'idle', 'title': 'x · ses_tui'})
    assert store.inbox_tasks()[0]['latest']['kind'] == 'native'
    sender.adopt('opencode', 'ses_tui', OWNER, 'x · ses_tui')
    wait(lambda: store.dispatch.rows and only(store)['reply'] == 'tui answer')
    tasks = store.inbox_tasks()
    assert len(tasks) == 1 and tasks[0]['latest']['kind'] == 'dispatch'
    assert tasks[0]['work_title'] == 'TUI work' and only(store)['prompt'] == 'from tui'
    sender.send('opencode:ses_tui', 'continue here')
    wait(lambda: only(store)['reply'] == 'ok: continue here')


def test_inbox_mcp_report_on_the_same_session_shares_one_card(tmp_path, fake):
    store, sender, _ = dispatcher(tmp_path, fake)
    sender.start_task(str(tmp_path), 'hello', OWNER)
    wait(lambda: store.dispatch.rows and only(store)['state'] == 'done')
    store.bind_conversation(OWNER, 'task-1', 'session:opencode:ses_1')
    store.receive(OWNER, 'OpenCode', {'request_key': 'r1', 'work_id': 'task-1', 'work_title': 'T',
                                      'kind': 'completed', 'text': 'reported'})
    tasks = store.inbox_tasks()
    assert len(tasks) == 1 and tasks[0]['latest']['kind'] == 'dispatch'
    report = next(e for e in store._chat['events'] if e['request_key'] == 'r1')
    assert tasks[0]['latest']['also_read'] == report['id']
    store.mark_read(OWNER, 'session:opencode:ses_1', [only(store)['id'], report['id']])
    assert store.conversation_summaries()[0]['state'] == '' and only(store)['read']
    store.remove_conversation(OWNER, 'session:opencode:ses_1')
    assert store.inbox_tasks() == [] and store.dispatch.rows == {}
    assert 'ses_1' in fake.sessions  # removing the card never deletes the client session


def test_service_discovery_version_and_auth(tmp_path, monkeypatch, fake):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    with pytest.raises(OpenCodeError):
        OpenCodeService.connect(start=False)
    (tmp_path / 'opencode').mkdir()
    (tmp_path / 'opencode' / 'service.json').write_text(json.dumps({'url': fake.url, 'password': PASSWORD}))
    assert OpenCodeService.connect(start=False).version == '2.0.32'
    (tmp_path / 'opencode' / 'service.json').write_text(json.dumps({'url': fake.url, 'password': 'wrong'}))
    with pytest.raises(OpenCodeError):
        OpenCodeService.connect(start=False)
    fake.version = '1.18.30'
    (tmp_path / 'opencode' / 'service.json').write_text(json.dumps({'url': fake.url, 'password': PASSWORD}))
    with pytest.raises(OpenCodeError, match='2.x'):
        OpenCodeService.connect(start=False)


def test_start_uses_real_exe_without_a_window(tmp_path, monkeypatch, fake):
    npm = tmp_path / 'npm'
    exe = npm / 'node_modules' / '@opencode' / 'cli' / 'bin' / 'opencode.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    (npm / 'opencode.cmd').write_text('@echo off')
    monkeypatch.setattr(opencode.shutil, 'which', lambda name: str(npm / 'opencode.cmd'))
    assert opencode.find_executable() == str(exe)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    ran = []

    def run(args, **kwargs):
        ran.append((args, kwargs))
        folder = tmp_path / 'state' / 'opencode'
        folder.mkdir(parents=True)
        (folder / 'service.json').write_text(json.dumps({'url': fake.url, 'password': PASSWORD}))
    monkeypatch.setattr(opencode.subprocess, 'run', run)
    assert OpenCodeService.connect(start=True).url == fake.url
    args, kwargs = ran[0]
    assert args == [str(exe), 'service', 'start'] and kwargs['creationflags'] == opencode.NO_WINDOW
    assert kwargs['stdin'] is opencode.subprocess.DEVNULL


def test_broker_cannot_start_or_approve_work(tmp_path):
    from agentdock.broker import Broker
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    headers = {'authorization': f'Bearer {broker.token}', 'host': '127.0.0.1:1', 'x-agentdock-owner': OWNER}
    for path in ('/dispatch', '/send', '/permission', '/sessions/ses_1/prompt'):
        status, _ = broker.handle('POST', path, headers, b'{}')
        assert status != 200
    assert store.dispatch.rows == {}


def test_dispatch_package_has_no_gui_import():
    import subprocess
    import sys
    code = ('import sys; import agentdock.qa.store, agentdock.dispatch.runner, agentdock.mcp_server; '
            'print("PySide6" in sys.modules)')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'False'


def test_saved_rows_without_session_are_ignored(tmp_path):
    (tmp_path / 'dispatch.json').write_text(json.dumps({'rows': {'x': {'state': 'running'}}, 'recent_dirs': [1, 'D:/a']}))
    store = DispatchStore(tmp_path, lambda *_: None)
    assert store.rows == {} and store.recent == ['D:/a']
