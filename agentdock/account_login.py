"""User-requested CLI account operations in a visible, interactive console.

No credentials are read or copied. The CLI owns authentication and its storage.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess

from agentdock.agent_info import find_program


LOGIN_ARGS = {'claude-code': ['auth', 'login'], 'codex': ['login'], 'grok-cli': ['login']}
LOGOUT_ARGS = {'claude-code': ['auth', 'logout'], 'codex': ['logout'], 'grok-cli': ['logout']}


def _literal(value: str) -> str:
    """A PowerShell literal, including paths containing quotes or shell syntax."""
    return "'" + value.replace("'", "''") + "'"


def console_command(kind: str, executable: str, switch: bool = False) -> list[str]:
    if kind not in LOGIN_ARGS:
        raise ValueError('此 Agent 尚未支援帳號登入。')
    if Path(executable).suffix.lower() != '.exe':
        raise ValueError('帳號登入需要客戶端的原生執行檔。')
    commands = []
    if switch:
        commands.append(LOGOUT_ARGS[kind])
    commands.append(LOGIN_ARGS[kind])
    script = "$ErrorActionPreference = 'Stop'\n"
    for args in commands:
        script += '& ' + ' '.join(_literal(arg) for arg in [executable, *args]) + '\n'
        script += 'if ($LASTEXITCODE -ne 0) { return }\n'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    shell = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) /
                'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    return [shell, '-NoLogo', '-NoProfile', '-EncodedCommand', encoded]


def open_login(kind: str, switch: bool = False) -> None:
    """Only called after the user clicks 登入 or 切換帳號."""
    executable = find_program(kind)
    if not executable:
        raise ValueError('找不到客戶端執行檔。')
    command = console_command(kind, executable, switch)
    subprocess.Popen(command, cwd=Path.home(),
                     creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
