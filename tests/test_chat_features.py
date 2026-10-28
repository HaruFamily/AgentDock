"""Chat window conveniences: list menu actions, delete, rename, keys, copy, retry, queued messages."""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentdock.dispatch.claude import ClaudeClient
from agentdock.dispatch.codex import CodexClient
from agentdock.dispatch.grok import GrokClient
from agentdock.dispatch.opencode import OpenCodeService
from agentdock.dispatch.projects import ProjectStore
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.store import owner_for
from agentdock.qa.store import QuestionStore
from test_dispatch import PASSWORD, FakeOpenCode  # noqa: F401  (fixture class)

FAKES = Path(__file__).parent / 'fakes'
OWNER = owner_for('p', 'claude-code')


def wait(condition, timeout=15.0, app=None):
    end = time.time() + timeout
    while time.time() < end:
        if app is not None:
            app.processEvents()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError('timed out')


def lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


# --------------------------------------------------------------------------- pure helpers
def test_split_code_keeps_text_and_fences_apart():
    from agentdock.ui.chat_window import split_code
    text = 'Intro\n\n```python\nprint(1)\n\nprint(2)\n```\nAfter\n~~~\nraw ``` inside\n~~~'
    assert split_code(text) == [('text', 'Intro\n'), ('code', 'python', 'print(1)\n\nprint(2)'), ('text', 'After'),
                                ('code', '', 'raw ``` inside')]
    # While streaming, an unclosed fence is already a code block.
    assert split_code('x\n```sh\nnpm te') == [('text', 'x'), ('code', 'sh', 'npm te')]
    assert split_code('plain') == [('text', 'plain')] and split_code('') == []


def test_when_is_short_and_local():
    from agentdock.ui.chat_window import when
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    stamp = lambda delta: (now - delta).isoformat().replace('+00:00', 'Z')
    assert when(stamp(timedelta(seconds=20)), now) == '剛剛'
    assert when(stamp(timedelta(minutes=5)), now) == '5 分'
    assert when(stamp(timedelta(days=400)), now).count('/') == 2
    assert when('nonsense', now) == ''


# --------------------------------------------------------------------------- store / dispatcher
def test_rename_survives_the_client_title(tmp_path):
    fake = FakeOpenCode()
    store = QuestionStore(tmp_path)
    sender = Dispatcher(store.dispatch, connect=lambda start: OpenCodeService(fake.url, PASSWORD, fake.version),
                        adapters=[])
    fake.sessions['ses_r'] = {'id': 'ses_r', 'title': 'from opencode', 'outcome': 'succeeded'}
    store.dispatch.track('opencode', 'ses_r', owner=OWNER, title='first', state='done')
    store.dispatch.rename('opencode:ses_r', '  我的   名字 ')
    sender._reconcile(OpenCodeService(fake.url, PASSWORD, fake.version), 'opencode:ses_r')
    row = QuestionStore(tmp_path).dispatch.get('opencode:ses_r')
    assert row['title'] == '我的 名字' and row['custom_title'] is True


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_CLAUDE_LOG', str(tmp_path / 'claude.log'))
    monkeypatch.setenv('FAKE_CODEX_LOG', str(tmp_path / 'codex.log'))
    monkeypatch.setenv('FAKE_GROK_LOG', str(tmp_path / 'grok.log'))
    monkeypatch.setenv('FAKE_GROK_STORE', str(tmp_path / 'grok-sessions.json'))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude-config'))
    work = tmp_path / 'work'
    work.mkdir()
    fake = FakeOpenCode()
    store = QuestionStore(tmp_path / 'data')
    notes, events = [], []
    sender = Dispatcher(store.dispatch, notify=notes.append,
                        connect=lambda start: OpenCodeService(fake.url, PASSWORD, fake.version), adapters=[
                            ClaudeClient(find=lambda: [sys.executable, str(FAKES / 'fake_claude.py')]),
                            CodexClient(find=lambda: [sys.executable, str(FAKES / 'fake_codex.py')]),
                            GrokClient(find=lambda: [sys.executable, str(FAKES / 'fake_grok.py')])])
    sender.listeners.append(lambda key, event: events.append((key, event)))
    yield store, sender, notes, events, work, tmp_path, fake
    sender.close()


def start(sender, work, client, text):
    started = []
    sender.start_task(str(work), text, OWNER, started=started.append, client=client)
    wait(lambda: started)
    return started[0]


def done(store, key):
    return lambda: store.dispatch.get(key)['state'] in ('done', 'failed', 'interrupted')


@pytest.mark.parametrize('client', ['claude-code', 'grok-cli'])
def test_messages_sent_while_running_queue_up(clients, client):
    store, sender, notes, events, work, tmp, fake = clients
    key = start(sender, work, client, 'hello')
    sender.send(key, 'again')   # before the first reply has finished
    wait(lambda: sum(e['type'] == 'session.execution.succeeded' for k, e in events) == 2)
    kinds = [e['type'] for _k, e in events if e['type'].startswith('session.execution')]
    # The second turn is reported as running again after the first one ends.
    assert kinds == ['session.execution.started', 'session.execution.succeeded',
                     'session.execution.started', 'session.execution.succeeded']
    wait(lambda: store.dispatch.get(key)['state'] == 'done')
    assert notes == []


def test_codex_message_while_running_steers_the_turn(clients):
    store, sender, notes, events, work, tmp, fake = clients
    key = start(sender, work, 'codex', 'SLOW')
    wait(lambda: any(e['type'] == 'session.execution.started' for _k, e in events))
    sender.send(key, 'use tabs')
    wait(done(store, key))
    row = store.dispatch.get(key)
    assert row['state'] == 'done' and row['reply'] == 'steered use tabs' and notes == []
    log = lines(tmp / 'codex.log')
    assert [l['method'] for l in log if l.get('method') in ('turn/start', 'turn/steer')] == ['turn/start', 'turn/steer']
    assert {'steer_expected': True} in log
    users = [e['data']['item']['payload']['text'] for _k, e in events if e['type'] == 'session.inbox.enqueued']
    assert users == ['SLOW', 'use tabs']


# --------------------------------------------------------------------------- window
@pytest.fixture
def window(clients, ui_app, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from agentdock.ui.chat_window import ChatWindow
    store, sender, notes, events, work, tmp, fake = clients

    class Bus(QObject):
        changed = Signal()
    bus = Bus()
    projects = ProjectStore(tmp, emit=bus.changed.emit)
    state, saved = {}, []

    def make(ui=None):
        win = ChatWindow(store, sender, projects, lambda: [], notify=notes.append, ui_state=state if ui is None else ui,
                         save_state=lambda: saved.append(1))
        bus.changed.connect(win.schedule_refresh)
        store.subscribe(lambda _e, _q: bus.changed.emit())
        win.resize(1100, 720)
        return win
    win = make()
    win.show()
    yield win, make, store, sender, notes, work, projects, state, ui_app
    win.hide()
    win.deleteLater()


def test_list_actions_rename_move_delete(window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from agentdock.ui import chat_window as cw
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'claude-code', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    wait(lambda: not win.conversation.loading, app=app)
    # F2 renames.
    monkeypatch.setattr(cw.RenameDialog, 'exec', lambda self: (self.name.setText('新名字'), cw.QDialog.DialogCode.Accepted)[1])
    win.sidebar.refresh()
    item = next(win.sidebar.tree.topLevelItem(0).child(i) for i in range(win.sidebar.tree.topLevelItem(0).childCount()))
    win.sidebar.tree.setCurrentItem(item)
    QTest.keyClick(win.sidebar.tree, Qt.Key.Key_F2)
    assert store.dispatch.get(key)['title'] == '新名字'
    wait(lambda: win.conversation.title.text() == '新名字', app=app)
    # Moving through the menu's action is the same as dropping.
    project = projects.add('Repo', str(work.parent))
    win.move_conversation(key, '')
    assert store.dispatch.get(key)['project'] == ''
    win.move_conversation(key, project['id'])
    assert projects.project_of(store.dispatch.get(key)) == project['id']
    # Delete: it leaves the list at once; 復原 brings it back, otherwise it goes for good.
    win.open_conversation(key)
    win.delete_conversation(key)
    assert key not in win.rows() and store.dispatch.get(key) is not None
    assert win.undo_bar.isVisible() and win.pages.currentWidget() is win.new_view
    win.undo_delete()
    assert key in win.rows() and win.current_key == key and not win.undo_bar.isVisible()
    win.delete_conversation(key)
    win._delete_timer.timeout.emit()
    assert store.dispatch.get(key) is None and win.pages.currentWidget() is win.new_view
    assert 'last_conversation' not in state and notes == [] and not win.undo_bar.isVisible()


def test_keyboard_new_step_stop_search(window):
    win, make, store, sender, notes, work, projects, state, app = window
    first = start(sender, work, 'grok-cli', 'hello')
    wait(done(store, first), app=app)
    second = start(sender, work, 'codex', 'SLOW')
    wait(lambda: store.dispatch.get(second)['state'] == 'running', app=app)
    win.open_conversation(second)
    # Esc stops the open conversation.
    win.escape()
    wait(done(store, second), app=app)
    assert store.dispatch.get(second)['state'] == 'interrupted'
    # Alt+Up/Down follow the list order (newest first).
    win.sidebar.refresh()
    order = win.sidebar.keys()
    assert set(order) == {first, second}
    win.open_conversation(order[0])
    win.step_conversation(1)
    assert win.current_key == order[1]
    win.step_conversation(1)
    assert win.current_key == order[1]   # stays at the end
    # Ctrl+N starts next to the open conversation: same folder, same agent.
    win.open_conversation(first)
    win.new_like_current()
    assert win.pages.currentWidget() is win.new_view and win.new_view.agent == 'grok-cli'
    assert win.new_view.folder_path == str(work) and win.new_view.place.text() == work.name
    win.focus_search()
    win.sidebar.search.setText('zzz')
    win.escape()
    assert win.sidebar.search.text() == ''


def test_reopening_returns_to_the_last_conversation(window):
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'grok-cli', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    assert state['last_conversation'] == key
    again = make()
    assert again.pages.currentWidget() is again.new_view   # nothing loads while hidden (tray start)
    again.show()
    assert again.current_key == key and again.pages.currentWidget() is again.conversation
    again.hide()
    again.deleteLater()


def test_copy_edit_and_retry(window):
    from PySide6.QtWidgets import QApplication, QPushButton
    from agentdock.ui.chat_window import CodeBlock
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'claude-code', 'FAIL now')
    win.open_conversation(key)
    wait(lambda: store.dispatch.get(key)['state'] == 'failed', app=app)
    wait(lambda: any(w.retry is not None and w.retry.isVisibleTo(win) for w in win.conversation.widgets.values()),
         app=app)
    user = next(w for w in win.conversation.widgets.values() if w.item['kind'] == 'user')
    buttons = {b.text(): b for b in user.actions.findChildren(QPushButton)}
    buttons['複製'].click()
    assert QApplication.clipboard().text() == 'FAIL now'
    buttons['編輯'].click()   # edited in the bubble itself, not in the box below
    assert user.editor_box.toPlainText() == 'FAIL now' and win.conversation.composer.text.toPlainText() == ''
    user.cancel_edit()
    # Retry sends the last message again.
    retry = next(w.retry for w in win.conversation.widgets.values() if w.retry is not None and w.retry.isVisibleTo(win))
    retry.click()
    wait(lambda: sum(1 for l in lines(Path(store.dispatch.file).parent.parent / 'claude.log')
                     if l.get('type') == 'user') == 2, app=app)
    wait(lambda: store.dispatch.get(key)['state'] == 'failed', app=app)
    # Code in a reply has its own copy button.
    key2 = start(sender, work, 'claude-code', 'hello')
    win.open_conversation(key2)
    wait(done(store, key2), app=app)
    wait(lambda: any(w.item['kind'] == 'assistant' and w.parts for w in win.conversation.widgets.values()), app=app)
    assistant = next(w for w in win.conversation.widgets.values() if w.item['kind'] == 'assistant')
    part = next(iter(assistant.parts.values()))
    part.set_text('Run:\n```sh\nnpm test\n```')
    block = part.findChild(CodeBlock)
    assert block is not None and block.language.text() == 'sh'
    block.copy.click()
    assert QApplication.clipboard().text() == 'npm test' and block.copy.text() == '已複製'
    part.set_text('Run:\n```sh\nnpm test --watch\n```')
    assert part.findChild(CodeBlock) is block and block.code == 'npm test --watch'   # updated in place
    assistant_buttons = {b.text(): b for b in assistant.actions.findChildren(QPushButton)}
    assert set(assistant_buttons) == {'複製', '分叉'}


# --------------------------------------------------------------------------- batch 2
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c63f8cfc0f00f0004850180848a8c21'
                    '0000000049454e44ae426082')


def test_settings_bar_follows_the_model(ui_app):
    from agentdock.ui.chat_window import SettingsBar
    bar = SettingsBar()
    changes = []
    bar.changed.connect(lambda f, v: changes.append((f, v)))
    options = {'models': [{'id': 'a', 'label': 'A', 'efforts': ['low', 'high']}, {'id': 'b', 'label': 'B', 'efforts': []}],
               'modes': [{'id': 'plan', 'label': '計畫'}]}
    bar.set_options(options, {'model': 'a', 'effort': 'high', 'mode': ''})
    assert bar.values() == {'model': 'a', 'effort': 'high', 'mode': ''} and changes == []
    assert bar.efforts() == ['low', 'high'] and bar.model_chip.text() == 'A' and bar.effort_chip.text() == '高'
    assert [a.text() for a in bar.model_chip.menu_.actions()] == ['預設模型', 'A', 'B']
    assert [a.text() for a in bar.effort_chip.menu_.actions()] == ['預設推理', '低', '高']
    bar.pick('model', 'b')
    assert ('model', 'b') in changes and ('effort', '') in changes and bar.efforts() == []   # B has no efforts
    assert bar.model_chip.text() == 'B' and bar.effort_chip.isHidden()
    bar.set_options(options, {'mode': 'plan'}, mode_locked=True)
    assert not bar.mode_chip.isEnabled() and bar.values()['mode'] == 'plan' and bar.mode_chip.text() == '計畫'
    bar.set_options({}, {})
    assert bar.model_chip.isHidden() and bar.effort_chip.isHidden() and bar.mode_chip.isHidden()


def test_new_conversation_sends_settings_and_attachments(window, tmp_path):
    win, make, store, sender, notes, work, projects, state, app = window
    (work / 'shot.png').write_bytes(PNG)
    (work / 'notes.txt').write_text('n', encoding='utf-8')
    store.dispatch.remember_options('claude-code', {'models': [{'id': 'sonnet', 'label': 'Sonnet', 'efforts': ['low']}],
                                                     'modes': [{'id': 'plan', 'label': '計畫'}]})
    win.show_new()
    view = win.new_view
    view.set_folder(str(work))   # not 只聊天, whose cautious mode would already show 計畫
    view.pick_agent('claude-code')
    bar = view.composer.settings
    bar.pick('model', 'sonnet')
    bar.pick('effort', 'low')
    bar.pick('mode', 'plan')
    assert state['new_settings']['claude-code'] == {'model': 'sonnet', 'effort': 'low', 'mode': 'plan'}
    view.composer.add_files([str(work / 'shot.png'), str(work / 'notes.txt'), str(work / 'missing.txt')])
    assert [Path(f).name for f in view.composer.files] == ['shot.png', 'notes.txt']
    view.composer.text.setPlainText('look')
    view.send()
    wait(lambda: win.current_key and store.dispatch.get(win.current_key)['state'] == 'done', app=app)
    row = store.dispatch.get(win.current_key)
    assert (row['model'], row['effort'], row['mode']) == ('sonnet', 'low', 'plan')
    log = lines(Path(store.dispatch.file).parent.parent / 'claude.log')
    argv = next(l['argv'] for l in log if 'argv' in l and '--session-id' in l['argv'])
    assert argv[argv.index('--model') + 1] == 'sonnet' and argv[argv.index('--effort') + 1] == 'low'
    assert argv[argv.index('--permission-mode') + 1] == 'plan'
    content = next(l for l in log if l.get('type') == 'user')['message']['content']
    assert content[0] == {'type': 'text', 'text': f"look\n\n附件：{work / 'notes.txt'}"}
    assert content[1]['type'] == 'image' and content[1]['source']['media_type'] == 'image/png'
    # The fake writes no transcript file: the user's line shows only when its live event came after the
    # window opened the conversation, so check it when it is there.
    user = next((w for w in win.conversation.widgets.values() if w.item['kind'] == 'user'), None)
    assert user is None or user.item['files'] == ['shot.png', 'notes.txt']
    assert not view.composer.files
    # The same Agent starts with the same choices next time.
    win.show_new()
    view.pick_agent('claude-code')
    assert view.composer.settings.values() == {'model': 'sonnet', 'effort': 'low', 'mode': 'plan'}


def test_changing_settings_in_a_conversation(window):
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'claude-code', 'hello')
    wait(done(store, key), app=app)
    store.dispatch.remember_options('claude-code', {'models': [{'id': 'haiku', 'label': 'Haiku', 'efforts': ['high']}],
                                                     'modes': [{'id': 'acceptEdits', 'label': '自動接受編輯'}]})
    win.open_conversation(key)
    bar = win.conversation.composer.settings
    wait(lambda: not bar.model_chip.isHidden(), app=app)
    bar.pick('model', 'haiku')
    bar.pick('mode', 'acceptEdits')
    wait(lambda: store.dispatch.get(key).get('mode') == 'acceptEdits', app=app)
    log_file = Path(store.dispatch.file).parent.parent / 'claude.log'
    wait(lambda: sum(l.get('type') == 'control_request' and l['request']['subtype'] in ('set_model', 'set_permission_mode')
                     for l in lines(log_file)) == 2, app=app)
    assert store.dispatch.get(key)['model'] == 'haiku' and notes == []


def test_codex_options_and_compact(window):
    win, make, store, sender, notes, work, projects, state, app = window
    got = []
    sender.options('codex', None, str(work), got.append)
    wait(lambda: got)
    assert got[-1]['models'] == [{'id': 'gpt-x', 'label': 'GPT X', 'efforts': ['low', 'high']}]   # hidden left out
    assert store.dispatch.options['codex']['models'][0]['id'] == 'gpt-x'
    key = start(sender, work, 'codex', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    win.conversation.composer.text.setPlainText('/compact')
    win.conversation.send()
    tmp = Path(store.dispatch.file).parent.parent
    wait(lambda: any(l.get('method') == 'thread/compact/start' for l in lines(tmp / 'codex.log')), app=app)
    wait(lambda: store.dispatch.get(key)['state'] == 'done', app=app)
    assert not any(l.get('method') == 'turn/start' and l['params']['input'][0]['text'] == '/compact'
                   for l in lines(tmp / 'codex.log'))


def test_grok_settings_at_start(clients):
    store, sender, notes, events, work, tmp, fake = clients
    started = []
    sender.start_task(str(work), 'hello', OWNER, started=started.append, client='grok-cli',
                      settings={'model': 'grok-b', 'effort': 'low', 'mode': 'auto'})
    wait(lambda: started and store.dispatch.get(started[0])['state'] == 'done')
    log = lines(tmp / 'grok.log')
    new = next(l for l in log if l.get('method') == 'session/new')['params']
    assert new['_meta'] == {'autoMode': True}
    configs = [l['config'] for l in log if 'config' in l]
    assert [(c['configId'], c['value']) for c in configs] == [('model', 'grok-b'), ('reasoning_effort', 'low')]
    got = []
    sender.options('grok-cli', None, str(work), got.append)
    wait(lambda: got)
    assert got[-1]['models'][0] == {'id': 'grok-a', 'label': 'Grok A', 'efforts': ['low', 'high'], 'context': 2000}
    assert got[-1]['mode_fixed'] is True


def test_find_reasoning_diff_and_content_search(window):
    from agentdock.ui.chat_window import MessageWidget, diff_of
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'grok-cli', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    wait(lambda: any(w.item['kind'] == 'assistant' and w.parts for w in win.conversation.widgets.values()), app=app)
    # Ctrl+F finds text in the loaded messages.
    win.find_in_conversation()
    find = win.conversation.find
    assert find.isVisible()
    find.query.setText('there')
    assert find.count.text() == '1/1'
    find.query.setText('zzz')
    assert find.count.text() == '0'
    win.escape()
    assert not find.isVisible()
    # The list search also looks at the conversation's text.
    win.sidebar.search.setText('Hello there')
    assert win.sidebar.keys() == [key]
    win.sidebar.search.setText('nothing like this')
    assert win.sidebar.keys() == []
    win.sidebar.search.clear()
    # Thinking folds away; edits show as a diff.
    widget = MessageWidget({'kind': 'assistant', 'id': 'm', 'error': '', 'parts': [
        {'type': 'reasoning', 'key': 'reasoning:0', 'text': 'pondering'},
        {'type': 'tool', 'key': 'tool:t', 'name': 'Edit', 'status': 'completed', 'output': '', 'error': '', 'diff': '',
         'input': {'file_path': 'a.py', 'old_string': 'x = 1', 'new_string': 'x = 2'}}]})
    reasoning, tool = widget.parts['reasoning:0'], widget.parts['tool:t']
    assert reasoning.toggle.text() == '▸ 思考過程' and reasoning.text.isHidden()
    assert '-x = 1' in tool.diff_text and '+x = 2' in tool.diff_text
    assert 'old_string' not in tool.detail.text()
    assert diff_of({'diff': '--- a\n+++ a\n+y'}) == '--- a\n+++ a\n+y'
    widget.deleteLater()


def test_pasted_image_is_saved_and_attached(window, tmp_path):
    from PySide6.QtGui import QImage
    win, make, store, sender, notes, work, projects, state, app = window
    composer = win.new_view.composer
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xff0000)
    composer.add_image(image)
    assert len(composer.files) == 1 and composer.files[0].endswith('.png')
    assert Path(composer.files[0]).parent == Path(store.dispatch.file).parent / 'attachments' / 'chat'
    composer.remove_file(composer.files[0])
    assert composer.files == [] and composer.chips.isHidden()


def test_fork_context_and_import_in_the_window(window, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from agentdock.ui import chat_window as cw
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'codex', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    view = win.conversation
    wait(lambda: not view.loading and view.context_chip.isVisibleTo(win), app=app)
    assert view.context_chip.text() == '上下文 12%'
    # Fork from the reply: the new conversation opens.
    reply = next(w for w in view.widgets.values() if w.item['kind'] == 'assistant')
    buttons = {b.text(): b for b in reply.actions.findChildren(QPushButton)}
    buttons['分叉'].click()
    wait(lambda: win.current_key not in ('', key), app=app)
    assert store.dispatch.get(win.current_key)['title'].endswith('（分叉）')
    wait(lambda: not view.loading and any(w.item['kind'] == 'user' for w in view.widgets.values()), app=app)
    # Import lists the agent's sessions; tracked ones cannot be picked again.
    dialog = cw.ImportDialog(win, None, 'codex')
    wait(lambda: dialog.list.topLevelItemCount() == 2, app=app)
    assert {dialog.list.topLevelItem(i).text(2) for i in range(2)} == {'已在 AgentDock'}
    store.dispatch.remove(key)
    dialog.load()
    wait(lambda: any(dialog.list.topLevelItem(i).text(2) != '已在 AgentDock'
                     for i in range(dialog.list.topLevelItemCount())), app=app)
    item = next(dialog.list.topLevelItem(i) for i in range(dialog.list.topLevelItemCount())
                if dialog.list.topLevelItem(i).text(2) != '已在 AgentDock')
    item.setSelected(True)
    dialog.adopt()
    wait(lambda: win.current_key == key and store.dispatch.get(key) is not None, app=app)
    assert notes == []


def test_conversation_shows_its_agents_quota(window):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QLabel
    from agentdock.tools.tokengauge import gauge
    win, make, store, sender, notes, work, projects, state, app = window

    class Quota(QObject):
        changed = Signal()
        busy, last = False, None
        results = [gauge.ProviderResult('codex', 'Codex', [gauge.Account('a', [gauge.Window('5h', 30, None),
                                                                              gauge.Window('week', 60, None)])])]

        def visible_results(self):
            return self.results
    quota = Quota()
    win.set_quota(quota)
    view = win.conversation
    key = start(sender, work, 'codex', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    wait(lambda: view.quota.isVisibleTo(win), app=app)
    assert view.quota.shown == 1
    # Every window is listed, not only the tightest.
    assert len([l for l in view.quota.findChildren(QLabel) if l.objectName() == 'CardKind' and l.isVisibleTo(view.quota)]) == 2
    # Narrow: under the message box; wide: in the corner beside it.
    win.resize(1000, 720)
    wait(lambda: view.quota.parentWidget() is view.quota_slot, app=app)
    win.resize(1900, 900)
    wait(lambda: view.quota.parentWidget() is view, app=app)
    assert view.quota.geometry().left() > view.composer.geometry().right()
    # A new look redraws it in place; it must not be measured empty (0 high, so unseen).
    win.refresh_theme()
    assert view.quota.isVisibleTo(win) and view.quota.height() > 20
    # An Agent without quota data shows nothing.
    other = start(sender, work, 'grok-cli', 'hello')
    wait(done(store, other), app=app)
    win.open_conversation(other)
    wait(lambda: not view.quota.isVisibleTo(win), app=app)


def test_reopened_conversation_shows_at_once_then_refreshes(window, monkeypatch):
    win, make, store, sender, notes, work, projects, state, app = window
    first = start(sender, work, 'codex', 'hello')
    wait(done(store, first), app=app)
    second = start(sender, work, 'grok-cli', 'hello')
    wait(done(store, second), app=app)
    view = win.conversation
    win.open_conversation(first)
    wait(lambda: not view.loading and view.transcript.items, app=app)
    shown = [i['id'] for i in view.transcript.items]
    win.open_conversation(second)
    wait(lambda: not view.loading, app=app)
    # Back to the first: on screen before the client answers, then the client's copy is checked.
    win.open_conversation(first)
    assert [i['id'] for i in view.transcript.items] == shown and view.refreshing is not None
    assert view.status.isHidden()
    wait(lambda: view.refreshing is None and not view.loading, app=app)
    assert [i['id'] for i in view.transcript.items] == shown
    # A changed copy replaces what was cached.
    messages, older = view.history_cache[first]
    view.history_cache[first] = (messages[:1], older)
    win.open_conversation(second)
    win.open_conversation(first)
    assert len(view.transcript.items) < len(shown)
    wait(lambda: view.refreshing is None and [i['id'] for i in view.transcript.items] == shown, app=app)


def test_a_message_that_did_not_get_out_comes_back(window, monkeypatch):
    from agentdock.dispatch.adapter import ClientError
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'codex', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    view = win.conversation
    wait(lambda: not view.loading, app=app)

    def refuse(*_args, **_kw):
        raise ClientError('沒有連線')
    monkeypatch.setattr(sender.adapters['codex'], 'prompt', refuse)
    view.composer.text.setPlainText('a long message')
    view.send()
    assert view.composer.text.toPlainText() == ''
    wait(lambda: view.composer.text.toPlainText() == 'a long message', app=app)
    assert any('沒有連線' in n for n in notes)


def test_running_turn_shows_its_time(window):
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'codex', 'SLOW')
    wait(lambda: store.dispatch.get(key)['state'] == 'running', app=app)
    win.open_conversation(key)
    view = win.conversation
    win.refresh()
    win.run_started[key] = time.monotonic() - 83
    wait(lambda: not view.loading, app=app)
    view._tick()
    assert '1:23' in view.status.text() or '1:24' in view.status.text()
    sender.interrupt(key)
    wait(done(store, key), app=app)
    win.refresh()
    assert key not in win.run_started
