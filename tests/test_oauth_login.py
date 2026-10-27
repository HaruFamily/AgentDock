"""Google login runs against a fake OAuth library in a temp folder; never a real browser or token."""
import os
import subprocess
import sys

import pytest

from agentdock import oauth_login

SHEETS = {'key': 'google-sheets', 'type': 'uvx', 'package': 'mcp-google-sheets@latest',
          'options': ['--with', 'mcp<2'], 'secrets': ['CREDENTIALS_PATH', 'TOKEN_PATH']}

FAKE_FLOW = '''
import os
class InstalledAppFlow:
    @classmethod
    def from_client_secrets_file(cls, path, scopes):
        assert open(path).read() == 'client'
        return cls()
    def run_local_server(self, port, timeout_seconds):
        if os.environ.get('FAKE_FAIL'):
            raise RuntimeError('access_denied')
        class Creds:
            token = 'fresh'
            def to_json(self):
                return '{"token": "fresh"}'
        return Creds()
'''


def test_only_google_oauth_mcps_offer_login():
    assert oauth_login.scopes(SHEETS) == oauth_login.SCOPES['mcp-google-sheets']
    assert oauth_login.scopes({**SHEETS, 'package': 'mcp-google-sheets==0.6.3'})
    assert oauth_login.scopes({**SHEETS, 'secrets': ['SERVICE_ACCOUNT_PATH']}) is None
    assert oauth_login.scopes({**SHEETS, 'package': 'other-mcp@latest'}) is None
    assert oauth_login.scopes({**SHEETS, 'type': 'npx'}) is None


def test_command_needs_real_paths(tmp_path):
    credentials = tmp_path / 'credentials.json'
    with pytest.raises(ValueError):
        oauth_login.login_command(SHEETS, {'CREDENTIALS_PATH': str(credentials), 'TOKEN_PATH': str(tmp_path / 't')})
    credentials.write_text('client')
    with pytest.raises(ValueError):
        oauth_login.login_command(SHEETS, {'CREDENTIALS_PATH': str(credentials),
                                           'TOKEN_PATH': str(tmp_path / 'missing' / 't')})
    command = oauth_login.login_command(SHEETS, {'CREDENTIALS_PATH': str(credentials),
                                                 'TOKEN_PATH': str(tmp_path / 'token.json')})
    assert command[1:5] == ['--from', 'google-auth-oauthlib', 'python', '-c']
    assert command[-2:] == oauth_login.SCOPES['mcp-google-sheets']


@pytest.mark.parametrize('fail', [False, True])
def test_script_replaces_token_only_after_login(tmp_path, fail):
    fake = tmp_path / 'fake' / 'google_auth_oauthlib'
    fake.mkdir(parents=True)
    (fake / '__init__.py').write_text('')
    (fake / 'flow.py').write_text(FAKE_FLOW)
    (tmp_path / 'credentials.json').write_text('client')
    token = tmp_path / 'token.json'
    token.write_text('old')
    command = oauth_login.login_command(SHEETS, {'CREDENTIALS_PATH': str(tmp_path / 'credentials.json'),
                                                 'TOKEN_PATH': str(token)})
    env = {**os.environ, 'PYTHONPATH': str(tmp_path / 'fake'), **({'FAKE_FAIL': '1'} if fail else {})}
    result = subprocess.run([sys.executable, *command[4:]], env=env, capture_output=True, text=True)
    assert (result.returncode != 0) == fail
    assert token.read_text() == ('old' if fail else '{"token": "fresh"}')
    assert not (tmp_path / 'token.json.new').exists()


def test_failure_reports_last_error_line(monkeypatch, tmp_path):
    (tmp_path / 'c.json').write_text('client')
    secrets = {'CREDENTIALS_PATH': str(tmp_path / 'c.json'), 'TOKEN_PATH': str(tmp_path / 't.json')}
    monkeypatch.setattr(oauth_login.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, '', 'Traceback\nRuntimeError: access_denied\n'))
    with pytest.raises(RuntimeError, match='access_denied'):
        oauth_login.login(SHEETS, secrets)
