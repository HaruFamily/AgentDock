"""Fork, importing sessions started elsewhere, context usage and the Git window."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from agentdock import gitops
from agentdock.dispatch.claude import fork_transcript, list_transcripts, read_history, rewind_point
from agentdock.dispatch.grok import Record
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.transcript import Transcript
from agentdock.qa.store import QuestionStore
from tests.test_dispatch_clients import OWNER, lines, settled, setup, start, wait  # noqa: F401  (fixture)

ENTRIES = [
    {'type': 'user', 'uuid': 'u1', 'parentUuid': None, 'sessionId': 'old', 'cwd': '/w', 'slug': 's',
     'message': {'role': 'user', 'content': 'first'}},
    {'type': 'assistant', 'uuid': 'a1', 'parentUuid': 'u1', 'sessionId': 'old',
     'message': {'id': 'm1', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}}]}},
    {'type': 'user', 'uuid': 'r1', 'parentUuid': 'a1', 'sessionId': 'old', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}},
    {'type': 'assistant', 'uuid': 'a2', 'parentUuid': 'r1', 'sessionId': 'old',
     'message': {'id': 'm2', 'content': [{'type': 'text', 'text': 'done one'}]}},
    {'type': 'assistant', 'uuid': 'side', 'parentUuid': 'a2', 'isSidechain': True, 'sessionId': 'old',
     'message': {'id': 'mx', 'content': [{'type': 'text', 'text': 'sub'}]}},
    {'type': 'user', 'uuid': 'u2', 'parentUuid': 'a2', 'sessionId': 'old', 'message': {'role': 'user', 'content': 'second'}},
    {'type': 'assistant', 'uuid': 'a3', 'parentUuid': 'u2', 'sessionId': 'old',
     'message': {'id': 'm3', 'content': [{'type': 'text', 'text': 'done two'}]}},
    {'type': 'custom-title', 'customTitle': 'Old name', 'sessionId': 'old'},
]


def write_transcript(folder: Path, sid: str = 'old') -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{sid}.jsonl'
    path.write_text('\n'.join(json.dumps(e) for e in ENTRIES) + '\n', encoding='utf-8')
    return path


def test_claude_fork_keeps_the_whole_turn_with_new_ids(tmp_path):
    path = write_transcript(tmp_path / 'p')
    # Forking at the tool call still keeps its result and the turn's final reply.
    new = fork_transcript(path, 'm1', 'Old name（分叉）')
    forked = [json.loads(l) for l in (tmp_path / 'p' / f'{new}.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [e['type'] for e in forked] == ['user', 'assistant', 'user', 'assistant', 'custom-title']
    assert forked[-1]['customTitle'] == 'Old name（分叉）'
    assert {e['sessionId'] for e in forked} == {new}
    assert not {e['uuid'] for e in forked} & {e.get('uuid') for e in ENTRIES}
    assert forked[0]['parentUuid'] is None
    assert [e['parentUuid'] for e in forked[1:4]] == [e['uuid'] for e in forked[:3]]   # the chain is kept
    assert forked[0]['forkedFrom'] == {'sessionId': 'old', 'messageUuid': 'u1'} and 'slug' not in forked[0]
    messages = read_history(tmp_path / 'p' / f'{new}.jsonl')
    assert [m['type'] for m in messages] == ['user', 'assistant', 'assistant']
    with pytest.raises(Exception):
        fork_transcript(path, 'nope', 'x')


def test_claude_lists_saved_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path))
    write_transcript(tmp_path / 'projects' / 'w')
    other = tmp_path / 'projects' / 'w' / 'plain.jsonl'
    other.write_text(json.dumps({'type': 'user', 'uuid': 'x', 'cwd': '/v',
                                 'message': {'content': 'just   a\nquestion'}}) + '\n', encoding='utf-8')
    found = {s['id']: s for s in list_transcripts()}
    assert found['old']['title'] == 'Old name' and found['old']['directory'] == '/w'
    assert found['plain']['title'] == 'just a question' and found['plain']['updated'] > 0


def test_grok_prompt_index():
    record = Record()
    record.messages = [{'type': 'user', 'id': 'u1'}, {'type': 'assistant', 'id': 'a1'},
                       {'type': 'user', 'id': 'u2'}, {'type': 'assistant', 'id': 'a2'}]
    assert record.prompt_index('a1') == 0 and record.prompt_index('a2') == 1 and record.prompt_index('u2') == 1


@pytest.mark.parametrize('client', ['codex', 'grok-cli'])
def test_fork_continues_in_a_new_conversation(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'one')
    wait(settled(store, key))
    sender.send(key, 'two')
    wait(lambda: store.dispatch.get(key)['prompt'] == 'two' and store.dispatch.get(key)['state'] == 'done')
    live = Transcript()
    for _k, event in [e for e in events if e[0] == key]:
        live.apply(event)
    first_reply = [i for i in live.items if i['kind'] == 'assistant'][0]['id']
    store.dispatch.rename(key, 'Mine')
    forks = []
    sender.fork(key, first_reply, done=forks.append)
    wait(lambda: forks)
    new = store.dispatch.get(forks[0])
    assert new['title'] == 'Mine（分叉）' and new['custom_title'] and new['directory'] == str(work)
    assert new['state'] == 'done' and new['client'] == client and forks[0] != key
    got = []
    sender.history(forks[0], None, lambda messages, older: got.append(messages), failed=got.append)
    wait(lambda: got)
    texts = [m.get('text') for m in got[0] if m['type'] == 'user']
    assert texts == ['one'], got[0]
    # The fork carries on by itself; the original keeps both turns.
    sender.send(forks[0], 'three')
    wait(lambda: store.dispatch.get(forks[0])['prompt'] == 'three' and store.dispatch.get(forks[0])['state'] == 'done')
    assert notes == []
    if client == 'grok-cli':
        fork = next(l['fork'] for l in lines(tmp / 'grok.log') if 'fork' in l)
        assert fork['targetPromptIndex'] == 0 and fork['sourceCwd'] == fork['newCwd'] == str(work)


@pytest.mark.parametrize('client', ['codex', 'grok-cli'])
def test_context_usage_is_recorded(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    if client == 'grok-cli':
        got = []
        sender.options(client, None, str(work), got.append)   # the model list carries the context size
        wait(lambda: got)
    key = start(setup, client, 'hello')
    wait(settled(store, key))
    wait(lambda: store.dispatch.get(key).get('context'))
    usage = store.dispatch.get(key)['context']
    assert usage == ({'used': 1200, 'limit': 10000} if client == 'codex' else {'used': 500, 'limit': 2000})


@pytest.mark.parametrize('client', ['codex', 'grok-cli'])
def test_import_lists_and_adopts(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'hello')
    wait(settled(store, key))
    sid = key.split(':', 1)[1]
    store.dispatch.remove(key)
    found = []
    sender.sessions(client, found.append)
    wait(lambda: found)
    session = next(s for s in found[0] if s['id'] == sid)
    assert session['tracked'] is False and session['directory'] == '/w' and session['updated'] > 0
    adopted = []
    sender.adopt(client, sid, OWNER, session['title'], directory=str(work), done=adopted.append)
    wait(lambda: adopted)
    row = store.dispatch.get(adopted[0])
    assert row['state'] == 'done' and row['title'] == session['title'] and row['directory'] == str(work)
    sender.send(adopted[0], 'again')
    wait(lambda: store.dispatch.get(adopted[0])['prompt'] == 'again' and store.dispatch.get(adopted[0])['state'] == 'done')
    again = []
    sender.sessions(client, again.append)
    wait(lambda: again)
    assert next(s for s in again[0] if s['id'] == sid)['tracked'] is True


# --------------------------------------------------------------------------- edit
def test_claude_rewind_point(tmp_path):
    path = write_transcript(tmp_path / 'p')
    assert rewind_point(path, 'u2') == 'a2'   # the end of the turn before it
    assert rewind_point(path, 'u1') is None   # nothing before the first message
    meta = [{'type': 'user', 'uuid': 'n1', 'parentUuid': 'a1', 'isMeta': True, 'message': {'content': 'note'}},
            {'type': 'user', 'uuid': 'u3', 'parentUuid': 'n1', 'message': {'content': 'third'}}]
    with open(path, 'a', encoding='utf-8') as f:
        f.write(''.join(json.dumps(e) + '\n' for e in meta))
    assert rewind_point(path, 'u3') == 'a1'   # a note sent with the message goes with it
    with pytest.raises(Exception):
        rewind_point(path, 'a2')


def user_texts(sender, key):
    got = []
    sender.history(key, None, lambda messages, older: got.append(messages), failed=got.append)
    wait(lambda: got)
    return [m.get('text') for m in got[0] if m['type'] == 'user']


@pytest.mark.parametrize('client', ['codex', 'grok-cli'])
def test_edit_replaces_the_message_in_place(setup, client):
    store, sender, notes, events, started, work, tmp = setup
    key = start(setup, client, 'one')
    wait(settled(store, key))
    sender.send(key, 'two')
    wait(lambda: store.dispatch.get(key)['prompt'] == 'two' and store.dispatch.get(key)['state'] == 'done')
    live = Transcript()
    for _k, event in [e for e in events if e[0] == key]:
        live.apply(event)
    second = [i for i in live.items if i['kind'] == 'user'][1]['id']   # the id it had while streaming
    sender.edit(key, second, 0, 'TWO')
    wait(lambda: store.dispatch.get(key)['prompt'] == 'TWO' and store.dispatch.get(key)['state'] == 'done')
    assert user_texts(sender, key) == ['one', 'TWO']
    sender.edit(key, 'gone', 1, 'ONE')   # the first message: the session is emptied, not replaced
    wait(lambda: store.dispatch.get(key)['prompt'] == 'ONE' and store.dispatch.get(key)['state'] == 'done')
    assert user_texts(sender, key) == ['ONE']
    assert notes == [] and list(store.dispatch.rows) == [key]
    if client == 'grok-cli':
        rewinds = [l['rewind'] for l in lines(tmp / 'grok.log') if 'rewind' in l]
        assert [r['targetPromptIndex'] for r in rewinds] == [1, 0]
        assert {r['mode'] for r in rewinds} == {'conversation_only'}


def test_claude_edit_resumes_before_the_message(setup, monkeypatch):
    store, sender, notes, events, started, work, tmp = setup
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp / 'claude-config'))
    write_transcript(tmp / 'claude-config' / 'projects' / 'w')
    store.dispatch.track('claude-code', 'old', owner=OWNER, title='Old', directory=str(work), state='done',
                         settings={'model': 'opus'})
    store.dispatch.rename('claude-code:old', 'Mine')
    store.dispatch.set_pinned('claude-code:old', True)
    sender.edit('claude-code:old', 'u2', 0, 'again')
    wait(lambda: store.dispatch.get('claude-code:old')['prompt'] == 'again'
         and store.dispatch.get('claude-code:old')['state'] == 'done')
    argv = next(l['argv'] for l in lines(tmp / 'claude.log') if 'argv' in l)
    assert argv[argv.index('--resume') + 1] == 'old' and argv[argv.index('--resume-session-at') + 1] == 'a2'
    # The first message has nothing to resume at: a new session takes the conversation's place.
    replaced = []
    sender.edit('claude-code:old', 'u1', 1, 'from the top', replaced=lambda old, new: replaced.append((old, new)))
    wait(lambda: replaced and store.dispatch.get(replaced[0][1])['state'] == 'done')
    old, new = replaced[0]
    row = store.dispatch.get(new)
    assert old == 'claude-code:old' and new != old and row['prompt'] == 'from the top'
    assert row['title'] == 'Mine' and row['custom_title'] and row.get('pinned') and row['model'] == 'opus'
    argv = [l['argv'] for l in lines(tmp / 'claude.log') if 'argv' in l][-1]
    assert '--session-id' in argv and '--resume-session-at' not in argv
    assert notes == []


# --------------------------------------------------------------------------- git
def git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.email', 't@t')
    git(root, 'config', 'user.name', 't')
    (root / 'a.txt').write_text('a\n')
    git(root, 'add', 'a.txt')
    git(root, 'commit', '-qm', 'init')
    return root


def test_git_status_stage_commit_and_branches(repo):
    root = gitops.repo_root(str(repo / '.'))
    assert os.path.samefile(root, repo)
    (repo / 'a.txt').write_text('a\nb\n')
    (repo / 'new file.txt').write_text('n\n')
    status = gitops.status(root)
    assert status.branch == 'main' and status.upstream == '' and status.summary == 'main · 2 個變更'
    changes = {c.path: c for c in status.changes}
    assert changes['a.txt'].unstaged and not changes['a.txt'].staged and changes['new file.txt'].index == '?'
    assert '+b' in gitops.diff(root, changes['a.txt'], staged=False)
    assert gitops.diff(root, changes['new file.txt'], staged=False) == '+n\n'
    gitops.stage(root, ['a.txt', 'new file.txt'])
    assert all(c.staged for c in gitops.status(root).changes)
    gitops.unstage(root, ['new file.txt'])
    assert {c.path: c.staged for c in gitops.status(root).changes} == {'a.txt': True, 'new file.txt': False}
    with pytest.raises(gitops.GitError):
        gitops.commit(root, '  ')
    assert gitops.commit(root, 'more').endswith(' more')
    gitops.discard(root, next(c for c in gitops.status(root).changes if c.path == 'new file.txt'))
    assert gitops.status(root).changes == []
    gitops.checkout(root, 'feature', create=True)
    assert gitops.status(root).branch == 'feature' and set(gitops.branches(root)) == {'main', 'feature'}
    with pytest.raises(gitops.GitError, match='origin'):
        gitops.push(root)
    assert [subject for _h, subject, _d in gitops.log(root)] == ['more', 'init']
    assert gitops.repo_root(str(repo.parent)) is None


def test_git_window_is_read_only(repo, ui_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from agentdock.ui.git_view import GitDialog
    (repo / 'a.txt').write_text('staged version\n')
    gitops.stage(str(repo), ['a.txt'])
    (repo / 'a.txt').write_text('worktree version\n')
    before = (repo / '.git' / 'index').read_bytes()
    head = gitops.run(str(repo), 'rev-parse', 'HEAD')
    dialog = GitDialog(str(repo))
    dialog.show()
    pump = lambda condition: wait_ui(ui_app, condition)
    pump(lambda: dialog.status is not None and dialog.busy == 0)
    assert dialog.branch.text() == 'main'
    for group, expected in [(0, 'staged version'), (1, 'worktree version')]:
        item = dialog.changes.topLevelItem(group).child(0)
        dialog.changes.setCurrentItem(item)
        pump(lambda: expected in dialog.diff.toPlainText() and dialog.busy == 0)
        QTest.mouseDClick(dialog.changes.viewport(), Qt.MouseButton.LeftButton,
                         pos=dialog.changes.visualItemRect(item).center())
    dialog.reload()
    pump(lambda: dialog.busy == 0)
    assert (repo / '.git' / 'index').read_bytes() == before
    assert gitops.run(str(repo), 'rev-parse', 'HEAD') == head
    assert (repo / 'a.txt').read_text() == 'worktree version\n'
    assert not any(b.text().startswith(('提交', '推送', '拉取', '暫存', '捨棄')) for b in dialog.findChildren(QPushButton))
    dialog.close()


def wait_ui(app, condition, timeout=10.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError('timed out')


def test_context_text():
    from agentdock.ui.chat_window import context_text
    assert context_text(None) == '' and context_text({'used': 0}) == ''
    assert context_text({'used': 50_000, 'limit': 200_000}) == '上下文 25%'
    assert context_text({'used': 12_345, 'limit': 0}) == '上下文 12k'
    assert context_text({'used': 1_500, 'limit': 0}) == '上下文 1.5k'


def test_git_window_counts_lines_and_numbers_the_diff(repo, ui_app):
    from agentdock.ui.git_view import diff_rows
    (repo / 'a.txt').write_text('changed\nmore\n')
    root = str(repo)
    assert gitops.numstat(root, False)['a.txt'][0] == 2
    assert gitops.numstat(root, True) == {}
    rows, added, removed = diff_rows('@@ -3,2 +3,3 @@ def f\n x\n-<old>\n+new\n+more\n', 'a.py')
    assert (added, removed) == (2, 1)
    assert '<td class="ln">3</td><td class="ln">3</td>' in rows       # context: both sides
    assert '<td class="ln">4</td><td class="ln"></td>' in rows        # removed: old side only
    assert '<td class="ln"></td><td class="ln">5</td>' in rows        # second added line
    assert '<old>' not in rows                                        # code is escaped


def test_opencode_fork_list_and_context(tmp_path):
    from tests.test_dispatch import FakeOpenCode, PASSWORD
    from agentdock.dispatch.opencode import OpenCodeService
    fake = FakeOpenCode()
    service = OpenCodeService(fake.url, PASSWORD)
    store = QuestionStore(tmp_path / 'data')
    sender = Dispatcher(store.dispatch, connect=lambda _s: service, adapters=[])
    fake.sessions['ses_1'] = {'id': 'ses_1', 'title': 'Hi', 'location': {'directory': '/w'}}
    fake.messages['ses_1'] = [
        {'id': 'u1', 'type': 'user', 'text': 'one'},
        {'id': 'a1', 'type': 'assistant', 'content': [], 'tokens': {'input': 10, 'output': 5, 'reasoning': 0,
                                                                     'cache': {'read': 100, 'write': 0}},
         'model': {'id': 'm', 'providerID': 'p'}},
        {'id': 'a1b', 'type': 'assistant', 'content': []},
        {'id': 'u2', 'type': 'user', 'text': 'two'},
        {'id': 'a2', 'type': 'assistant', 'content': []}]
    found = []
    sender.sessions('opencode', found.append)
    wait(lambda: found)
    assert found[0] == [{'id': 'ses_1', 'title': 'Hi', 'directory': '/w', 'updated': 1700000000.0, 'tracked': False}]
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Hi', directory='/w', state='done')
    forks = []
    sender.fork('opencode:ses_1', 'a1', done=forks.append)
    wait(lambda: forks)
    new_sid = forks[0].split(':', 1)[1]
    assert [m['id'] for m in fake.messages[new_sid]] == ['u1', 'a1', 'a1b']   # forked before the next prompt
    assert ('POST', 'api/session/ses_1/fork', {'before': 'u2'}) in fake.calls
    assert service.context('ses_1') == {'used': 115, 'model': 'p/m'}
    sender.close()
    fake.server.shutdown()



def test_opencode_edit_reverts_then_sends(tmp_path):
    from tests.test_dispatch import FakeOpenCode, PASSWORD
    from agentdock.dispatch.opencode import OpenCodeService
    fake = FakeOpenCode()
    service = OpenCodeService(fake.url, PASSWORD)
    store = QuestionStore(tmp_path / 'data')
    notes = []
    sender = Dispatcher(store.dispatch, notify=notes.append, connect=lambda _s: service, adapters=[])
    fake.sessions['ses_1'] = {'id': 'ses_1', 'title': 'Hi', 'location': {'directory': '/w'}}
    fake.messages['ses_1'] = [{'id': 'u1', 'type': 'user', 'text': 'one'}, {'id': 'a1', 'type': 'assistant', 'content': []},
                              {'id': 'u2', 'type': 'user', 'text': 'two'}, {'id': 'a2', 'type': 'assistant', 'content': []}]
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Hi', directory='/w', state='done')
    sender.edit('opencode:ses_1', 'live-id', 0, 'TWO')
    wait(lambda: any(c[1] == 'api/session/ses_1/prompt' for c in fake.calls))
    assert ('POST', 'api/session/ses_1/revert/stage', {'messageID': 'u2', 'files': False}) in fake.calls
    assert [m.get('text') for m in fake.messages['ses_1'] if m['type'] == 'user'] == ['one', 'TWO']
    assert notes == []
    sender.close()
    fake.server.shutdown()
