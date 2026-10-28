"""Update tests never execute installers or touch real client installations."""
import subprocess
from types import SimpleNamespace

import pytest

from agentdock import agent_update as updates


@pytest.mark.parametrize('kind', list(updates.PACKAGES))
def test_npm_targets_selected_installation(tmp_path, monkeypatch, kind):
    prefix = tmp_path / 'other npm'
    binary = prefix / 'node_modules' / updates.PACKAGES[kind] / 'node_modules' / 'platform' / 'client.exe'
    monkeypatch.setattr(updates, 'find_program', lambda _: str(binary))
    monkeypatch.setattr(updates.shutil, 'which', lambda _: 'npm.cmd')
    plan = updates.plan_update(kind)
    assert plan.command == ['npm.cmd', 'install', '-g', '--prefix', str(prefix),
                            updates.PACKAGES[kind] + '@latest']


def test_app_bundled_codex_does_not_update_unrelated_npm(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, 'find_program', lambda _: str(tmp_path / 'Codex' / 'bin' / 'codex.exe'))
    with pytest.raises(ValueError, match='原安裝來源'):
        updates.plan_update('codex')


@pytest.mark.parametrize('kind,folder', [('claude-code', '.local/bin'), ('grok-cli', '.grok/bin')])
def test_native_update(tmp_path, monkeypatch, kind, folder):
    monkeypatch.setattr(updates.Path, 'home', lambda: tmp_path)
    binary = str(tmp_path / folder / 'client.exe')
    monkeypatch.setattr(updates, 'find_program', lambda _: binary)
    assert updates.plan_update(kind).command == [binary, 'update']


def test_update_result_and_failure(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout='', stderr='cleanup warning')
    monkeypatch.setattr(updates.subprocess, 'run', run)
    monkeypatch.setattr(updates, 'program_version', lambda _: '0.165.0')
    plan = updates.UpdatePlan('fake.exe', ['fake.exe', 'update'])
    assert updates.run_update(plan) == '0.165.0'
    assert calls[0]['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    assert calls[0]['stdin'] == subprocess.DEVNULL
    monkeypatch.setattr(updates.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=1, stdout='', stderr='download failed'))
    with pytest.raises(RuntimeError, match='download failed'):
        updates.run_update(plan)


def test_ui_confirmation_and_update(tmp_path, ui_app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QPushButton, QVBoxLayout, QWidget
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    manager = AgentManager(tmp_path / 'data')
    manager.add('Codex', 'codex', str(tmp_path / 'codex.toml'))
    manager.add('Grok', 'grok-cli', str(tmp_path / 'grok.toml'))
    view = AgentsView(manager, Library(tmp_path / 'repo', tmp_path / 'data'), wide=True)
    cards = [w for w in view.sections['agents'].findChildren(QWidget)
             if w.objectName() == 'SettingsCard']
    assert len(cards) >= 2
    assert isinstance(cards[0].parentWidget().layout(), QVBoxLayout)
    calls = []
    monkeypatch.setattr(updates, 'plan_update', lambda _: updates.UpdatePlan('fake.exe', ['fake.exe']))
    monkeypatch.setattr(updates, 'run_update', lambda plan: calls.append(plan) or '1.2.3')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.No)
    view._update_agent('codex')
    assert not calls and not view.updating_agents
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    view._update_agent('codex')
    import time
    until = time.monotonic() + 3
    while view.updating_agents and time.monotonic() < until:
        ui_app.processEvents()
        time.sleep(.01)
    assert len(calls) == 1
    assert view.updated_versions['codex'] == '1.2.3'
    assert '重啟 AgentDock' in view.notice.text()
    assert any(b.text() == '更新' for b in view.sections['agents'].findChildren(QPushButton))
    view.close()
