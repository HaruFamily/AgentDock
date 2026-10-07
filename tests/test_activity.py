import json
import time
import os
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentdock.activity import ActivityStore, FRESH_SECONDS
from agentdock.activity_hook import normalize
from agentdock.broker import Broker
from agentdock.qa.store import QuestionStore

OWNER = 'a' * 64


def observation(state='running', **kwargs):
    return dict(client='opencode', session_id='s1', title='project', state=state, **kwargs)


def test_activity_permissions_isolation_expiry_restart(tmp_path):
    emitted = []
    store = ActivityStore(tmp_path, lambda *args: emitted.append(args))
    store.receive(OWNER, observation())
    store.receive(OWNER, observation('approval', request_id='p1'))
    store.receive(OWNER, observation('approval', request_id='p2'))
    store.receive(OWNER, observation('approval_done', request_id='p1'))
    assert store.tasks()[0]['state'] == 'approval'
    store.receive(OWNER, observation())
    assert store.tasks()[0]['state'] == 'approval'  # parallel activity is not consent
    store.receive(OWNER, observation('approval_done', request_id='p2'))
    assert store.tasks()[0]['state'] == 'running'
    assert len([e for e in emitted if e[0] == 'native-approval']) == 2
    store.receive('b' * 64, observation('approval'))
    assert len(store.tasks()) == 2
    assert all(t['state'] == 'unknown' for t in ActivityStore(tmp_path, lambda *a: None).tasks())
    store.live = {k: time.monotonic() - FRESH_SECONDS - 1 for k in store.live}
    assert store.attention() == 0
    assert all(t['state'] == 'unknown' for t in store.tasks())


def test_activity_terminal_read_and_owner(tmp_path):
    store = ActivityStore(tmp_path, lambda *a: None)
    store.receive(OWNER, observation('idle'))
    task = store.tasks()[0]
    assert store.attention() == 1
    assert not store.mark_read('b' * 64, task['work_id'], [task['latest']['id']])
    assert store.mark_read(OWNER, task['work_id'], [task['latest']['id']])
    assert store.attention() == 0
    store.remove('b' * 64, task['work_id'])
    assert store.tasks()
    store.remove(OWNER, task['work_id'])
    assert not store.tasks()


def test_hook_normalization_no_permission_decisions():
    base = dict(session_id='session', cwd='D:/project', hook_event_name='PermissionRequest')
    assert normalize('codex', base)['state'] == 'approval'
    assert normalize('claude-code', base) is None
    assert normalize('claude-code', {**base, 'hook_event_name': 'Notification', 'notification_type': 'idle_prompt'}) is None
    assert normalize('claude-code', {**base, 'hook_event_name': 'Notification', 'notification_type': 'permission_prompt'})['state'] == 'approval'
    assert normalize('codex', {**base, 'agent_id': 'child'}) is None
    assert normalize('codex', {**base, 'hook_event_name': 'Stop'})['state'] == 'idle'


def test_activity_broker_auth_and_schema(tmp_path):
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    headers = {'host': '127.0.0.1:1234', 'authorization': f'Bearer {broker.token}', 'x-agentdock-owner': OWNER}
    data = json.dumps(observation('approval')).encode()
    assert broker.handle('POST', '/activity', {}, data)[0] == 403
    assert broker.handle('POST', '/activity', {**headers, 'origin': 'https://example.com'}, data)[0] == 403
    assert broker.handle('POST', '/activity', headers, data)[0] == 200
    assert store.inbox_tasks()[0]['state'] == 'approval'
    invalid = json.dumps({**observation(), 'decision': 'allow'}).encode()
    assert broker.handle('POST', '/activity', headers, invalid)[0] == 400


@pytest.mark.skipif(os.name != 'nt', reason='Windows hook launcher')
def test_installed_hook_command_reaches_broker_without_deciding(tmp_path):
    from agentdock.extensions import PRESETS, target
    from agentdock.library import Library
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    broker.start()
    try:
        library = Library(Path(__file__).resolve().parent.parent, tmp_path)
        profile = SimpleNamespace(kind='codex', id='isolated', path=str(tmp_path / 'config.toml'))
        preset = next(e for e in PRESETS if e['key'] == 'inbox-activity')
        command = target(library, preset, profile).spec['command']
        payload = dict(session_id='test-session', cwd='D:/test', hook_event_name='PermissionRequest')
        result = subprocess.run(command.split(), input=json.dumps(payload), text=True,
                                capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0 and json.loads(result.stdout) == {}
        assert not result.stderr
        assert store.activity.tasks()[0]['state'] == 'approval'
    finally:
        broker.close()


@pytest.mark.skipif(not shutil.which('node'), reason='Node is not available')
def test_opencode_plugin_forwards_permission_ids(tmp_path):
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    broker.start()
    try:
        root = Path(__file__).resolve().parent.parent
        source = (root / 'agentdock/assets/opencode/inbox-activity.js').read_text('utf-8')
        source = source.replace('__INBOX_ENDPOINT__', json.dumps(str(tmp_path / 'endpoint.json')))
        source = source.replace('__INBOX_IDENTITY__', json.dumps('isolated-opencode'))
        plugin = tmp_path / 'plugin.mjs'
        plugin.write_text(source, 'utf-8')
        program = 'import { InboxActivity } from ' + json.dumps(plugin.as_uri()) + ''';
const plugin = await InboxActivity({ directory: "D:/test" });
for (const [type, properties] of [
  ["permission.asked", {sessionID: "s1", id: "p1"}],
  ["permission.asked", {sessionID: "s1", id: "p2"}],
  ["permission.replied", {sessionID: "s1", permissionID: "p1", reply: "once"}],
]) await plugin.event({event: {type, properties}});
'''
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', program],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        task = store.activity.tasks()[0]
        assert task['state'] == 'approval'
        assert set(task['latest']['approvals']) == {'p2'}
    finally:
        broker.close()
