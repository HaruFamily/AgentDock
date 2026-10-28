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
from agentdock.ui.chat_common import hidden, ROLE
from test_dispatch import OWNER, PASSWORD, FakeOpenCode

TARGETS = lambda: [{'label': 'OpenCode', 'client': 'opencode', 'owner': OWNER}]


def test_qt_message_shows_image_attachment(ui_app, tmp_path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QLabel
    from agentdock.ui.chat_messages import MessageWidget

    path = tmp_path / 'picture.png'
    image = QImage(800, 600, QImage.Format.Format_RGB32)
    image.fill(0xff6633)
    assert image.save(str(path))
    widget = MessageWidget({'kind': 'user', 'id': 'u', 'text': '',
                            'files': ['picture.png', 'missing.txt']},
                           resolve=lambda name: str(tmp_path / name))
    labels = widget.findChildren(QLabel)
    pictures = [label for label in labels if label.pixmap() and not label.pixmap().isNull()]
    assert len(pictures) == 1
    assert pictures[0].pixmap().width() == 320
    assert pictures[0].pixmap().height() == 240
    assert any(label.text() == '附件：missing.txt' for label in labels)
    widget.close()


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
    store.dispatch.track('codex', 'ses_1', owner=OWNER, title='t', project='p1')
    store.dispatch.set_project('codex:ses_1', '')
    store.dispatch.track('codex', 'ses_1', owner=OWNER, title='t', state='unknown')
    assert QuestionStore(tmp_path).dispatch.get('codex:ses_1')['project'] == ''


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
    store.dispatch.track('codex', 'ses_x', owner=OWNER, title='修登入', directory=str(tmp_path / 'a'), state='done')
    store.dispatch.track('codex', 'ses_y', owner=OWNER, title='寫測試', directory=str(tmp_path / 'z'), state='done')
    win.refresh()
    tree = win.sidebar.tree

    def grouping():
        out = {}
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            out[top.text(0)] = [top.child(j).text(0) for j in range(top.childCount()) if top.child(j).data(0, ROLE)[0] == 'conv']
        return out
    assert grouping() == {'A': ['修登入'], 'B': [], '未分類': ['寫測試']}
    tree.moved.emit('codex:ses_x', b['id'])
    tree.moved.emit('codex:ses_y', a['id'])
    assert grouping() == {'A': ['寫測試'], 'B': ['修登入']}
    tree.moved.emit('codex:ses_y', UNSORTED)
    assert grouping()['未分類'] == ['寫測試']
    win.sidebar.search.setText('登入')
    assert grouping() == {'A': [], 'B': ['修登入']}
    # The project page lists its conversations and changes go to the store.
    win.show_project(b['id'])
    assert win.project_view.root_path == os.path.normpath(str(tmp_path / 'b'))
    win.project_view.name.setText('B2')
    win.project_view._rename()
    assert projects.get(b['id'])['name'] == 'B2'
    assert 'codex:ses_x' in win.sidebar.keys()
    # A draft choice changes the default only through the explicit action.
    win.project_view.starter.pick_agent('codex')
    assert projects.get(b['id'])['agent'] == 'opencode'
    win.project_view.starter.agent_chip.menu_.actions()[-1].trigger()
    assert projects.get(b['id'])['agent'] == 'codex'
    win.show_project(a['id'])
    assert win.project_view.starter.agent == projects.get(a['id'])['agent']
    assert win.project_view.starter is win.new_view
    assert win.new_view.place.text() == 'A'


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


def test_steps_of_one_answer_share_one_card(window, tmp_path):
    win, store, projects, notes, app = window
    store.dispatch.track('codex', 's9', owner='o', title='t', directory=str(tmp_path), state='done')
    win.open_conversation('codex:s9')
    view = win.conversation
    view.loading = True
    view.history_loaded('codex:s9', [
        {'id': 'u1', 'type': 'user', 'text': '問'},
        {'id': 'a0', 'type': 'assistant', 'content': [{'type': 'reasoning', 'text': ''}]},
        {'id': 'a1', 'type': 'assistant', 'content': [{'type': 'text', 'text': '先查一下'}]},
        {'id': 'a2', 'type': 'assistant', 'content': [{'type': 'tool', 'id': 'c', 'name': 'shell',
                                                        'state': {'status': 'completed', 'input': {'command': 'ls'}}}]},
        {'id': 'a3', 'type': 'assistant', 'content': [{'type': 'text', 'text': '答案'}]},
        {'id': 'u2', 'type': 'user', 'text': '再問'}], None)
    view._regroup()
    widgets = view.widgets
    first = widgets['a1']
    # Done: the remark and the tool fold into one line on top, a rule over the answer (as on the web page).
    assert '處理了 1 個步驟' in first.process.text() and all(hidden(p) for p in first.parts.values())
    assert hidden(widgets['a2']) and not hidden(widgets['a3'].rule)
    shown = [w for w in widgets.values() if w.item['kind'] == 'assistant' and not hidden(w)]
    assert shown == [first, widgets['a3']]                    # the empty thought takes no place at all
    assert [w.joined for w in shown] == [(False, True), (True, False)]
    assert first.copy_text() == '先查一下\n\n答案'             # copying takes the whole answer
    first.process.click()
    shown = [w for w in widgets.values() if w.item['kind'] == 'assistant' and not hidden(w)]
    assert len(shown) == 3 and not any(hidden(p) for p in first.parts.values())
    assert [w.joined for w in shown] == [(False, True), (True, True), (True, False)]
    assert all(w.joined == (False, False) for w in widgets.values() if w.item['kind'] == 'user')


def test_tools_in_a_row_fold_into_one_line(window, tmp_path):
    win, store, projects, notes, app = window
    from agentdock.ui.chat_window import ToolRow, tool_name
    store.dispatch.track('codex', 's8', owner='o', title='t', directory=str(tmp_path), state='done')
    win.open_conversation('codex:s8')
    view = win.conversation
    view.loading = True
    tool = lambda i: {'id': f'a{i}', 'type': 'assistant', 'content': [{'type': 'tool', 'id': f'c{i}', 'name': 'shell',
                                                                       'state': {'status': 'completed'}}]}
    view.history_loaded('codex:s8', [{'id': 'u1', 'type': 'user', 'text': '問'}, tool(1), tool(2), tool(3),
                                        {'id': 'a4', 'type': 'assistant', 'content': [{'type': 'text', 'text': '答'}]}],
                        None)
    view._regroup()
    view.widgets['a1'].process.click()                         # the finished answer's steps, opened
    rows = [w.parts[f'tool:c{i}'] for i in (1, 2, 3) for w in [view.widgets[f'a{i}']]]
    assert all(isinstance(r, ToolRow) for r in rows)
    assert rows[0].heads and '3 個工具' in rows[0].summary.text()
    assert all(r.folded for r in rows)
    assert not view.widgets['a1'].folded and view.widgets['a2'].folded and view.widgets['a3'].folded
    rows[0].summary.click()                                   # unfolds them all
    assert not any(r.folded for r in rows) and not view.widgets['a3'].folded
    assert tool_name('mcp__google-sheets__list_sheets') == 'google-sheets · list_sheets'


def test_changing_to_a_framed_look_keeps_the_window_maximized(window, monkeypatch):
    from PySide6.QtCore import Qt
    from agentdock.ui import theme
    win, store, projects, notes, app = window
    win.showMaximized()
    app.processEvents()
    assert win.isMaximized() and not win.frameless
    monkeypatch.setattr(theme, 'decorated', lambda name: name == 'titlebar')   # a look that draws its own bar
    win._frame_mode()
    app.processEvents()
    assert win.frameless and win.isMaximized() and win._border() == 0
    monkeypatch.setattr(theme, 'decorated', lambda name: False)
    win._frame_mode()
    app.processEvents()
    assert not win.frameless and win.isMaximized()
    # back to an opaque window: nothing left that makes Windows paint its background black
    assert not win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not win.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)


def test_editing_a_message_replaces_it_and_what_followed(window, tmp_path, fake):
    win, store, projects, notes, app = window
    win.show_new()
    win.new_view.set_folder(str(tmp_path))
    win.new_view.composer.text.setPlainText('one')
    win.new_view.send()
    pump(app, lambda: win.current_key and store.dispatch.get(win.current_key)['state'] == 'done')
    key, view = win.current_key, win.conversation
    view.composer.text.setPlainText('two')
    view.send()
    pump(app, lambda: store.dispatch.get(key)['prompt'] == 'two' and store.dispatch.get(key)['state'] == 'done')
    pump(app, lambda: [i['kind'] for i in view.transcript.items].count('assistant') == 2)
    second = [i for i in view.transcript.items if i['kind'] == 'user'][1]['id']
    message = view.widgets[second]
    from PySide6.QtWidgets import QPushButton
    edit = next(b for b in message.actions.findChildren(QPushButton) if b.text() == '編輯')
    edit.click()                                               # the bubble itself becomes the box
    assert message.editor is not None and message.editor_box.toPlainText() == 'two'
    assert message.bubble.isHidden() and view.composer.text.toPlainText() == ''
    next(b for b in message.editor.findChildren(QPushButton) if b.text() == '取消').click()
    assert message.editor is None and not message.bubble.isHidden()
    edit.click()
    message.editor_box.setPlainText('TWO')
    next(b for b in message.editor.findChildren(QPushButton) if b.text() == '送出').click()
    assert second not in view.widgets
    assert [i['text'] for i in view.transcript.items if i['kind'] == 'user'] == ['one']
    pump(app, lambda: any(p.get('text') == 'ok: TWO' for i in view.transcript.items if i['kind'] == 'assistant'
                          for p in i['parts']))
    assert [m['text'] for m in fake.messages['ses_1'] if m['type'] == 'user'] == ['one', 'TWO']
    assert [i['text'] for i in view.transcript.items if i['kind'] == 'user'] == ['one', 'TWO']
    assert notes == []


def test_thinking_is_open_while_live_and_folds_when_done(window, tmp_path):
    win, store, projects, notes, app = window
    from agentdock.ui.chat_messages import ReasoningRow
    store.dispatch.track('codex', 's7', owner='o', title='t', directory=str(tmp_path), state='running')
    win.open_conversation('codex:s7')
    view = win.conversation
    view.loading = True
    view.history_loaded('codex:s7', [
        {'id': 'u1', 'type': 'user', 'text': '問'},
        {'id': 'a1', 'type': 'assistant', 'content': [{'type': 'reasoning', 'text': '想一想'}]},
        {'id': 'a2', 'type': 'assistant', 'content': [{'type': 'text', 'text': '答案'}]}], None)
    view._regroup()
    think = next(p for p in view.widgets['a1'].parts.values() if isinstance(p, ReasoningRow))
    assert think.toggle.isChecked() and not hidden(think)    # live: every step shows, thinking open
    assert view.widgets['a1'].process is None
    store.dispatch.update('codex:s7', state='done')
    view.update_row()
    view._regroup()
    head = view.widgets['a1']
    assert '處理了 1 個步驟' in head.process.text() and not hidden(head.process)
    assert hidden(think) and not think.toggle.isChecked()     # folded away, and closed when opened again
    assert not hidden(view.widgets['a2'].rule)


def test_editing_keeps_the_message_where_it_is(window, tmp_path):
    win, store, projects, notes, app = window
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton
    store.dispatch.track('codex', 's6', owner='o', title='t', directory=str(tmp_path), state='done')
    win.open_conversation('codex:s6')
    view = win.conversation
    view.loading = True
    history = []
    for i in range(30):
        history += [{'id': f'u{i}', 'type': 'user', 'text': f'question {i}'},
                    {'id': f'a{i}', 'type': 'assistant', 'content': [{'type': 'text', 'text': 'answer ' * 40}]}]
    view.history_loaded('codex:s6', history, None)
    view._regroup()
    pump(app, lambda: view.scroll.verticalScrollBar().maximum() > 0)
    for target, value in (('u29', None), ('u12', 'middle')):   # at the bottom, then scrolled up
        bar = view.scroll.verticalScrollBar()
        app.processEvents()
        bar.setValue(bar.maximum() if value is None else bar.maximum() // 2)
        app.processEvents()
        message = view.widgets[target]
        if value is not None:   # bring the message into view first, as a reader would
            bar.setValue(bar.value() + message.mapTo(view.scroll.viewport(), QPoint(0, 0)).y() - 100)
            app.processEvents()
        top = message.mapTo(view.scroll.viewport(), QPoint(0, 0)).y()
        edit = next(b for b in message.actions.findChildren(QPushButton) if b.text() == '編輯')
        edit.click()
        for _ in range(20):
            app.processEvents()
        assert message.editor is not None
        assert abs(message.mapTo(view.scroll.viewport(), QPoint(0, 0)).y() - top) <= 2
        message.cancel_edit()
        for _ in range(20):
            app.processEvents()
        assert abs(message.mapTo(view.scroll.viewport(), QPoint(0, 0)).y() - top) <= 2


def test_project_drafts_and_hover_geometry(window, tmp_path):
    win, store, projects, notes, app = window
    a = projects.add('Alpha', str(tmp_path))
    (tmp_path / 'b').mkdir()
    b = projects.add('Beta', str(tmp_path / 'b'))
    win.show_new(a['id'])
    win.project_view.composer.text.setPlainText('draft A')
    win.show_new(b['id'])
    assert win.project_view.composer.text.toPlainText() == ''
    win.project_view.composer.text.setPlainText('draft B')
    win.show_new(a['id'])
    assert win.project_view.composer.text.toPlainText() == 'draft A'
    win.show_new()
    assert win.new_view.pid == a['id']
    win.show()
    app.processEvents()
    tree = win.sidebar.tree
    item = tree.topLevelItem(0)
    before = tree.visualItemRect(item)
    win.sidebar._hovered(item)
    app.processEvents()
    assert tree.visualItemRect(item) == before
    win.sidebar._unhover()
    app.processEvents()
    assert tree.visualItemRect(item) == before


def test_project_and_new_share_fixed_composer_and_draft(window, tmp_path):
    from PySide6.QtCore import QPoint
    win, store, projects, notes, app = window
    a = projects.add('Alpha', str(tmp_path))
    (tmp_path / 'beta').mkdir()
    b = projects.add('Beta', str(tmp_path / 'beta'))
    attachment = tmp_path / 'note.txt'
    attachment.write_text('attachment')
    win.show_new(a['id'])
    composer = win.new_view.composer
    composer.text.setPlainText('draft Alpha')
    composer.add_files([str(attachment)])
    app.processEvents()
    assert abs(composer.mapTo(win.workspace, composer.rect().center()).y() - win.workspace.height() / 2) < 30
    position = composer.mapTo(win, QPoint(0, 0))
    win.show_new()
    app.processEvents()
    assert composer is win.project_view.composer
    assert composer.mapTo(win, QPoint(0, 0)) == position
    assert abs(composer.mapTo(win.workspace, composer.rect().center()).y() - win.workspace.height() / 2) < 30
    assert composer.text.toPlainText() == 'draft Alpha'
    win.show_new(b['id'])
    assert not composer.text.toPlainText() and not composer.files
    composer.text.setPlainText('draft Beta')
    win.show_new(a['id'])
    assert composer.text.toPlainText() == 'draft Alpha'
    assert composer.files == [str(attachment)]
    win.show_new(a['id'])
    assert composer.text.toPlainText() == 'draft Alpha'
    assert win.sidebar.tree.currentItem().text(0) == 'Alpha'


def test_project_row_only_toggles_expansion(window, tmp_path):
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    win, store, projects, notes, app = window
    project = projects.add('Project', str(tmp_path))
    store.dispatch.track('codex', 'toggle', owner=OWNER, title='Conversation',
                         directory=str(tmp_path), state='done')
    win.refresh()
    tree = win.sidebar.tree
    item = tree.topLevelItem(0)
    page = win.pages.currentWidget()
    win.new_view.composer.text.setPlainText('keep this draft')
    app.processEvents()
    rect = tree.visualItemRect(item)
    point = QPoint(rect.left() + 40, rect.center().y())
    assert item.isExpanded()
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert not item.isExpanded()
    assert win.pages.currentWidget() is page
    assert win.new_view.composer.text.toPlainText() == 'keep this draft'
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert item.isExpanded()
    assert not tree.rootIsDecorated()


def test_project_settings_preserves_current_draft(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from agentdock.ui.chat_dialogs import ProjectView
    win, store, projects, notes, app = window
    a = projects.add('Alpha', str(tmp_path))
    (tmp_path / 'settings-beta').mkdir()
    b = projects.add('Beta', str(tmp_path / 'settings-beta'))
    win.show_new(a['id'])
    win.new_view.composer.text.setPlainText('Alpha draft')
    page = win.pages.currentWidget()
    win.project_settings(b['id'])
    assert win.pages.currentWidget() is win.project_view
    assert not win.compose_area.isVisible()
    details = win.project_view
    details.name.setText('Beta renamed')
    details._rename()
    assert projects.get(b['id'])['name'] == 'Beta renamed'
    assert win.new_view.pid == a['id']
    assert win.new_view.composer.text.toPlainText() == 'Alpha draft'


def test_empty_project_icon_color_and_direct_details(window, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    win, store, projects, notes, app = window
    p = projects.add('Empty', str(tmp_path))
    win.refresh()
    item = win.sidebar.tree.topLevelItem(0)
    assert item.child(0).text(0) == '沒有對話'
    assert win.sidebar.keys() == []
    assert not item.icon(0).isNull()
    win.sidebar._hovered(item)
    win.sidebar.hover_button.click()
    assert win.pages.currentWidget() is win.project_view
    assert win.project_view.pid == p['id']
    monkeypatch.setattr(QColorDialog, 'getColor', lambda *a: QColor('#4c82dd'))
    win.project_view.color_button.click()
    assert projects.get(p['id'])['color'] == '#4c82dd'
    assert ProjectStore(tmp_path).get(p['id'])['color'] == '#4c82dd'
    with pytest.raises(ValueError):
        projects.update(p['id'], color='invalid')


def test_project_page_direct_controls(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    win, store, projects, notes, app = window
    project = projects.add('Direct', str(tmp_path))
    win.project_settings(project['id'])
    view = win.project_view
    view.icon_buttons['tools'].click()
    assert ProjectStore(tmp_path).get(project['id'])['icon'] == 'tools'
    assert view.icon_buttons['tools'].isChecked()
    assert not view.icon_buttons['folder'].isChecked()
    replacement = tmp_path / 'replacement'
    replacement.mkdir()
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a: str(replacement))
    view.change_root.click()
    assert view.root_path == str(replacement)
    assert not hasattr(view, 'git')
    assert view.git_view.directory == str(replacement)
    with pytest.raises(ValueError):
        projects.update(project['id'], icon='unknown')
