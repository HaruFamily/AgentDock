"""Conversation window: projects, transcript, history and the window against a fake OpenCode 2.x."""
import json
import os
import time

import pytest

from agentdock.dispatch.projects import UNSORTED, ProjectStore
from agentdock.dispatch.runner import Dispatcher
from agentdock.dispatch.opencode import OpenCodeService
from agentdock.dispatch.transcript import Transcript, item_from_message, tool_summary
from agentdock.qa.store import QuestionStore
from test_dispatch import OWNER, PASSWORD, FakeOpenCode

TARGETS = lambda: [{'label': 'OpenCode', 'client': 'opencode', 'owner': OWNER}]


@pytest.fixture
def fake():
    server = FakeOpenCode()
    yield server
    server.close()


# --------------------------------------------------------------------------- projects
def test_projects_validate_persist_and_match_folders(tmp_path):
    (tmp_path / 'repo' / 'sub').mkdir(parents=True)
    (tmp_path / 'other').mkdir()
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.add('x', str(tmp_path / 'missing'))
    with pytest.raises(ValueError):
        store.add('  ', str(tmp_path))
    outer = store.add('Outer', str(tmp_path))
    repo = store.add('Repo', str(tmp_path / 'repo'))
    assert ProjectStore(tmp_path).list() == store.list()
    # The deepest root that contains the folder wins.
    assert store.project_of({'directory': str(tmp_path / 'repo' / 'sub')}) == repo['id']
    assert store.project_of({'directory': str(tmp_path / 'other')}) == outer['id']
    assert store.project_of({'directory': str(tmp_path) + '-x'}) == UNSORTED
    # An explicit move beats the folder, and '' means unsorted.
    assert store.project_of({'directory': str(tmp_path / 'repo'), 'project': outer['id']}) == outer['id']
    assert store.project_of({'directory': str(tmp_path / 'repo'), 'project': ''}) == UNSORTED
    store.update(repo['id'], name='Renamed', root=str(tmp_path / 'other'))
    assert store.get(repo['id'])['root'] == os.path.normpath(str(tmp_path / 'other'))
    store.remove(outer['id'])
    assert store.project_of({'directory': str(tmp_path), 'project': outer['id']}) == UNSORTED


def test_explicit_move_survives_retrack(tmp_path):
    store = QuestionStore(tmp_path)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='t', project='p1')
    store.dispatch.set_project('opencode:ses_1', '')
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='t', state='unknown')
    assert QuestionStore(tmp_path).dispatch.get('opencode:ses_1')['project'] == ''


# --------------------------------------------------------------------------- transcript
STORED = [
    {'id': 'u1', 'type': 'user', 'text': '列出檔案'},
    {'id': 'a1', 'type': 'assistant', 'content': [
        {'type': 'tool', 'id': 'call_1', 'name': 'shell', 'state': {
            'status': 'completed', 'input': {'command': 'ls -la'}, 'content': [{'type': 'text', 'text': 'a.txt'}]}},
        {'type': 'text', 'text': '有 a.txt'}]},
    {'id': 'i1', 'type': 'idle', 'outcome': 'failed', 'error': {'type': 'provider.auth', 'message': 'HTTP 403'}},
]


def test_transcript_from_stored_messages():
    t = Transcript(STORED)
    assert [i['kind'] for i in t.items] == ['user', 'assistant', 'end']
    tool, text = t.items[1]['parts']
    assert (tool['name'], tool['status'], tool['output'], tool_summary(tool)) == ('shell', 'completed', 'a.txt', 'ls -la')
    assert (text['key'], text['text']) == ('text:0', '有 a.txt')
    assert t.items[2] == {'kind': 'end', 'id': 'i1', 'outcome': 'failed', 'error': 'HTTP 403'}
    assert item_from_message({'type': 'step', 'id': 'x'}) is None


def test_live_events_extend_without_duplicates():
    t = Transcript(STORED[:2])
    ev = lambda kind, **data: {'type': kind, 'data': {'sessionID': 's', **data}}
    assert t.apply(ev('session.inbox.enqueued', inboxID='u1', item={'type': 'user', 'payload': {'text': 'x'}})) is None
    user = t.apply(ev('session.inbox.enqueued', inboxID='u2', item={'type': 'user', 'payload': {'text': '再來'}}))
    assert user['text'] == '再來'
    # A text block streamed into an already stored message reuses its block (ordinal counts text only).
    t.apply(ev('session.text.ended', assistantMessageID='a1', ordinal=0, text='有 a.txt 和 b.txt'))
    assert [p['text'] for p in t.items[1]['parts'] if p['type'] == 'text'] == ['有 a.txt 和 b.txt']
    t.apply(ev('session.step.started', assistantMessageID='a2'))
    t.apply(ev('session.tool.input.started', assistantMessageID='a2', id='c2', name='read'))
    t.apply(ev('session.tool.called', assistantMessageID='a2', id='c2', input={'filePath': 'README.md'}))
    t.apply(ev('session.tool.failed', assistantMessageID='a2', id='c2', error={'message': 'denied'}))
    t.apply(ev('session.text.delta', assistantMessageID='a2', ordinal=0, delta='好'))
    t.apply(ev('session.text.delta', assistantMessageID='a2', ordinal=0, delta='的'))
    a2 = t.items[-1]
    assert [(p['type'], p.get('status'), p.get('text')) for p in a2['parts']] == [('tool', 'error', None), ('text', None, '好的')]
    assert tool_summary(a2['parts'][0]) == 'README.md' and a2['parts'][0]['error'] == 'denied'
    end = t.apply({'type': 'session.execution.interrupted', 'id': 'e1', 'data': {'sessionID': 's'}})
    assert end['outcome'] == 'interrupted' and t.apply(ev('session.unknown')) is None


def test_history_pages_oldest_first(tmp_path, fake):
    store = QuestionStore(tmp_path)
    sender = Dispatcher(store.dispatch, connect=lambda start: OpenCodeService(fake.url, PASSWORD, fake.version))
    fake.sessions['ses_h'] = {'id': 'ses_h', 'title': 'h'}
    fake.messages['ses_h'] = [{'id': 'm1', 'type': 'user', 'text': 'one'}, {'id': 'm2', 'type': 'user', 'text': 'two'}]
    store.dispatch.track('opencode', 'ses_h', owner=OWNER, title='h', state='done')
    got = []
    sender.history('opencode:ses_h', None, lambda messages, older: got.append((messages, older)))
    deadline = time.monotonic() + 15
    while not got and time.monotonic() < deadline:
        time.sleep(0.02)
    assert [m['id'] for m in got[0][0]] == ['m1', 'm2'] and got[0][1] is None


# --------------------------------------------------------------------------- window
def pump(app, predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    raise AssertionError('condition not reached')


@pytest.fixture
def window(tmp_path, fake, ui_app):
    from PySide6.QtCore import QObject, Signal
    from agentdock.ui.chat_window import ChatWindow

    class Bus(QObject):
        changed = Signal()
    bus = Bus()
    store = QuestionStore(tmp_path)
    notes = []
    sender = Dispatcher(store.dispatch, notify=notes.append,
                        connect=lambda start: OpenCodeService(fake.url, PASSWORD, fake.version))
    projects = ProjectStore(tmp_path, emit=bus.changed.emit)
    win = ChatWindow(store, sender, projects, TARGETS, notify=notes.append)
    bus.changed.connect(win.schedule_refresh)
    store.subscribe(lambda _e, _q: bus.changed.emit())
    win.resize(1100, 720)
    win.show()
    yield win, store, projects, notes, ui_app
    sender.close()
    win.hide()
    win.deleteLater()


def test_new_conversation_in_project_streams_into_the_window(window, tmp_path, fake):
    win, store, projects, notes, app = window
    project = projects.add('Repo', str(tmp_path))
    win.show_new(project['id'])
    view = win.new_view
    assert view.place.text() == 'Repo' and view.place.toolTip() == os.path.normpath(str(tmp_path))
    view.composer.text.setPlainText('整理 README')
    view.send()
    pump(app, lambda: win.pages.currentWidget() is win.conversation and win.current_key)
    key = win.current_key
    assert store.dispatch.get(key)['project'] == project['id']
    assert fake.sessions['ses_1']['location'] == {'directory': os.path.normpath(str(tmp_path))}
    pump(app, lambda: any(w.item['kind'] == 'assistant' and w.item['parts'] for w in win.conversation.widgets.values()))
    pump(app, lambda: store.dispatch.get(key)['state'] == 'done')
    texts = [p['text'] for i in win.conversation.transcript.items if i['kind'] == 'assistant' for p in i['parts']]
    assert texts == ['ok: 整理 README']
    assert [i['kind'] for i in win.conversation.transcript.items].count('user') == 1
    # Continue the conversation; Enter sends, Shift+Enter keeps writing.
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    box = win.conversation.composer.text
    box.setFocus()
    box.insertPlainText('補一段')   # QTest can only type ASCII
    QTest.keyClick(box, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    box.insertPlainText('安裝')
    assert box.toPlainText() == '補一段\n安裝'
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert box.toPlainText() == ''
    pump(app, lambda: any(p.get('text') == 'ok: 補一段\n安裝' for i in win.conversation.transcript.items
                          if i['kind'] == 'assistant' for p in i['parts']))
    assert notes == []


def test_reopening_loads_history_and_inline_permission(window, tmp_path, fake):
    win, store, projects, notes, app = window
    win.show_new()
    win.new_view.set_folder(str(tmp_path))
    win.new_view.composer.text.setPlainText('permission please')
    win.new_view.send()
    pump(app, lambda: win.current_key and store.dispatch.get(win.current_key)['permissions'])
    pump(app, lambda: win.conversation.permission_rows)
    key = win.current_key
    from PySide6.QtWidgets import QPushButton
    allow = next(b for b in win.conversation.permission_rows[0].findChildren(QPushButton) if b.text() == '允許')
    allow.click()
    pump(app, lambda: store.dispatch.get(key)['state'] == 'done')
    assert ('POST', 'api/session/ses_1/permission/per_1/reply', {'decision': 'once'}) in fake.calls
    pump(app, lambda: not win.conversation.permission_rows)
    win.show_new()
    win.open_conversation(key)
    pump(app, lambda: not win.conversation.loading)
    kinds = [i['kind'] for i in win.conversation.transcript.items]
    assert kinds == ['user', 'assistant', 'end'] and win.conversation.widgets
    assert not win.conversation.widgets[win.conversation.transcript.items[-1]['id']].isVisibleTo(win)


def test_drag_moves_between_projects_and_search_filters(window, tmp_path):
    win, store, projects, notes, app = window
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    a = projects.add('A', str(tmp_path / 'a'))
    b = projects.add('B', str(tmp_path / 'b'))
    store.dispatch.track('opencode', 'ses_x', owner=OWNER, title='修登入', directory=str(tmp_path / 'a'), state='done')
    store.dispatch.track('opencode', 'ses_y', owner=OWNER, title='寫測試', directory=str(tmp_path / 'z'), state='done')
    win.refresh()
    tree = win.sidebar.tree

    def grouping():
        out = {}
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            out[top.text(0)] = [top.child(j).text(0) for j in range(top.childCount())]
        return out
    assert grouping() == {'A': ['修登入'], 'B': [], '未分類': ['寫測試']}
    tree.moved.emit('opencode:ses_x', b['id'])
    tree.moved.emit('opencode:ses_y', a['id'])
    assert grouping() == {'A': ['寫測試'], 'B': ['修登入']}
    tree.moved.emit('opencode:ses_y', UNSORTED)
    assert grouping()['未分類'] == ['寫測試']
    win.sidebar.search.setText('登入')
    assert grouping() == {'A': [], 'B': ['修登入']}
    # The project page lists its conversations and changes go to the store.
    win.show_project(b['id'])
    assert win.project_view.root.text() == os.path.normpath(str(tmp_path / 'b'))
    win.project_view.name.setText('B2')
    win.project_view._rename()
    assert projects.get(b['id'])['name'] == 'B2'


def test_unsupported_agents_cannot_send(window, tmp_path, fake):
    win, store, projects, notes, app = window
    assert set(win.dispatcher.clients) == {'opencode', 'claude-code', 'codex', 'grok-cli'}
    win.dispatcher.clients = ('opencode',)   # e.g. a build without the other adapters
    win.show_new()
    win.new_view.pick_agent('codex')
    assert not win.new_view.composer.send.isEnabled() and win.new_view.error.text() == 'Codex 尚未支援。'
    win.new_view.composer.text.setPlainText('hello')
    win.new_view.send()
    assert fake.calls == []
    win.new_view.pick_agent('opencode')
    assert win.new_view.composer.send.isEnabled()


def test_rail_pages_and_list_toggle(window):
    win, store, projects, notes, app = window
    opened = []
    win.open_page = opened.append
    assert win.rail.width() == 56 and win.rail.buttons['chat'].toolTip() == '聊天'
    # 聊天 while chatting hides and shows the list; Ctrl+B does the same.
    win.rail.buttons['chat'].click()
    assert win.sidebar.isHidden() and win.rail.buttons['new'].isVisibleTo(win.rail)
    win.rail.buttons['new'].click()
    assert win.pages.currentWidget() is win.new_view and win.sidebar.isHidden()
    win.toggle_sidebar()
    assert not win.sidebar.isHidden() and not win.rail.buttons['new'].isVisibleTo(win.rail)
    win.rail.buttons['settings'].click()
    win.rail.buttons['tools'].click()
    assert opened == ['settings', 'tools']
    assert win.rail.buttons['settings'].y() > win.rail.buttons['tools'].y() + 200   # settings sits at the bottom


def test_new_project_dialog_and_pasted_files(window, tmp_path, monkeypatch):
    win, store, projects, notes, app = window
    from PySide6.QtCore import QMimeData, QUrl
    from agentdock.ui import chat_window
    monkeypatch.setattr(chat_window.ProjectDialog, 'exec', lambda self: (
        self.root.setText(str(tmp_path)), chat_window.QDialog.DialogCode.Accepted)[1])
    win.add_project()
    assert projects.list()[0]['name'] == os.path.basename(str(tmp_path))
    assert win.pages.currentWidget() is win.project_view
    (tmp_path / 'a b.txt').write_text('x', encoding='utf-8')
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(tmp_path / 'a b.txt'))])
    box = win.new_view.composer.text
    box.insertFromMimeData(data)
    # Pasted files become attachments of the message.
    assert box.toPlainText() == '' and win.new_view.composer.files == [os.path.normpath(str(tmp_path / 'a b.txt'))]
    assert not win.new_view.composer.chips.isHidden()
