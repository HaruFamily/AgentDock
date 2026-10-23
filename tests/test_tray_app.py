"""The tray app replaces the floating card; the Inbox is retired."""
import json
import os

import pytest
from PySide6.QtWidgets import QLabel

from agentdock.broker import RETIRED, Broker
from agentdock.dispatch.store import owner_for
from agentdock.qa.store import QuestionStore

OWNER = owner_for('p', 'opencode')


def test_retired_inbox_declines_questions_and_drops_reports(tmp_path):
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store, inbox=False)
    headers = {'authorization': f'Bearer {broker.token}', 'host': '127.0.0.1:1', 'x-agentdock-owner': OWNER}
    ask = json.dumps({'source': 'Codex', 'input': {'request_key': 'k', 'work_id': 'w', 'work_title': 't',
                                                   'question': 'ok?'}}).encode()
    status, body = broker.handle('POST', '/questions', headers, ask)
    assert status == 410 and body['error'] == RETIRED and store.list() == []
    report = json.dumps({'source': 'Codex', 'input': {'request_key': 'r', 'work_id': 'w', 'work_title': 't',
                                                      'kind': 'completed', 'text': 'done'}}).encode()
    assert broker.handle('POST', '/chat/events', headers, report) == (200, {'status': 'ignored'})
    assert broker.handle('POST', '/activity', headers, b'{}') == (200, {'status': 'ignored'})
    assert broker.handle('GET', '/health', headers, b'')[0] == 200
    assert store._chat['events'] == [] and store.activity.rows == {}
    # The old shell keeps its Inbox.
    old_shell = Broker(tmp_path, store)
    assert old_shell.handle('POST', '/questions', {**headers, 'authorization': f'Bearer {old_shell.token}'}, ask)[0] == 200


def test_attention_counts_and_badge(ui_app):
    from agentdock.ui.tray_app import attention, badge_icon
    rows = {'a': {'permissions': {'p1': {}}, 'forms': {}, 'read': True, 'state': 'running'},
            'b': {'permissions': {}, 'forms': {'f1': {}, 'f2': {}}, 'read': True, 'state': 'running'},
            'c': {'permissions': {}, 'forms': {}, 'read': False, 'state': 'done'},
            'd': {'permissions': {}, 'forms': {}, 'read': False, 'state': 'running'}}
    assert attention(rows) == (1, 2, 1)
    assert not badge_icon(3, False).isNull() and not badge_icon(0, True).isNull()


def test_tray_app_runs_without_the_floating_card(tmp_path, monkeypatch):
    # A real app.exec() must not run callbacks left by other widget tests.
    if os.environ.get('AGENTDOCK_TRAY_TEST_CHILD') != '1':
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', __file__ + '::test_tray_app_runs_without_the_floating_card'],
            env={**os.environ, 'AGENTDOCK_TRAY_TEST_CHILD': '1', 'QT_QPA_PLATFORM': 'offscreen'},
            capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        return
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication
    from agentdock.tools import tokengauge
    from agentdock.ui import app as app_module, tray_app
    app = QApplication.instance() or QApplication([])
    single_shot = QTimer.singleShot
    monkeypatch.setattr(QTimer, 'singleShot', staticmethod(
        lambda delay, *args: single_shot(delay, *args) if delay < 1000 else None))
    monkeypatch.setenv('AGENTDOCK_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(tray_app, 'ROOT', tmp_path)
    monkeypatch.setattr(app_module, 'ROOT', tmp_path)
    monkeypatch.setattr(tray_app, 'discover', lambda: [])
    monkeypatch.setattr(tray_app.winutil, 'IS_WINDOWS', False)
    monkeypatch.setattr(tray_app.AgentsView, 'check_updates', lambda *a, **kw: None)

    class FakeQuota(QObject):
        changed = Signal()

        def __init__(self, *_args):
            super().__init__()
            self.active, self.last, self.busy, self.collected = True, None, False, 0

        def set_active(self, active):
            self.active = active

        def visible_results(self):
            return []

        def collect(self):
            self.collected += 1
    monkeypatch.setattr(tokengauge, 'QuotaModel', FakeQuota)

    class FakeTray:
        def __init__(self):
            self.messages = []

        def showMessage(self, *args):
            self.messages.append(args)

        def setIcon(self, _icon):
            pass

        def setToolTip(self, text):
            self.tip = text

        def hide(self):
            pass

    dock = tray_app.TrayApp(app, True)
    assert not hasattr(dock, 'ball') and not dock.chat.isVisible()        # --background: tray only
    assert dock.broker.inbox is False and not dock.quota.active
    dock.tray = FakeTray()
    dock.open_chat()
    assert dock.chat.isVisible() and dock.chat.rail.current == 'chat'
    dock.chat.rail.buttons['settings'].click()
    assert dock.chat.pages.currentWidget() is dock.agents and dock.chat.sidebar.isHidden()
    dock.chat.rail.buttons['tools'].click()
    assert dock.chat.pages.currentWidget() is dock.tools_page and dock.quota.active
    dock.chat.rail.buttons['chat'].click()
    assert dock.chat.pages.currentWidget() is dock.chat.new_view and not dock.quota.active
    # The quota float is the only floating window, remembered across starts.
    dock.tools_page.float_switch.setChecked(True)
    assert dock.quota_float.isVisible() and dock.quota.active and dock.ui['quota_float'] is True
    dock.quota_float.moved.emit(dock.quota_float.pos())
    assert 'quota_float_pos' in json.loads((tmp_path / 'ui.json').read_text(encoding='utf-8'))
    dock.set_quota_float(False)
    assert not dock.quota_float.isVisible() and not dock.tools_page.float_switch.isChecked()
    # Approvals and questions pop a notice unless that conversation is already in front.
    store = dock.store.dispatch
    store.track('opencode', 'ses_1', owner=OWNER, title='修登入')
    store.update('opencode:ses_1', permissions={'per_1': {'action': 'shell', 'resources': ['npm test']}})
    app.processEvents()
    assert dock.tray.messages[-1][:2] == ('OpenCode：待授權', '修登入') and dock._toast_key == 'opencode:ses_1'
    dock._sync_tray()
    assert dock.tray.tip == 'AgentDock：待授權 1'
    dock._toast_clicked()
    assert dock.chat.current_key == 'opencode:ses_1'
    dock.ui['notify_attention'] = False
    store.update('opencode:ses_1', forms={'frm_1': {'id': 'frm_1', 'fields': []}})
    app.processEvents()
    assert len(dock.tray.messages) == 1
    # Starting with Windows stays in the tray.
    assert dock.autostart_command().endswith('--background')
    # A second start brings the window back.
    dock.chat.hide()
    dock.bus.show_requested.emit()
    assert dock.chat.isVisible()
    QTimer.singleShot(0, dock.quit)
    app.exec()
    assert not dock.chat.isVisible() and 'chat_geometry' in dock.ui
    dock.chat.deleteLater()
    dock.quota_float.deleteLater()


@pytest.mark.parametrize('name', ['inbox', 'agentchat'])
def test_settings_remove_retired_inbox_instead_of_migrating(tmp_path, ui_app, name):
    import tomlkit
    from agentdock.agents import AgentManager
    from agentdock.library import Library, QAI_KEY
    from agentdock.ui.agents_view import AgentsView
    cfg = tmp_path / 'config.toml'
    cfg.write_text(f'[mcp_servers.zeta]\ncommand = "z"\n[mcp_servers.{name}]\ncommand = "python.exe"\n'
                   f'args = ["-m", "agentdock.mcp_server"]\n[mcp_servers.{name}.env]\nAGENTDOCK_DATA_DIR = "d"\n',
                   encoding='utf-8')
    manager, library = AgentManager(tmp_path / 'data'), Library(tmp_path / 'repo', tmp_path / 'data')
    library.inbox_retired = True
    profile = manager.add('Codex', 'codex', str(cfg))
    view = AgentsView(manager, library)
    assert QAI_KEY not in {e['key'] for e in library.entries()}
    view._update_all()
    assert [(o['server'], o['op']) for o in view.ops] == [(name, 'remove')]
    manager.apply(manager.prepare(view.ops))
    assert set(tomlkit.parse(cfg.read_text(encoding='utf-8')).unwrap()['mcp_servers']) == {'zeta'}
    assert list((tmp_path / 'data').glob('*.bak')) or any(cfg.parent.glob('*.bak'))   # backed up first
    assert not any(e['key'] == 'inbox-activity' for e in view._addable_extensions(manager.get(profile.id)))


def test_wide_settings_have_categories_and_one_switch_per_agent(tmp_path, ui_app):
    import json
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    from agentdock.ui.settings_page import Switch
    manager, library = AgentManager(tmp_path / 'data'), Library(tmp_path / 'repo', tmp_path / 'data')
    library.inbox_retired = True   # as in the tray app
    cfg = tmp_path / '.claude.json'
    cfg.write_text(json.dumps({'mcpServers': {'mine': {'command': 'a', 'args': ['b']}}}), encoding='utf-8')
    claude = manager.add('Claude', 'claude-code', str(cfg))
    codex = manager.add('Codex', 'codex', str(tmp_path / 'config.toml'))
    state, saved = {}, []
    view = AgentsView(manager, library, ui_state=state, save_state=lambda: saved.append(1), wide=True)
    view.agent_info = lambda kind: {'program': '2.1.0' if kind == 'claude-code' else None, 'model': 'Opus 5.5 · 中'}
    view.resize(1200, 800)
    view.show()
    assert list(view.tabs) == ['general', 'look', 'agents', 'library', 'extensions', 'backup']
    assert view.tabs['agents'].isChecked() and view.tabs['general'].isHidden()   # 一般/外觀 come from the tray app
    assert view.extra_pages['backup'].isHidden() and not view.tabs['backup'].isHidden()
    view.tabs['library'].click()
    assert view.sections['library'].isVisible() and not view.sections['agents'].isVisible()
    assert state['settings_tab'] == 'library' and saved
    view.refresh()                 # a rebuild keeps the chosen category
    assert view.sections['library'].isVisible()

    def cells():   # one switch per Agent for the adopted MCP "mine"
        return [w for w in view.sections['library'].findChildren(Switch) if w.isVisibleTo(view)]
    assert [c.state for c in cells()] == ['on', 'off']
    cells()[1].click()             # add to Codex: queued, shown as waiting, the bar offers to apply
    assert [o['op'] for o in view.ops] == ['add'] and o_profile(view) == codex.id
    assert [c.state for c in cells()] == ['on', 'adding'] and view.apply_btn.isVisibleTo(view)
    cells()[1].click()             # clicking a waiting change undoes it
    assert not view.ops and [c.state for c in cells()] == ['on', 'off']
    cells()[0].click()             # remove from Claude
    assert view.ops[0]['op'] == 'remove' and view.ops[0]['profile'] == claude.id
    texts = [w.text() for w in view.sections['library'].findChildren(QLabel)]
    assert any('Claude 將移除' in t for t in texts)
    view.tabs['agents'].click()
    facts = [w.text() for w in view.sections['agents'].findChildren(QLabel)]
    assert '2.1.0' in facts and '找不到程式' in facts and 'Opus 5.5 · 中' in facts
    narrow = AgentsView(manager, library)
    assert not hasattr(narrow, 'tabs') and narrow.property('wide') is False


def o_profile(view):
    return view.ops[0]['profile']
