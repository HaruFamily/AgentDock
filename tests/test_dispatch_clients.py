"""Claude Code and Codex as send/receive clients, against fake CLIs speaking their protocols."""
import json
import sys
import time
from pathlib import Path

import pytest

from agentdock.dispatch import claude as claude_module
from agentdock.dispatch.claude import ClaudeClient, read_history, transcript_file
from agentdock.dispatch.codex import CodexClient, messages_of, tool_of
from agentdock.dispatch.grok import GrokClient, Record
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.store import owner_for
from agentdock.dispatch.transcript import Transcript
from agentdock.qa.store import QuestionStore

FAKES = Path(__file__).parent / 'fakes'
OWNER = owner_for('p', 'claude-code')


def wait(condition, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError('timed out')


def lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_CLAUDE_LOG', str(tmp_path / 'claude.log'))
    monkeypatch.setenv('FAKE_CODEX_LOG', str(tmp_path / 'codex.log'))
    monkeypatch.setenv('FAKE_GROK_LOG', str(tmp_path / 'grok.log'))
    monkeypatch.setenv('FAKE_GROK_STORE', str(tmp_path / 'grok-sessions.json'))
    monkeypatch.setenv('CLAUDECODE', '1')   # must not leak into the child
    work = tmp_path / 'work'
    work.mkdir()
    store = QuestionStore(tmp_path / 'data')
    notes, events, started = [], [], []
    claude = ClaudeClient(find=lambda: [sys.executable, str(FAKES / 'fake_claude.py')])
    codex = CodexClient(find=lambda: [sys.executable, str(FAKES / 'fake_codex.py')])
    grok = GrokClient(find=lambda: [sys.executable, str(FAKES / 'fake_grok.py')])
    sender = Dispatcher(store.dispatch, notify=notes.append, connect=lambda _s: None, adapters=[claude, codex, grok])
    sender.listeners.append(lambda key, event: events.append((key, event)))
    yield store, sender, notes, events, started, work, tmp_path
    sender.close()


def start(setup, client, text):
    store, sender, notes, events, started, work, _tmp = setup
    sender.start_task(str(work), text, OWNER, started=started.append, client=client)
    wait(lambda: started)
    return started[-1]


def settled(store, key):
    return lambda: store.dispatch.get(key)['state'] in ('done', 'failed', 'interrupted')


@pytest.mark.parametrize('client', ['claude-code', 'codex', 'grok-cli'])
def test_send_reply_and_continue(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    assert set(sender.clients) == {'opencode', 'claude-code', 'codex', 'grok-cli'}
    key = start(setup, client, 'hello')
    assert key.startswith(client + ':')
    wait(settled(store, key))
    row = store.dispatch.get(key)
    assert row['state'] == 'done' and row['reply'] == 'Hello there' and row['prompt'] == 'hello'
    assert row['source'] == {'claude-code': 'Claude Code', 'codex': 'Codex', 'grok-cli': 'Grok'}[client] and row['directory'] == str(work)
    # Live events build the same conversation the chat window shows.
    live = Transcript()
    for k, event in events:
        assert k == key
        live.apply(event)
    assert [i['kind'] for i in live.items] == ['user', 'assistant', 'end']
    assert live.items[1]['parts'][0]['text'] == 'Hello there' and live.items[2]['outcome'] == 'succeeded'
    sender.send(key, 'again')
    wait(lambda: store.dispatch.get(key)['prompt'] == 'again' and store.dispatch.get(key)['state'] == 'done')
    assert notes == []
    if client == 'claude-code':
        log = lines(tmp / 'claude.log')
        starts = [l for l in log if 'argv' in l]
        assert len(starts) == 1 and '--session-id' in starts[0]['argv'] and starts[0]['cwd'] == str(work)
        assert '--permission-prompt-tool' in starts[0]['argv'] and starts[0]['entrypoint'] == 'sdk-py'
        users = [l for l in log if l.get('type') == 'user']
        assert [u['message']['content'] for u in users] == ['hello', 'again']
        assert all(u['session_id'] == key.split(':', 1)[1] for u in users)
    elif client == 'grok-cli':
        log = lines(tmp / 'grok.log')
        assert [l['method'] for l in log if l.get('method') in ('session/new', 'session/prompt')] == \
            ['session/new', 'session/prompt', 'session/prompt']
        init = next(l for l in log if l.get('method') == 'initialize')['params']
        assert init['clientCapabilities'] == {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}
        new = next(l for l in log if l.get('method') == 'session/new')['params']
        assert new == {'cwd': str(work), 'mcpServers': []}   # never yolo: Grok's own permission mode applies
    else:
        log = lines(tmp / 'codex.log')
        assert [l['method'] for l in log if l.get('method') in ('thread/start', 'turn/start')] == \
            ['thread/start', 'turn/start', 'turn/start']
        assert next(l for l in log if l.get('method') == 'thread/start')['params']['cwd'] == str(work)


@pytest.mark.parametrize('client,decision,expect', [
    ('claude-code', 'once', 'done'), ('claude-code', 'reject', 'stopped'),
    ('codex', 'once', 'decision accept'), ('codex', 'always', 'decision acceptForSession'),
    ('codex', 'reject', 'decision decline'),
    ('grok-cli', 'once', 'option allow-once'), ('grok-cli', 'always', 'option always-allow'),
    ('grok-cli', 'reject', 'option reject-once')])
def test_permissions_wait_for_a_click(setup, client, decision, expect):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'TOOL' if client == 'claude-code' else 'CMD')
    wait(lambda: store.dispatch.get(key)['permissions'])
    (rid, permission), = store.dispatch.get(key)['permissions'].items()
    if client == 'claude-code':
        assert permission == {'action': 'Bash', 'resources': ['npm test', 'run tests']}
    elif client == 'grok-cli':
        assert permission == {'action': 'Run Command', 'resources': ['npm test']}
    else:
        assert permission == {'action': 'shell', 'resources': ['npm test', 'needs network']}   # no shell wrapper
    time.sleep(0.3)
    assert store.dispatch.get(key)['state'] == 'running'   # nothing decides on its own
    sender.reply_permission(key, rid, decision)
    wait(settled(store, key))
    row = store.dispatch.get(key)
    assert row['permissions'] == {} and row['reply'].endswith(expect) and notes == []


def test_claude_always_adds_only_the_suggested_rules(setup):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, 'claude-code', 'TOOL')
    wait(lambda: store.dispatch.get(key)['permissions'])
    sender.reply_permission(key, next(iter(store.dispatch.get(key)['permissions'])), 'always')
    wait(settled(store, key))
    answer = next(l for l in lines(tmp / 'claude.log') if l.get('type') == 'control_response')['response']['response']
    assert answer['behavior'] == 'allow' and answer['updatedInput'] == {'command': 'npm test', 'description': 'run tests'}
    assert [s['type'] for s in answer['updatedPermissions']] == ['addRules']   # never switches the mode


def test_codex_file_change_shows_paths(setup):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, 'codex', 'EDIT')
    wait(lambda: store.dispatch.get(key)['permissions'])
    (rid, permission), = store.dispatch.get(key)['permissions'].items()
    assert permission == {'action': 'edit', 'resources': ['src/app.py']}
    sender.reply_permission(key, rid, 'once')
    wait(settled(store, key))


@pytest.mark.parametrize('client', ['claude-code', 'codex', 'grok-cli'])
def test_questions_become_forms(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'ASK')
    wait(lambda: store.dispatch.get(key)['forms'])
    (fid, form), = store.dispatch.get(key)['forms'].items()
    field = form['fields'][0]
    assert field['description'] == 'Which colour?' and field['custom'] is True
    assert field['title'] == ('' if client == 'grok-cli' else 'Colour')
    assert [o['value'] for o in field['options']] == ['Red', 'Blue']
    sender.reply_form(key, fid, {field['key']: 'Blue'})
    wait(settled(store, key))
    row = store.dispatch.get(key)
    assert row['forms'] == {}
    if client == 'claude-code':
        assert row['reply'] == 'answers {"Which colour?": "Blue"}'
    elif client == 'grok-cli':
        assert row['reply'] == 'answers {"outcome": "accepted", "answers": {"Which colour?": "Blue"}}'
    else:
        assert row['reply'] == 'answers {"colour": {"answers": ["Blue"]}}'


def test_claude_skipped_question_is_denied(setup):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, 'claude-code', 'ASK')
    wait(lambda: store.dispatch.get(key)['forms'])
    sender.reply_form(key, next(iter(store.dispatch.get(key)['forms'])), None)
    wait(settled(store, key))
    assert store.dispatch.get(key)['reply'] == 'stopped'


@pytest.mark.parametrize('client', ['claude-code', 'codex', 'grok-cli'])
def test_stop(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'SLOW')
    wait(lambda: any(e['type'] == 'session.execution.started' for _k, e in events))
    time.sleep(0.2)
    sender.interrupt(key)
    wait(settled(store, key))
    assert store.dispatch.get(key)['state'] == 'interrupted' and notes == []


@pytest.mark.parametrize('client,error', [('claude-code', 'API Error: 401 unauthorized'), ('codex', 'quota exceeded'),
                                          ('grok-cli', 'Internal error：quota exceeded')])
def test_failure_reason(setup, client, error):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'FAIL')
    wait(settled(store, key))
    row = store.dispatch.get(key)
    assert row['state'] == 'failed' and row['error'] == error


@pytest.mark.parametrize('client,detail', [('claude-code', 'boom: something broke'), ('codex', 'codex panicked'),
                                           ('grok-cli', 'grok crashed')])
def test_crash_is_reported_and_next_message_restarts(setup, client, detail):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'CRASH')
    wait(settled(store, key))
    row = store.dispatch.get(key)
    assert row['state'] == 'failed' and detail in row['error']
    sender.send(key, 'hello')
    wait(lambda: store.dispatch.get(key)['state'] == 'done')
    if client == 'claude-code':
        starts = [l['argv'] for l in lines(tmp / 'claude.log') if 'argv' in l]
        assert '--session-id' in starts[0] and '--resume' in starts[1]   # the new process resumes the session
    elif client == 'grok-cli':
        assert any(l.get('method') == 'session/load' for l in lines(tmp / 'grok.log'))
    else:
        assert any(l.get('method') == 'thread/resume' for l in lines(tmp / 'codex.log'))


def test_missing_cli_is_explained(tmp_path):
    store = QuestionStore(tmp_path)
    notes = []
    sender = Dispatcher(store.dispatch, notify=notes.append, connect=lambda _s: None,
                        adapters=[ClaudeClient(find=lambda: None), CodexClient(find=lambda: None),
                                  GrokClient(find=lambda: None)])
    for client in ('claude-code', 'codex', 'grok-cli'):
        sender.start_task(str(tmp_path), 'hi', OWNER, client=client)
    wait(lambda: len(notes) == 3)
    for name in ('找不到 Claude Code', '找不到 Codex', '找不到 Grok'):
        assert any(name in n for n in notes)
    sender.close()


def test_restart_marks_unfinished_turns_interrupted(tmp_path):
    store = QuestionStore(tmp_path)
    store.dispatch.track('claude-code', 's1', owner=OWNER, title='t')
    store.dispatch.update('claude-code:s1', permissions={'p': {'action': 'Bash', 'resources': []}})
    store.dispatch.track('codex', 't1', owner=OWNER, title='t', state='done')
    reopened = QuestionStore(tmp_path)
    connects = []
    sender = Dispatcher(reopened.dispatch, connect=lambda start: connects.append(start),
                        adapters=[ClaudeClient(find=lambda: None), CodexClient(find=lambda: None)])
    sender.resume()
    row = reopened.dispatch.get('claude-code:s1')
    assert row['state'] == 'interrupted' and row['permissions'] == {} and 'AgentDock' in row['error']
    assert reopened.dispatch.get('codex:t1')['state'] == 'done'
    time.sleep(0.2)
    assert connects == []   # OpenCode is not contacted for other clients' cards
    sender.close()


def test_stale_permission_is_dropped(setup):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, 'claude-code', 'hello')
    wait(settled(store, key))
    store.dispatch.update(key, permissions={'gone': {'action': 'Bash', 'resources': []}})
    sender.reply_permission(key, 'gone', 'once')
    wait(lambda: notes)
    assert '已失效' in notes[0] and store.dispatch.get(key)['permissions'] == {}


@pytest.mark.parametrize('client', ['claude-code', 'codex', 'grok-cli'])
def test_history_matches_live_conversation(setup, client, monkeypatch):
    store, sender, notes, events, started, work, tmp = setup
    if client == 'claude-code':
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp / 'claude-config'))
    key = start(setup, client, 'hello')
    wait(settled(store, key))
    if client == 'claude-code':
        # The fake does not write a transcript; write what Claude Code would have written.
        sid = key.split(':', 1)[1]
        user_id = next(e for _k, e in events if e['type'] == 'session.inbox.enqueued')['data']['inboxID']
        mid = next(e for _k, e in events if e['type'] == 'session.step.started')['data']['assistantMessageID']
        folder = tmp / 'claude-config' / 'projects' / 'x'
        folder.mkdir(parents=True)
        (folder / f'{sid}.jsonl').write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'user', 'uuid': user_id, 'parentUuid': None, 'message': {'role': 'user', 'content': 'hello'}},
            {'type': 'assistant', 'uuid': 'a1', 'parentUuid': user_id,
             'message': {'id': mid, 'content': [{'type': 'text', 'text': 'Hello there'}]}}]), encoding='utf-8')
    got = []
    sender.history(key, None, lambda messages, older: got.append((messages, older)), failed=got.append)
    wait(lambda: got)
    messages, older = got[0]
    assert older is None
    live = Transcript()
    for _k, event in events:
        live.apply(event)
    stored = Transcript(messages)
    strip = lambda items: [(i['kind'], i['id'], [p.get('text') for p in i.get('parts', [])]) for i in items
                           if i['kind'] != 'end']
    assert strip(stored.items) == strip(live.items)
    # Loading history after live events does not duplicate anything.
    assert live.prepend(messages) == []


def test_claude_transcript_parsing(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path))
    directory = r'C:\Users\me\My Project'
    folder = tmp_path / 'projects' / 'C--Users-me-My-Project'
    folder.mkdir(parents=True)
    entries = [
        {'type': 'user', 'uuid': 'u1', 'parentUuid': None, 'message': {'role': 'user', 'content': 'fix it'}},
        {'type': 'attachment', 'uuid': 'x1', 'parentUuid': 'u1'},
        {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'x1', 'message': {'id': 'm1', 'content': [
            {'type': 'thinking', 'thinking': '…'}, {'type': 'text', 'text': 'Looking.'}]}},
        {'type': 'assistant', 'uuid': 'a2', 'parentUuid': 'a1', 'message': {'id': 'm1', 'content': [
            {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'ls'}}]}},
        {'type': 'user', 'uuid': 'r1', 'parentUuid': 'a2', 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 't1', 'content': [{'type': 'text', 'text': 'a.txt'}]}]}},
        {'type': 'assistant', 'uuid': 's1', 'parentUuid': 'r1', 'isSidechain': True,
         'message': {'id': 'side', 'content': [{'type': 'text', 'text': 'subagent'}]}},
        {'type': 'user', 'uuid': 'meta', 'parentUuid': 'r1', 'isMeta': True, 'message': {'content': 'caveat'}},
        {'type': 'user', 'uuid': 'cmd', 'parentUuid': 'meta', 'message': {'content': '<command-name>/clear</command-name>'}},
        {'type': 'user', 'uuid': 'u2', 'parentUuid': 'cmd', 'message': {'content': [{'type': 'text', 'text': 'stop'}]}},
        {'type': 'user', 'uuid': 'i1', 'parentUuid': 'u2', 'message': {'content': [
            {'type': 'text', 'text': '[Request interrupted by user]'}]}},
        {'type': 'last-prompt', 'leafUuid': 'i1'},
    ]
    (folder / 'sid.jsonl').write_text('\n'.join(json.dumps(e) for e in entries) + '\nnot json\n', encoding='utf-8')
    path = transcript_file(directory, 'sid')
    assert path == folder / 'sid.jsonl'
    messages = read_history(path)
    assert [m['type'] for m in messages] == ['user', 'assistant', 'user', 'idle']
    tool = messages[1]['content'][2]
    assert messages[1]['content'][:2] == [{'type': 'reasoning', 'text': '…'}, {'type': 'text', 'text': 'Looking.'}]
    assert tool['name'] == 'Bash' and tool['state']['status'] == 'completed'
    assert tool['state']['content'] == [{'type': 'text', 'text': 'a.txt'}]
    assert messages[3]['outcome'] == 'interrupted'
    items = Transcript(messages).items
    assert [i['kind'] for i in items] == ['user', 'assistant', 'user', 'end']
    assert transcript_file(r'D:\elsewhere', 'missing') is None and read_history(None) == []


def test_codex_items_as_tools():
    command = {'type': 'commandExecution', 'id': 'c', 'command': "bash -lc 'ls'", 'status': 'failed', 'exitCode': 2,
               'commandActions': [{'command': 'ls'}], 'aggregatedOutput': 'nope'}
    assert tool_of(command) == {'name': 'shell', 'input': {'command': 'ls'}, 'status': 'error', 'output': 'nope',
                                'error': 'nope'}
    assert tool_of({'type': 'reasoning', 'id': 'r'}) is None
    assert tool_of({'type': 'fileChange', 'id': 'f', 'status': 'declined', 'changes': [{'path': 'a', 'diff': 'd'}]})['error'] == '已拒絕'
    messages = messages_of([{'id': 't1', 'status': 'failed', 'error': {'message': 'boom'}, 'items': [
        {'type': 'userMessage', 'id': 'u', 'content': [{'type': 'text', 'text': 'hi'}]},
        {'type': 'reasoning', 'id': 'r'}, command,
        {'type': 'agentMessage', 'id': 'a', 'text': 'sorry'}]}])
    assert [m['type'] for m in messages] == ['user', 'assistant', 'assistant', 'idle']
    assert Transcript(messages).items[-1] == {'kind': 'end', 'id': 'end:t1', 'outcome': 'failed', 'error': 'boom'}


def test_find_executables_prefer_the_real_binaries(tmp_path, monkeypatch):
    npm = tmp_path / 'npm'
    claude_exe = npm / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe'
    codex_exe = npm / 'node_modules' / '@openai' / 'codex-win32-x64' / 'vendor' / 'x86_64-pc-windows-msvc' / 'bin' / 'codex.exe'
    for exe in (claude_exe, codex_exe):
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'MZ')
    (npm / 'claude.cmd').write_text('@echo off')
    (npm / 'codex.cmd').write_text('@echo off')
    from agentdock.dispatch import adapter, codex
    monkeypatch.setattr(claude_module.shutil, 'which', lambda name: str(npm / f'{name}.cmd') if name in ('claude', 'codex') else None)
    monkeypatch.setattr(codex.shutil, 'which', lambda name: str(npm / f'{name}.cmd') if name in ('claude', 'codex') else None)
    monkeypatch.setattr(adapter, 'npm_dir', lambda: [])
    monkeypatch.setattr(claude_module, 'npm_dir', lambda: [])
    monkeypatch.setattr(codex, 'npm_dir', lambda: [])
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'none'))
    monkeypatch.setattr(claude_module.Path, 'home', staticmethod(lambda: tmp_path / 'home'))
    assert claude_module.find_executable() == str(claude_exe)
    assert codex.find_executable() == str(codex_exe)


def test_dispatch_package_has_no_gui_imports():
    for name in ('adapter', 'claude', 'codex'):
        source = (Path(__file__).parent.parent / 'agentdock' / 'dispatch' / f'{name}.py').read_text(encoding='utf-8')
        assert 'PySide6' not in source


@pytest.mark.parametrize('client', ['claude-code', 'codex', 'grok-cli'])
def test_chat_window_runs_other_agents(setup, client, ui_app):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QPushButton
    from agentdock.dispatch.projects import ProjectStore
    from agentdock.ui.chat_window import ChatWindow
    store, sender, notes, events, started, work, tmp = setup

    def pump(predicate):
        end = time.time() + 15
        while time.time() < end:
            ui_app.processEvents()
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError('condition not reached')

    class Bus(QObject):
        changed = Signal()
    bus = Bus()
    state, saved = {}, []
    win = ChatWindow(store, sender, ProjectStore(tmp, emit=bus.changed.emit), lambda: [], notify=notes.append,
                     ui_state=state, save_state=lambda: saved.append(1))
    bus.changed.connect(win.schedule_refresh)
    store.subscribe(lambda _e, _q: bus.changed.emit())
    win.resize(1100, 720)
    win.show()
    try:
        win.show_new()
        win.new_view.pick_agent(client)
        assert win.new_view.composer.send.isEnabled() and not win.new_view.error.isVisible()
        win.new_view.set_folder(str(work))
        win.new_view.composer.text.setPlainText('TOOL' if client == 'claude-code' else 'CMD')
        win.new_view.send()
        pump(lambda: win.current_key and win.conversation.permission_rows)
        key = win.current_key
        assert key.startswith(client + ':') and state['last_agent'] == client and saved
        assert store.dispatch.get(key)['owner'] == owner_for(None, client)   # no profile: the client's own owner
        allow = next(b for b in win.conversation.permission_rows[0].findChildren(QPushButton) if b.text() == '允許')
        allow.click()
        pump(lambda: store.dispatch.get(key)['state'] == 'done' and not win.conversation.permission_rows)
        pump(lambda: any(p.get('type') == 'tool' and p['status'] == 'completed'
                         for i in win.conversation.transcript.items if i['kind'] == 'assistant' for p in i['parts']))
        store.dispatch.offline = 'OpenCode 服務未執行。'   # OpenCode being offline says nothing about this agent
        win.refresh()
        pump(lambda: not win.conversation.status.isVisible())
        assert 'OpenCode' not in win.conversation.status.text()
        # Reopening reads the stored history.
        win.show_new()
        assert win.new_view.agent == client
        win.open_conversation(key)
        if client != 'claude-code':
            pump(lambda: not win.conversation.loading)
            kinds = [i['kind'] for i in win.conversation.transcript.items]
            assert kinds[0] == 'user' and 'assistant' in kinds
        assert notes == []
    finally:
        win.hide()
        win.deleteLater()


def test_grok_history_after_restart_replays_the_session(setup):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, 'grok-cli', 'CMD')
    wait(lambda: store.dispatch.get(key)['permissions'])
    sender.reply_permission(key, next(iter(store.dispatch.get(key)['permissions'])), 'once')
    wait(settled(store, key))
    # A new AgentDock (new agent process) opens the session with session/load and shows its replay.
    fresh = Dispatcher(store.dispatch, connect=lambda _s: None,
                       adapters=[GrokClient(find=lambda: [sys.executable, str(FAKES / 'fake_grok.py')])])
    got = []
    fresh.history(key, None, lambda messages, older: got.append(messages), failed=got.append)
    wait(lambda: got)
    items = Transcript(got[0]).items
    assert [i['kind'] for i in items] == ['user', 'assistant']
    assert items[0]['text'] == 'CMD'
    parts = items[1]['parts']
    assert [p['type'] for p in parts] == ['text', 'tool', 'text']
    assert parts[1]['name'] == 'run_terminal_command' and parts[1]['status'] == 'completed'
    assert parts[1]['input']['command'] == 'npm test' and parts[1]['output'] == 'tests passed'
    assert parts[2]['text'] == 'option allow-once'
    fresh.send(key, 'hello')
    wait(lambda: store.dispatch.get(key)['reply'] == 'Hello there' and store.dispatch.get(key)['state'] == 'done')
    assert [l['method'] for l in lines(tmp / 'grok.log') if l.get('method') == 'session/load'] == ['session/load']
    fresh.close()


def test_grok_record_numbers_text_between_tools():
    record, emitted = Record(), []
    meta = {'promptId': 'p1'}
    emit = lambda kind, **data: emitted.append((kind, data.get('ordinal'), data.get('text') or data.get('delta')))
    record.apply({'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'A'}}, meta, emit)
    record.apply({'sessionUpdate': 'tool_call', 'toolCallId': 't', 'title': 'Read file', 'kind': 'read',
                  'rawInput': {'variant': 'Read', 'path': 'x.py'}}, meta, emit)
    record.apply({'sessionUpdate': 'tool_call_update', 'toolCallId': 't', 'status': 'failed',
                  'content': [{'type': 'content', 'content': {'type': 'text', 'text': 'nope'}}]}, meta, emit)
    record.apply({'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': 'B'}}, meta, emit)
    record.apply({'sessionUpdate': 'agent_thought_chunk', 'content': {'type': 'text', 'text': 'hmm'}}, meta, emit)
    record.close_text('p1', emit)
    assert [e for e in emitted if e[0].startswith('session.text')] == [
        ('session.text.delta', 0, 'A'), ('session.text.ended', 0, 'A'),
        ('session.text.delta', 1, 'B'), ('session.text.ended', 1, 'B')]
    (message,) = record.visible()
    assert message['content'][1]['state']['input'] == {'path': 'x.py'}
    assert message['content'][1]['state']['error'] == {'message': 'nope'}
    assert all('ordinal' not in p for p in message['content'])


def test_find_grok_from_the_installer(tmp_path, monkeypatch):
    from agentdock.dispatch import grok
    exe = tmp_path / 'home' / '.grok' / 'bin' / 'grok.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MZ')
    monkeypatch.setattr(grok.Path, 'home', staticmethod(lambda: tmp_path / 'home'))
    monkeypatch.setattr(grok.shutil, 'which', lambda _name: None)
    monkeypatch.setattr(grok, 'npm_dir', lambda: [])
    assert grok.find_executable() == str(exe)
    exe.unlink()
    assert grok.find_executable() is None
