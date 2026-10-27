"""User-requested Google OAuth login for MCPs that keep a desktop-app token (CREDENTIALS_PATH, TOKEN_PATH).

Such an MCP opens the same login when its token no longer refreshes, but the client stops it after about
30 seconds, so the token is never saved and every start asks again. This runs the login on its own.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from agentdock.paths import local_uvx

SCOPES = {
    'mcp-google-sheets': ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'],
}
TIMEOUT = 600

# Writes beside the token first, so a failed login keeps the old one.
SCRIPT = '''
import os, sys
from google_auth_oauthlib.flow import InstalledAppFlow
credentials, token, timeout, *scopes = sys.argv[1:]
flow = InstalledAppFlow.from_client_secrets_file(credentials, scopes)
creds = flow.run_local_server(port=0, timeout_seconds=int(timeout))
if not creds or not creds.token:
    sys.exit('沒有完成登入。')
with open(token + '.new', 'w', encoding='utf-8') as f:
    f.write(creds.to_json())
os.replace(token + '.new', token)
'''


def scopes(entry: dict[str, Any]) -> list[str] | None:
    """The scopes to ask for, or None when the entry is not a known Google OAuth MCP."""
    if entry.get('type') != 'uvx' or not {'CREDENTIALS_PATH', 'TOKEN_PATH'} <= set(entry.get('secrets') or []):
        return None
    name = re.split(r'[@=<>!~\[\s]', str(entry.get('package') or ''), maxsplit=1)[0].lower()
    return SCOPES.get(name)


def login_command(entry: dict[str, Any], secrets: dict[str, str]) -> list[str]:
    wanted = scopes(entry)
    if not wanted:
        raise ValueError('這個 MCP 不支援 Google 登入。')
    credentials, token = secrets.get('CREDENTIALS_PATH', ''), secrets.get('TOKEN_PATH', '')
    if not credentials or not os.path.isfile(credentials):
        raise ValueError('找不到 CREDENTIALS_PATH 指定的憑證檔。')
    if not token or not os.path.isdir(os.path.dirname(token) or '.'):
        raise ValueError('TOKEN_PATH 的資料夾不存在。')
    return [local_uvx() or 'uvx', '--from', 'google-auth-oauthlib', 'python', '-c', SCRIPT,
            credentials, token, str(TIMEOUT), *wanted]


def login(entry: dict[str, Any], secrets: dict[str, str]) -> None:
    """Only called after the user clicks 登入 Google. Opens the browser and waits for the answer."""
    result = subprocess.run(login_command(entry, secrets), capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=TIMEOUT + 120,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0:
        lines = [l for l in (result.stderr or result.stdout).splitlines() if l.strip()]
        raise RuntimeError(lines[-1] if lines else f'結束代碼 {result.returncode}')
