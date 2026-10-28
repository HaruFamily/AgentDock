"""Desktop-app handling: the new-conversation page, round send/stop, list toggle and pins, typing helpers,
the Ctrl+K palette and the jump to the latest message."""
from pathlib import Path

from agentdock.qa.store import QuestionStore
from test_chat_features import clients, done, start, wait, window  # noqa: F401  (fixtures)


def keys(widget, *pairs):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    for key in pairs:
        QTest.keyClick(widget, key, Qt.KeyboardModifier.NoModifier)


# --------------------------------------------------------------------------- store
def test_pins_and_given_names_survive_a_new_start(tmp_path):
    store = QuestionStore(tmp_path)
    store.dispatch.track('codex', 'ses_1', owner='o', title='first')
    store.dispatch.rename('codex:ses_1', 'Mine')
    store.dispatch.set_pinned('codex:ses_1', True)
    store.dispatch.track('codex', 'ses_1', owner='o', title='client title')
    row = QuestionStore(tmp_path).dispatch.get('codex:ses_1')
    assert row['pinned'] and row['title'] == 'Mine'
    store.dispatch.set_pinned('codex:ses_1', False)
    assert 'pinned' not in store.dispatch.get('codex:ses_1')


# --------------------------------------------------------------------------- new conversation page
def test_new_page_picks_place_and_agent_from_chips(window, tmp_path):
    win, make, store, sender, notes, work, projects, state, app = window
    other = tmp_path / 'other'
    other.mkdir()
    project = projects.add('Repo', str(work))
    win.show_new()
    view = win.new_view
    view.set_folder(str(other))
    assert view.place.text() == 'other' and view.project() is None
    menu = [a.text() for a in view.place.menu_.actions()]
    # Folders come only from projects; an older conversation's own folder is shown while it is in use.
    assert menu[:3] == ['只聊天', '專案', 'Repo'] and 'other' in menu and menu[-1] == '新增專案…'
    assert '選擇資料夾…' not in menu
    next(a for a in view.place.menu_.actions() if a.text() == 'Repo').trigger()
    assert view.project()['id'] == project['id'] and view.place.text() == 'Repo'
    next(a for a in view.agent_chip.menu_.actions() if a.text() == 'Codex').trigger()
    assert view.agent == 'codex' and view.agent_chip.text() == 'Codex'
    view.composer.text.setPlainText('hello')
    view.send()
    wait(lambda: win.current_key and done(store, win.current_key)(), app=app)
    key = win.current_key
    row = store.dispatch.get(key)
    assert row['client'] == 'codex' and row['project'] == project['id']
    # History is available only in the sidebar.
    win.show_new()
    assert key in win.sidebar.keys()
    assert not hasattr(view, 'recent')
    win.open_conversation(key)
    assert win.pages.currentWidget() is win.conversation


def test_one_round_button_sends_or_stops(window):
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'codex', 'SLOW')
    wait(lambda: store.dispatch.get(key)['state'] == 'running', app=app)
    win.open_conversation(key)
    composer = win.conversation.composer
    wait(lambda: composer.running, app=app)
    assert composer.stop.isVisibleTo(composer) and not composer.send.isVisibleTo(composer)
    composer.text.setPlainText('also this')
    assert composer.send.isVisibleTo(composer) and not composer.stop.isVisibleTo(composer)
    composer.text.clear()
    composer.stop.click()
    wait(done(store, key), app=app)
    wait(lambda: not composer.running, app=app)
    assert composer.send.isVisibleTo(composer) and not composer.stop.isVisibleTo(composer)


# --------------------------------------------------------------------------- list
def test_pinned_group_and_row_more_button(window, monkeypatch):
    from agentdock.ui.chat_window import Sidebar
    win, make, store, sender, notes, work, projects, state, app = window
    first = start(sender, work, 'claude-code', 'one')
    wait(done(store, first), app=app)
    second = start(sender, work, 'claude-code', 'two')
    wait(done(store, second), app=app)
    store.dispatch.set_pinned(first, True)
    win.sidebar.refresh()
    top = win.sidebar.tree.topLevelItem(0)
    assert top.text(0) == '釘選' and top.child(0).data(0, 0x0100)[1] == first
    assert win.sidebar.keys()[0] == first
    shown = []
    monkeypatch.setattr(Sidebar, 'show_menu', lambda self, menu, _w: shown.append([a.text() for a in menu.actions()]))
    tree = win.sidebar.tree
    app.processEvents()
    top = tree.topLevelItem(0)  # queued store changes may rebuild the tree
    widths = [tree.columnWidth(i) for i in range(3)]
    title_rect = tree.visualItemRect(top.child(0))
    badge = top.child(0).text(1)
    win.sidebar._hovered(top.child(0))
    app.processEvents()
    button = win.sidebar.hover_button
    assert button is not None and top.child(0).text(1) == badge
    assert [tree.columnWidth(i) for i in range(3)] == widths
    assert tree.visualItemRect(top.child(0)) == title_rect
    button.click()
    assert shown and shown[0][0] == '取消釘選'
    win.sidebar._unhover()
    app.processEvents()
    assert top.child(0).text(1) == badge
    assert [tree.columnWidth(i) for i in range(3)] == widths
    assert top.text(1) == ''


def test_sidebar_width_is_remembered(window):
    win, make, store, sender, notes, work, projects, state, app = window
    win.split.setSizes([360, 700])
    win.split.splitterMoved.emit(360, 1)
    assert state['sidebar_width'] == win.sidebar.width() >= 300
    again = make()
    again.show()
    assert abs(again.sidebar.width() - state['sidebar_width']) <= 2
    again.hide()


# --------------------------------------------------------------------------- typing
def test_up_recalls_and_slash_and_at_suggest(window):
    from PySide6.QtCore import Qt
    win, make, store, sender, notes, work, projects, state, app = window
    (work / 'src').mkdir()
    (work / 'src' / 'main.py').write_text('x', encoding='utf-8')
    (work / 'node_modules').mkdir()
    (work / 'node_modules' / 'main.js').write_text('x', encoding='utf-8')
    key = start(sender, work, 'claude-code', 'first message')
    win.open_conversation(key)
    wait(done(store, key), app=app)
    composer = win.conversation.composer
    wait(lambda: win.conversation._last_user_text() == 'first message', app=app)
    wait(lambda: any(c['name'] == 'review' for c in composer.commands), app=app)   # learned from Claude Code
    composer.text.setFocus()
    keys(composer.text, Qt.Key.Key_Up)
    assert composer.text.toPlainText() == 'first message'
    composer.text.clear()
    composer.text.insertPlainText('/re')
    assert composer.suggest.isVisible() and composer.suggest.value() == '/review '
    keys(composer.text, Qt.Key.Key_Tab)
    assert composer.text.toPlainText() == '/review ' and composer.suggest.isHidden()
    composer.text.clear()
    composer.text.insertPlainText('/')
    assert [composer.suggest.item(i).text().split()[0] for i in range(composer.suggest.count())][:2] == \
        ['/compact', '/review']
    composer.text.clear()
    composer.text.insertPlainText('look at @mai')
    values = [composer.suggest.item(i).data(0x0100) for i in range(composer.suggest.count())]
    assert values == ['@src/main.py ']   # node_modules is skipped
    keys(composer.text, Qt.Key.Key_Return)   # Enter picks, does not send
    assert composer.text.toPlainText() == 'look at @src/main.py '
    assert store.dispatch.get(key)['prompt'] == 'first message'


def test_files_dropped_anywhere_go_to_the_box(window):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    win, make, store, sender, notes, work, projects, state, app = window
    (work / 'a.txt').write_text('a', encoding='utf-8')
    win.show_new()
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(work / 'a.txt'))])
    event = QDropEvent(QPointF(400, 100), Qt.DropAction.CopyAction, data, Qt.MouseButton.NoButton,
                       Qt.KeyboardModifier.NoModifier)
    win.dropEvent(event)
    assert [Path(f).name for f in win.new_view.composer.files] == ['a.txt']


# --------------------------------------------------------------------------- palette and scrolling
def test_ctrl_k_palette_finds_and_opens(window):
    from PySide6.QtCore import Qt
    win, make, store, sender, notes, work, projects, state, app = window
    first = start(sender, work, 'claude-code', 'alpha topic')
    wait(done(store, first), app=app)
    second = start(sender, work, 'claude-code', 'beta topic')
    wait(done(store, second), app=app)
    win.show_new()
    win.palette_.open()
    assert win.palette_.isVisible() and win.palette_.results.count() == 2
    win.palette_.query.setText('alpha')
    assert win.palette_.results.count() == 1
    keys(win.palette_.query, Qt.Key.Key_Return)
    assert win.current_key == first and win.palette_.isHidden()
    win.palette_.open()
    win.escape()
    assert win.palette_.isHidden()


def test_jump_to_latest_appears_when_reading_back(window):
    win, make, store, sender, notes, work, projects, state, app = window
    key = start(sender, work, 'claude-code', 'hello')
    wait(done(store, key), app=app)
    win.open_conversation(key)
    view = win.conversation
    view.content.setMinimumHeight(4000)
    wait(lambda: view.scroll.verticalScrollBar().maximum() > 1000, app=app)
    view.to_latest()
    app.processEvents()
    assert view.latest.isHidden()
    view.scroll.verticalScrollBar().setValue(0)
    assert view.latest.isVisible()
    view.latest.click()
    app.processEvents()
    bar = view.scroll.verticalScrollBar()
    assert bar.value() == bar.maximum() and view.latest.isHidden()


# --------------------------------------------------------------------------- Claude's models and the modes
def test_claude_lists_models_before_any_conversation(window, monkeypatch):
    win, make, store, sender, notes, work, projects, state, app = window
    monkeypatch.setenv('FAKE_CLAUDE_MODELS', '1')
    sender.adapters['claude-code']._probed = False   # the window's first page (Claude) already asked once
    win.show_new(agent='claude-code', folder=str(work))
    bar = win.new_view.composer.settings
    wait(lambda: not bar.model_chip.isHidden(), app=app)
    options = store.dispatch.options['claude-code']
    assert [m['id'] for m in options['models']] == ['opus', 'haiku']
    assert options['defaults'] == {'model': 'opus', 'effort': 'high'}   # what Claude Code really uses
    wait(lambda: bar.model_chip.text() == 'Opus 9' and bar.effort_chip.text() == '高', app=app)
    assert [a.text() for a in bar.model_chip.menu_.actions()] == ['Opus 9', 'Haiku 9']   # no separate 預設 entry
    assert win.new_view.agent_chip.text() == 'Claude'
    assert not list((work.parent / 'claude-config').glob('projects/*/*.jsonl'))   # nothing was started


def test_mode_menu_uses_the_agents_own_default():
    from agentdock.ui.chat_window import SettingsBar
    bar = SettingsBar()
    modes = [{'id': 'default', 'label': '詢問'}, {'id': 'plan', 'label': '計畫'}]
    bar.set_options({'modes': modes, 'mode_default': 'default'}, {})
    assert bar.mode_chip.text() == '詢問' and bar.values()['mode'] == ''
    menu = [(a.text(), a.isChecked()) for a in bar.mode_chip.menu_.actions()]
    assert menu == [('詢問', True), ('計畫', False)]   # no separate 預設權限
    bar.pick('mode', 'plan')
    assert bar.mode_chip.text() == '計畫'
    bar.set_options({'modes': modes}, {})
    assert [a.text() for a in bar.mode_chip.menu_.actions()][0] == '預設權限'


def test_claude_model_names_carry_the_version():
    from agentdock.dispatch.claude import ClaudeClient
    from agentdock.ui.chat_window import SettingsBar
    client = ClaudeClient(find=lambda: None)
    got = []
    client.remember = got.append
    init = {'models': [
        {'value': 'default', 'resolvedModel': 'claude-opus-5-5', 'displayName': 'Default (recommended)',
         'description': 'Use the default model (currently Opus 5.5) · $4/$20 per Mtok', 'supportedEffortLevels': ['low']},
        {'value': 'opus', 'resolvedModel': 'claude-opus-5-5', 'displayName': 'Opus',
         'description': 'Opus 5.5 · Best for everyday, complex tasks', 'supportedEffortLevels': ['low', 'medium', 'high']},
        {'value': 'custom', 'displayName': 'Custom', 'description': 'Something else entirely'}]}
    client.learned(init)
    options = got[-1]
    assert [m['label'] for m in options['models']] == ['Opus 5.5', 'Custom'] and options['defaults'] == {'model': 'opus'}
    bar = SettingsBar()
    bar.set_options(options, {})
    assert bar.model_chip.text() == 'Opus 5.5' and bar.values()['model'] == ''
    assert [(a.text(), a.isChecked()) for a in bar.model_chip.menu_.actions()] == [('Opus 5.5', True), ('Custom', False)]
    assert bar.effort_chip.text() == '預設推理'   # effort not known yet: Claude Code's get_settings tells it
    client.learned(init, {'model': 'claude-opus-5-5', 'effort': 'medium'})
    bar.set_options(got[-1], {})
    assert bar.effort_chip.text() == '中' and [a.text() for a in bar.effort_chip.menu_.actions()] == ['低', '中', '高']
    bar.pick('model', 'custom')
    assert bar.model_chip.text() == 'Custom' and bar.effort_chip.isHidden()

# --------------------------------------------------------------------------- the local look's decorations
def test_local_look_paints_parts_of_the_chat_window(window, monkeypatch):
    import types
    from agentdock.ui import theme
    win, make, store, sender, notes, work, projects, state, app = window
    assert not theme.decorated('panel')   # nothing local: the plain look
    calls = []

    def hook(name):
        return lambda painter, rect, *args: calls.append(name)
    local = types.SimpleNamespace(**{f'draw_{n}': hook(n) for n in ('canvas', 'panel', 'box', 'mascot')})
    local.draw_box = lambda *_a: (_ for _ in ()).throw(RuntimeError('broken'))   # a failing hook never breaks painting
    monkeypatch.setattr(theme, 'PRIVATE', local)
    monkeypatch.setattr(theme, 'CUTE', True)
    win.refresh_theme()
    win.show_new()
    win.repaint()
    app.processEvents()
    assert {'canvas', 'panel', 'mascot'} <= set(calls)
    assert win.sidebar.layout().contentsMargins().top() == 40
    assert 'background: transparent; border-right: none' in win.styleSheet()


def test_rounding_stays_within_half_the_height(window):
    import re
    win, make, store, sender, notes, work, projects, state, app = window
    css = win.styleSheet()
    radius = lambda name: int(re.search(rf'#{name} {{[^}}]*border-radius: (\d+)px', css).group(1))
    assert radius('UserBubble') * 2 <= 36 and radius('ToolRow') * 2 <= 28


def test_dialogs_lose_the_square_system_frame(ui_app, monkeypatch):
    import types
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QDialog, QMessageBox, QVBoxLayout, QLabel
    from agentdock.ui import cute, theme
    dresser = cute.install(ui_app)
    try:
        painted = []
        monkeypatch.setattr(theme, 'CUTE', True)
        monkeypatch.setattr(theme, 'PRIVATE', types.SimpleNamespace(
            draw_dialog=lambda p, r, title, close, hovered: painted.append(title)))
        dialog = QDialog()
        dialog.setWindowTitle('刪除對話')
        QVBoxLayout(dialog).addWidget(QLabel('x'))
        dialog.show()
        dialog.repaint()
        ui_app.processEvents()
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint and '刪除對話' in painted
        assert dialog.layout().contentsMargins().top() >= cute.MARGINS[1]
        assert not dialog.mask().isEmpty()   # cut to its rounded shape: no black corners on Windows
        closed = []
        dialog.rejected.connect(lambda: closed.append(1))
        from PySide6.QtTest import QTest
        QTest.mouseClick(dialog, Qt.MouseButton.LeftButton, pos=cute.close_rect(dialog).center())
        assert closed
        box = QMessageBox(QMessageBox.Icon.Question, 't', 'q', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.show()
        ui_app.processEvents()
        assert box.button(QMessageBox.StandardButton.Yes).text() == '是' and box.button(QMessageBox.StandardButton.No).text() == '否'
        from PySide6.QtWidgets import QDialogButtonBox
        asking = QDialog()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        QVBoxLayout(asking).addWidget(buttons)
        asking.show()
        ui_app.processEvents()
        assert [b.text() for b in buttons.buttons()] == ['確定', '取消']
        asking.hide()
        box.hide()
        # Without the local hook the system frame stays.
        monkeypatch.setattr(theme, 'PRIVATE', None)
        plain = QDialog()
        plain.show()
        ui_app.processEvents()
        assert not plain.windowFlags() & Qt.WindowType.FramelessWindowHint
        plain.hide()
    finally:
        ui_app.removeEventFilter(dresser)


def test_window_draws_its_own_title_bar(window, monkeypatch):
    import types
    from PySide6.QtCore import QPoint, Qt
    from agentdock.ui import theme
    win, make, store, sender, notes, work, projects, state, app = window
    assert not win.frameless and win.titlebar.isHidden()
    seen = []
    monkeypatch.setattr(theme, 'CUTE', True)
    monkeypatch.setattr(theme, 'PRIVATE', types.SimpleNamespace(
        draw_titlebar=lambda p, r, title, buttons, hovered, maximized: seen.append((title, sorted(buttons))),
        draw_canvas=lambda p, r, border: seen.append(('canvas', border))))
    win.refresh_theme()
    app.processEvents()
    win.repaint()
    assert win.frameless and win.isVisible() and not win.titlebar.isHidden()
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert ('AgentDock', ['close', 'max', 'min']) in seen and ('canvas', win.BORDER) in seen
    assert win.rail.y() == win.BORDER + win.titlebar.height()
    assert win._edges(QPoint(1, 1)) == Qt.Edge.LeftEdge | Qt.Edge.TopEdge and not win._edges(QPoint(300, 300))
    closed = []
    win.closed.connect(lambda: closed.append(1))
    win.titlebar.act('close')
    assert closed and not win.isVisible()
    # Back to the system frame with the plain look.
    monkeypatch.setattr(theme, 'PRIVATE', None)
    win.show()
    win.refresh_theme()
    assert not win.frameless and win.titlebar.isHidden() and win.rail.y() == 0


def test_codex_and_grok_report_their_real_defaults(window):
    win, make, store, sender, notes, work, projects, state, app = window
    for client, expected in (('codex', {'model': 'gpt-x', 'effort': {'gpt-x': 'high'}}),
                             ('grok-cli', {'model': 'grok-a', 'effort': {}})):
        got = []
        sender.options(client, None, str(work), got.append)
        wait(lambda: got and got[-1].get('defaults'), app=app)
        assert got[-1]['defaults'] == expected
    win.show_new(agent='codex', folder=str(work))
    bar = win.new_view.composer.settings
    wait(lambda: bar.model_chip.text() == 'GPT X', app=app)
    assert bar.effort_chip.text() == '高' and bar.values() == {'model': '', 'effort': '', 'mode': ''}


def test_chat_only_uses_an_empty_folder_and_a_cautious_mode(window):
    win, make, store, sender, notes, work, projects, state, app = window
    store.dispatch.remember_options('codex', {'models': [], 'modes': [{'id': 'read-only', 'label': '唯讀'},
                                                                       {'id': 'auto', 'label': '自動'}], 'mode_default': 'auto'})
    win.show_new(agent='codex', folder=str(work))
    view = win.new_view
    assert view.composer.settings.mode_chip.text() == '自動'
    next(a for a in view.place.menu_.actions() if a.text() == '只聊天').trigger()
    assert view.chat_only() and view.place.text() == '只聊天'
    assert view.composer.settings.mode_chip.text() == '唯讀'   # shown before sending
    view.composer.text.setPlainText('hi')
    view.send()
    wait(lambda: win.current_key and done(store, win.current_key)(), app=app)
    row = store.dispatch.get(win.current_key)
    assert win.is_chat_dir(row['directory']) and (Path(store.dispatch.file).parent / 'chat').is_dir()
    assert row['mode'] == 'read-only' and 'mode' not in (state.get('new_settings') or {}).get('codex', {})
    wait(lambda: win.conversation.project_chip.text() == '只聊天', app=app)
    # A new conversation without a project is 只聊天; a folder (an older conversation's) drops the cautious mode.
    win.show_new()
    assert view.chat_only()   # the last place is kept
    assert str(win.chat_dir()) not in [a.text() for a in view.place.menu_.actions()]
    view.set_folder(str(work))
    assert view.composer.settings.mode_chip.text() == '自動'


def test_a_new_conversation_without_a_project_just_talks(window, monkeypatch):
    from agentdock.ui import chat_window as cw
    win, make, store, sender, notes, work, projects, state, app = window
    fresh = make(ui={})
    fresh.show_new()
    view = fresh.new_view
    assert view.chat_only() and view.place.text() == '只聊天'
    menu = [a.text() for a in view.place.menu_.actions()]
    assert menu == ['只聊天', '', '新增專案…']   # no folders of its own; folders come with projects
    # 新增專案… makes the project and the new conversation goes there, without leaving the page.
    monkeypatch.setattr(cw.ProjectDialog, 'exec', lambda self: (self.root.setText(str(work)), self.name.setText('Work'),
                                                             cw.QDialog.DialogCode.Accepted)[2])
    view._new_project()
    assert view.project()['name'] == 'Work' and fresh.pages.currentWidget() is view
    fresh.hide()


def test_settings_lists_only_the_agents_the_chat_talks_to(tmp_path):
    import json
    from agentdock.agents import RETIRED_KINDS, AgentManager
    (tmp_path / 'c.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'd.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'agents.json').write_text(json.dumps([
        {'id': 'a', 'name': 'Claude Code', 'kind': 'claude-code', 'path': str(tmp_path / 'c.json')},
        {'id': 'b', 'name': 'Claude Desktop / Cowork', 'kind': 'claude-desktop', 'path': str(tmp_path / 'd.json')}]),
        encoding='utf-8')
    m = AgentManager(tmp_path)
    m.hidden_kinds = RETIRED_KINDS
    m.rename_old_defaults()
    assert [(p.name, p.kind) for p in m.list()] == [('Claude', 'claude-code')]
    assert [r['kind'] for r in m.inspect()] == ['claude-code']
    m.reorder(['a'])   # saving keeps the hidden registration as it was
    saved = json.loads((tmp_path / 'agents.json').read_text(encoding='utf-8'))
    assert [(d['name'], d['kind']) for d in saved] == [('Claude', 'claude-code'), ('Claude Desktop / Cowork', 'claude-desktop')]


def test_agent_facts_say_model_account_and_program():
    from agentdock.agent_info import AgentFacts, account_text, model_text
    from agentdock.tools.tokengauge.gauge import Account, ProviderResult
    options = {'models': [{'id': 'opus', 'label': 'Opus 5.5'}], 'defaults': {'model': 'opus', 'effort': {'opus': 'xhigh'}}}
    assert model_text(options) == 'Opus 5.5 · 很高'
    assert model_text({'models': [], 'defaults': {'model': 'x/y'}}) == 'x/y'
    assert model_text({}) == ''
    assert account_text(ProviderResult('claude', 'Claude', [Account('Pin', plan='Max 20x')])) == ('Pin · Max 20x', False)
    assert account_text(ProviderResult('claude', 'Claude', [Account(None)])) == ('已登入', False)
    assert account_text(ProviderResult('claude', 'Claude', error='需要重新登入')) == ('需要重新登入', True)
    assert account_text(ProviderResult('grok', 'Grok', missing=True)) == ('', False)
    facts = AgentFacts(lambda kind: options if kind == 'claude-code' else {},
                       lambda key: ProviderResult(key, key, [Account('Pin')]) if key == 'claude' else None)
    facts.versions = {'claude-code': '2.1.288', 'codex': None}
    assert facts('claude-code') == {'program': '2.1.288', 'model': 'Opus 5.5 · 很高', 'account': 'Pin', 'account_problem': False}
    assert facts('codex') == {'program': None}
    assert facts('grok-cli') == {}   # version not looked up yet: nothing claimed


def test_program_version_reads_the_first_version(tmp_path):
    import sys
    from agentdock.agent_info import program_version
    script = tmp_path / 'fake.py'
    script.write_text("print('2.1.288 (Claude Code)')", encoding='utf-8')
    exe = tmp_path / 'fake'
    exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{script}"\n', encoding='utf-8')
    exe.chmod(0o755)
    if sys.platform != 'win32':
        assert program_version(str(exe)) == '2.1.288'
    assert program_version(str(tmp_path / 'missing')) == ''


def test_general_page_follows_the_tray_menu(ui_app):
    from agentdock.ui.settings_page import GeneralPage, LookPage
    seen = []
    page = GeneralPage([('notify', '需要處理時跳出通知', True, seen.append)])
    page.switches['notify'].click()
    assert seen == [False]
    page.set_on('notify', True)          # changed from the tray menu: no echo back
    assert page.switches['notify'].isChecked() and seen == [False]
    looks = LookPage({'a': {'label': 'A'}, 'b': {'label': 'B'}}, 'a')
    picked = []
    looks.picked.connect(picked.append)
    looks.tiles['b'].click()
    assert picked == ['b']
    looks.set_current('b')
    assert looks.tiles['b'].isChecked() and not looks.tiles['a'].isChecked()


def test_bold_closes_after_chinese_punctuation(ui_app):
    from agentdock.ui.question_text import cjk_emphasis, question_html
    html = question_html('**工程化：**你進入酒館。**「你從北邊來？」**他問。')
    assert '**' not in html and html.count('font-weight') >= 2
    assert cjk_emphasis('`**a：**b` and **普通**') == '`**a：**b` and **普通**'   # code and plain bold untouched
    assert cjk_emphasis('```\n**x：**y\n```') == '```\n**x：**y\n```'


def test_empty_codex_thought_leaves_no_empty_card(ui_app):
    from agentdock.ui.chat_window import MessageWidget
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    holder = QWidget()
    lay = QVBoxLayout(holder)
    item = {'kind': 'assistant', 'id': 'm1', 'parts': [{'type': 'reasoning', 'key': 'r', 'text': ''}]}
    widget = MessageWidget(item)
    lay.addWidget(widget)
    holder.show()
    assert not widget.isVisible()
    widget.update_item({**item, 'parts': item['parts'] + [{'type': 'text', 'key': 't', 'text': '你好'}]})
    assert widget.isVisible() and not widget.parts['r'].isVisible()


def test_text_size_scales_every_style_sheet():
    from agentdock.ui import theme
    old = theme.TEXT_SCALE
    try:
        theme.TEXT_SCALE = 1.3
        assert theme.sized('QLabel { font-size: 10px; } QPushButton { font-size:13px }') == \
            'QLabel { font-size: 13px; } QPushButton { font-size: 17px }'
        assert theme.px(20) == 26
        theme.TEXT_SCALE = 1.0
        assert theme.sized('a { font-size: 10px; }') == 'a { font-size: 10px; }'
    finally:
        theme.TEXT_SCALE = old



def test_light_looks_bring_a_light_palette(ui_app):
    from PySide6.QtGui import QPalette
    from agentdock.ui import theme
    old = theme.NAME
    try:
        theme.apply('white_orange')
        assert not theme.is_dark()
        p = theme.palette()
        assert p.color(QPalette.ColorRole.Window).name() == theme.CANVAS.lower()
        assert p.color(QPalette.ColorRole.Base).lightness() > 200
        theme.apply('clean')
        assert theme.is_dark() and theme.palette().color(QPalette.ColorRole.Base).lightness() < 80
    finally:
        theme.apply(old)


def test_opencode_shows_the_model_it_really_uses(ui_app, tmp_path, monkeypatch):
    import json
    from agentdock.dispatch.opencode import OpenCodeService
    from agentdock.ui.chat_window import SettingsBar
    from test_dispatch import PASSWORD, FakeOpenCode
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    fake = FakeOpenCode()
    try:
        service = OpenCodeService(fake.url, PASSWORD, fake.version)
        options = service.options(str(tmp_path))
        assert options['defaults'] == {} and options['effort_unset'] == '標準'   # nothing says: no model shown as real
        # The model OpenCode last used (its state) with the variant it remembers for it.
        (tmp_path / 'state' / 'opencode').mkdir(parents=True)
        (tmp_path / 'state' / 'opencode' / 'model.json').write_text(json.dumps(
            {'recent': [{'providerID': 'openai', 'modelID': 'gpt-a'}], 'variant': {'openai/gpt-a': 'high'}}), encoding='utf-8')
        assert service.options(str(tmp_path))['defaults'] == {'model': 'openai/gpt-a', 'effort': {'openai/gpt-a': 'high'}}
        # The user's config beats it; a project's opencode.jsonc beats that.
        (tmp_path / 'config' / 'opencode').mkdir(parents=True)
        (tmp_path / 'config' / 'opencode' / 'opencode.json').write_text('{"model": "openai/gpt-b"}', encoding='utf-8')
        assert service.options(str(tmp_path))['defaults']['model'] == 'openai/gpt-b'
        fake.config = {'model': 'openai/gpt-a'}
        options = service.options(str(tmp_path))
        assert options['defaults']['model'] == 'openai/gpt-a'
        bar = SettingsBar()
        bar.set_options(options, {})
        assert bar.model_chip.text() == 'GPT A' and bar.effort_chip.text() == '高'
        bar.pick('model', 'openai/gpt-b')
        assert bar.effort_chip.text() == '標準'
        assert [a.text() for a in bar.effort_chip.menu_.actions()] == ['標準', '低', '高']
    finally:
        fake.close()
