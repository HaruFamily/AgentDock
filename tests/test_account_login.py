"""Account actions use isolated fake executables; never touch real CLI credentials."""
import base64
import os
import subprocess
import sys

import pytest

from agentdock import account_login


def script(command):
    return base64.b64decode(command[-1]).decode('utf-16-le')


@pytest.mark.parametrize('kind,login,logout', [
    ('claude-code', "'auth' 'login'", "'auth' 'logout'"),
    ('codex', "'login'", "'logout'"),
    ('grok-cli', "'login'", "'logout'"),
])
def test_login_and_switch_commands(kind, login, logout):
    command = account_login.console_command(kind, r'C:\fake\client.exe')
    assert '-NoProfile' in command and '-NoExit' not in command
    assert login in script(command) and logout not in script(command)
    switched = script(account_login.console_command(kind, r'C:\fake\client.exe', True))
    assert switched.index(logout) < switched.index(login)
    assert switched.index('if ($LASTEXITCODE -ne 0) { return }') < switched.index(login)


def test_executable_path_is_literal():
    path = "C:\\fake $() ` &\\O'Brien\\client.exe"
    body = script(account_login.console_command('grok-cli', path))
    assert "& 'C:\\fake $() ` &\\O''Brien\\client.exe' 'login'" in body
    with pytest.raises(ValueError):
        account_login.console_command('grok-cli', 'grok.cmd')
    with pytest.raises(ValueError):
        account_login.console_command('other', 'client.exe')


@pytest.mark.skipif(os.name != 'nt', reason='Windows login console')
@pytest.mark.parametrize('code', [0, 1, 130])
def test_console_exits_when_login_finishes(tmp_path, code):
    # Python stands in for the CLI; its "login" argument is a temporary script.
    # Success, failure and cancellation must all return without a shell prompt.
    (tmp_path / 'login').write_text(f'raise SystemExit({code})', encoding='utf-8')
    command = account_login.console_command('codex', sys.executable)
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode in (0, 1)


@pytest.mark.skipif(os.name != 'nt', reason='Windows login console')
def test_failed_logout_does_not_start_login(tmp_path):
    (tmp_path / 'logout').write_text('raise SystemExit(1)', encoding='utf-8')
    (tmp_path / 'login').write_text("from pathlib import Path; Path('ran').touch()", encoding='utf-8')
    subprocess.run(account_login.console_command('codex', sys.executable, True),
                   cwd=tmp_path, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert not (tmp_path / 'ran').exists()


def test_open_login_does_not_run_without_program(monkeypatch):
    monkeypatch.setattr(account_login, 'find_program', lambda kind: None)
    monkeypatch.setattr(account_login.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='找不到'):
        account_login.open_login('grok-cli')


def test_settings_login_only_launches_on_click(tmp_path, ui_app, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    manager = AgentManager(tmp_path / 'data')
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    manager.add('Grok', 'grok-cli', str(tmp_path / 'config.toml'))
    calls = []
    monkeypatch.setattr(account_login, 'open_login', lambda *args: calls.append(args))
    view = AgentsView(manager, library, wide=True)
    assert not calls
    actions = {b.text(): b for b in view.sections['agents'].findChildren(QPushButton)}
    actions['登入'].click()
    actions['切換帳號'].click()
    assert calls == [('grok-cli', False), ('grok-cli', True)]
    assert '重啟 AgentDock' in view.notice.text()
    monkeypatch.setattr(account_login, 'open_login', lambda *args: (_ for _ in ()).throw(OSError('failed')))
    actions['登入'].click()
    assert 'failed' in view.notice.text()
    view.close()
