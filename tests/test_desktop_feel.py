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
    store.dispatch.track('opencode', 'ses_1', owner='o', title='first')
    store.dispatch.rename('opencode:ses_1', 'Mine')
    store.dispatch.set_pinned('opencode:ses_1', True)
    store.dispatch.track('opencode', 'ses_1', owner='o', title='client title')
    row = QuestionStore(tmp_path).dispatch.get('opencode:ses_1')
    assert row['pinned'] and row['title'] == 'Mine'
    store.dispatch.set_pinned('opencode:ses_1', False)
    assert 'pinned' not in store.dispatch.get('opencode:ses_1')


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
    assert menu[:2] == ['專案', 'Repo'] and str(other) in menu and menu[-2:] == ['選擇資料夾…', '新增專案…']
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
    # The page lists the latest conversations to jump back into.
    win.show_new()
    entries = [view.recent.itemAt(i).widget() for i in range(view.recent.count())]
    assert [e.key for e in entries] == [key]
    assert entries[0].title.text() == row['title']
    entries[0].click()
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
    win.sidebar._hovered(top.child(0))
    button = win.sidebar.tree.itemWidget(top.child(0), 1)
    assert button is not None and top.child(0).text(1).strip() == ''
    button.click()
    assert shown and shown[0][0] == '取消釘選'
    win.sidebar._unhover()
    assert top.child(0).text(1).strip() != ''


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
    win.show_new(agent='claude-code', folder=str(work))
    bar = win.new_view.composer.settings
    wait(lambda: not bar.model_chip.isHidden(), app=app)
    assert [m['id'] for m in store.dispatch.options['claude-code']['models']] == ['opus']
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
    client.learned({'models': [
        {'value': 'default', 'displayName': 'Default (recommended)',
         'description': 'Use the default model (currently Opus 5.5) · $4/$20 per Mtok', 'supportedEffortLevels': ['low']},
        {'value': 'opus', 'displayName': 'Opus', 'description': 'Opus 5.5 · Best for everyday, complex tasks',
         'supportedEffortLevels': ['low', 'high']},
        {'value': 'custom', 'displayName': 'Custom', 'description': 'Something else entirely'}]})
    options = got[-1]
    assert [m['label'] for m in options['models']] == ['Opus 5.5', 'Custom'] and options['model_default'] == 'Opus 5.5'
    bar = SettingsBar()
    bar.set_options(options, {})
    assert bar.model_chip.text() == 'Opus 5.5'
    assert [a.text() for a in bar.model_chip.menu_.actions()][:2] == ['預設（Opus 5.5）', 'Opus 5.5']


# --------------------------------------------------------------------------- the local look's decorations
def test_local_look_paints_parts_of_the_chat_window(window, monkeypatch):
    import types
    from agentdock.ui import theme
    win, make, store, sender, notes, work, projects, state, app = window
    assert not theme.decorated('panel')   # nothing local: the plain look
    calls = []

    def hook(name):
        return lambda painter, rect, *args: calls.append(name)
    local = types.SimpleNamespace(**{f'draw_{n}': hook(n) for n in ('canvas', 'panel', 'box', 'heading')})
    local.draw_box = lambda *_a: (_ for _ in ()).throw(RuntimeError('broken'))   # a failing hook never breaks painting
    monkeypatch.setattr(theme, 'PRIVATE', local)
    monkeypatch.setattr(theme, 'CUTE', True)
    win.refresh_theme()
    win.show_new()
    win.repaint()
    app.processEvents()
    assert {'canvas', 'panel', 'heading'} <= set(calls)
    assert win.sidebar.layout().contentsMargins().top() == 40
    assert 'background: transparent; border-right: none' in win.styleSheet()


def test_rounding_stays_within_half_the_height(window):
    import re
    win, make, store, sender, notes, work, projects, state, app = window
    css = win.styleSheet()
    radius = lambda name: int(re.search(rf'#{name} {{[^}}]*border-radius: (\d+)px', css).group(1))
    assert radius('UserBubble') * 2 <= 36 and radius('ToolRow') * 2 <= 28
