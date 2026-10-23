import json

import pytest

from agentdock.agents import AgentManager, default_path
from agentdock.library import Library


def test_grokbot_registration_never_writes_client_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    cfg = tmp_path / '.grokbot' / 'settings.json'
    cfg.parent.mkdir()
    original = json.dumps({'version': 1, 'mcpBoxServers': ['remote-id'],
                           'mcpServers': {'ignored': {'command': 'unused'}}})
    cfg.write_text(original, encoding='utf-8')
    assert default_path('grokbot') == str(cfg)
    manager = AgentManager(tmp_path / 'data')
    profile = manager.add('GrokBot', 'grokbot', str(cfg))
    assert AgentManager(tmp_path / 'data').get(profile.id).kind == 'grokbot'
    assert manager.inspect()[0]['servers'] == []
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    with pytest.raises(ValueError, match='由 GrokBot 管理'):
        library.render({'type': 'command'}, 'grokbot', profile)
    for op in ('add', 'update', 'enable', 'disable', 'remove'):
        with pytest.raises(ValueError, match='由 GrokBot 管理'):
            manager.prepare([{'profile': profile.id, 'server': 'inbox', 'op': op}])
    manager.remove(profile.id)
    assert cfg.read_text(encoding='utf-8') == original
    assert not list(cfg.parent.glob('*.bak'))


def test_grokbot_dialog_and_card(tmp_path, monkeypatch, ui_app):
    from PySide6.QtWidgets import QLabel, QPushButton
    from agentdock.ui.agents_view import AgentDialog, AgentsView

    monkeypatch.setattr('pathlib.Path.home', lambda: tmp_path)
    dialog = AgentDialog(None)
    assert dialog.kind.findData('grokbot') == -1   # no longer offered for new registrations
    manager = AgentManager(tmp_path / 'data')
    profile = manager.add('GrokBot', 'grokbot', str(tmp_path / '.grokbot' / 'settings.json'))
    edit = AgentDialog(None, profile)   # an existing one can still be edited
    assert edit.kind.currentData() == 'grokbot'
    edit.close()
    view = AgentsView(manager, Library(tmp_path / 'repo', tmp_path / 'data'))
    card = view._agent_group(manager.inspect()[0])
    assert any(w.text() == '管理 MCP' for w in card.findChildren(QPushButton))
    assert not any(w.toolTip() == '從 MCP 庫加入' for w in card.findChildren(QPushButton))
    assert not view.needs
    card.close()
    view.close()
    dialog.close()
