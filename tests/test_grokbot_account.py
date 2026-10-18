import base64
import copy
import hashlib
import json
import time

import pytest

from agentdock import grokbot as g


def snapshot():
    return g.Snapshot(g.Identity('test-account', None, 'private-token'),
        {'mcpServers': {'remote': {'url': 'https://example.com/mcp'},
                        'cloud': {'command': '/opt/cloud-tool', 'env': {'KEY': 'preserve'}},
                        'plugin': {'url': 'https://example.com/plugin'}}},
        {'remote': {'serverId': '12'}, 'cloud': {'serverId': '13'},
         'plugin': {'serverId': '14', 'pluginId': '5'}})


class FakeClient:
    def __init__(self):
        self.state = snapshot()
        self.writes = []
        self.reads = 0
        self.fail_after_write = False

    def snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.state)

    def write(self, before, config):
        self.writes.append(copy.deepcopy(config))
        self.state.config = copy.deepcopy(config)
        if self.fail_after_write:
            raise TimeoutError('secret server response must not leak')

    def usage(self):
        return {'usagePercent': 12.5, 'nextResetTimestampUtc': '2026-10-25T02:35:34Z',
                'grokPlanLabel': 'Grok Bot Plan', 'isUnified': True}


def test_backup_then_apply_preserves_unmanaged_and_no_token(tmp_path):
    client = FakeClient()
    change = g.prepare(client.snapshot(), [{'op': 'add', 'name': 'new', 'entry': {'url': 'https://example.com/new'}}])
    assert not client.writes
    original = copy.deepcopy(client.state)
    write = client.write

    def checked_write(before, config):
        backups = list(tmp_path.glob('*.json'))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text())['config'] == original.config
        assert 'private-token' not in backups[0].read_text()
        write(before, config)

    client.write = checked_write
    backup = g.apply(client, change, tmp_path)
    assert backup.exists() and len(client.writes) == 1
    assert client.state.config['mcpServers']['cloud'] == original.config['mcpServers']['cloud']
    assert client.state.config['mcpServers']['plugin'] == original.config['mcpServers']['plugin']


@pytest.mark.parametrize('conflict', ['account', 'team', 'config', 'metadata'])
def test_preview_conflict_never_writes(tmp_path, conflict):
    client = FakeClient()
    change = g.prepare(client.snapshot(), [{'op': 'remove', 'name': 'remote'}])
    if conflict == 'account':
        client.state.identity = g.Identity('other', None, 'other-token')
    elif conflict == 'team':
        client.state.identity = g.Identity('test-account', 2, 'token')
    elif conflict == 'config':
        client.state.config['mcpServers']['remote']['url'] = 'https://changed.example/mcp'
    else:
        client.state.metadata['remote']['serverId'] = '99'
    with pytest.raises(g.GrokBotError, match='預覽後改變'):
        g.apply(client, change, tmp_path)
    assert not client.writes and not list(tmp_path.iterdir())


def test_conflict_after_backup_and_uncertain_write_do_not_retry(tmp_path, monkeypatch):
    client = FakeClient()
    change = g.prepare(client.snapshot(), [{'op': 'remove', 'name': 'remote'}])
    atomic = g.atomic_json

    def concurrent_change(path, value):
        atomic(path, value)
        client.state.config['mcpServers']['remote']['url'] = 'https://changed.example/mcp'

    monkeypatch.setattr(g, 'atomic_json', concurrent_change)
    with pytest.raises(g.GrokBotError, match='未套用'):
        g.apply(client, change, tmp_path)
    assert not client.writes
    monkeypatch.setattr(g, 'atomic_json', atomic)
    client.fail_after_write = True
    change = g.prepare(client.snapshot(), [{'op': 'remove', 'name': 'remote'}])
    with pytest.raises(g.GrokBotError, match='未自動重送') as error:
        g.apply(client, change, tmp_path)
    assert 'secret server response' not in str(error.value)
    assert len(client.writes) == 1
    assert len(list(tmp_path.glob('*.json'))) == 2


def test_reject_cloud_plugin_and_unknown_config():
    with pytest.raises(g.GrokBotError, match='內建'):
        g.prepare(snapshot(), [{'op': 'add', 'name': 'cursor-box-computer', 'entry': {'url': 'https://example.com'}}])
    for name in ('cloud', 'plugin'):
        with pytest.raises(g.GrokBotError):
            g.prepare(snapshot(), [{'op': 'remove', 'name': name}])
    for entry in ({'command': 'uvx'}, {'url': 'http://example.com'},
                  {'url': 'https://example.com', 'auth': {}},
                  {'url': 'https://example.com', 'headers': {'a': 3}}):
        with pytest.raises(g.GrokBotError):
            g.prepare(snapshot(), [{'op': 'add', 'name': 'new', 'entry': entry}])


def test_credentials_active_account_expiry_and_no_writes(tmp_path, monkeypatch):
    def token(subject, expiry):
        payload = base64.urlsafe_b64encode(json.dumps({'sub': subject, 'exp': expiry}).encode()).decode().rstrip('=')
        return 'header.' + payload + '.signature'

    principal = 'fixture-user'
    account = hashlib.sha256(principal.encode()).hexdigest()
    secret = tmp_path / 'sand-secrets.json'
    def write(value, active=account):
        secret.write_text(json.dumps({'version': 1, 'cursor-accounts': json.dumps({'active': active,
            'accounts': {account: {'cursor-access-token': value, 'cursor-selected-team-id': '7'}}})}))

    monkeypatch.setattr(g, '_decrypt', lambda value, folder: value)
    write(token(principal, time.time() + 900))
    original = secret.read_bytes()
    identity = g.credentials(tmp_path)
    assert identity.account == account and identity.team == 7
    assert 'signature' not in repr(identity)
    assert secret.read_bytes() == original
    write(token('different-user', time.time() + 900))
    with pytest.raises(g.GrokBotError):
        g.credentials(tmp_path)
    write(token(principal, time.time() - 10))
    with pytest.raises(g.GrokBotError, match='過期'):
        g.credentials(tmp_path)
    write(token(principal, time.time() + 900), None)
    with pytest.raises(g.GrokBotError):
        g.credentials(tmp_path)


def test_electron_v10_decrypt_fixture(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, nonce = b'k' * 32, b'n' * 12
    raw = b'v10' + nonce + AESGCM(key).encrypt(nonce, b'fixture-value', None)
    (tmp_path / 'Local State').write_text(json.dumps({'os_crypt': {
        'encrypted_key': base64.b64encode(b'DPAPIfixture').decode()}}))
    monkeypatch.setattr(g, '_unprotect', lambda value: key if value == b'fixture' else pytest.fail())
    assert g._decrypt(base64.b64encode(raw).decode(), tmp_path) == 'fixture-value'
    assert g._decrypt('plaintext:v1:' + base64.b64encode(b'fixture').decode(), tmp_path) == 'fixture'


def test_protocol_metadata_and_empty_account(monkeypatch):
    client = g.Client()
    identity = snapshot().identity
    monkeypatch.setattr(client, 'identity', lambda: identity)
    calls = []
    def call(method, payload, auth):
        calls.append((method, payload, auth))
        return {'configJson': json.dumps(snapshot().config), 'serverMetadataByName': snapshot().metadata}
    monkeypatch.setattr(client, 'call', call)
    before = client.snapshot()
    config = copy.deepcopy(before.config)
    del config['mcpServers']['remote']
    client.write(before, config)
    assert calls[0][1] == {'teamScope': False, 'redactSecrets': False}
    assert calls[1][1]['serverIdsByName'] == {'cloud': '13', 'plugin': '14'}
    assert calls[1][1]['teamScope'] is False
    monkeypatch.setattr(client, 'call', lambda *args: {})
    assert client.snapshot().config == {'mcpServers': {}}
    monkeypatch.setattr(client, 'call', lambda *args: {'configJson': '[]'})
    with pytest.raises(g.GrokBotError):
        client.snapshot()
    for malformed in ({'configJson': None}, {'error': 'server error'}):
        monkeypatch.setattr(client, 'call', lambda *args: malformed)
        with pytest.raises(g.GrokBotError):
            client.snapshot()


def test_transport_headers_no_redirect_and_sanitized_failure(monkeypatch):
    import io
    import urllib.error
    seen = []
    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return io.BytesIO(b'{"usagePercent":5}')
    def opener(handler):
        assert isinstance(handler, g._NoRedirect)
        assert handler.redirect_request(None, None, 302, '', {}, 'https://other.example') is None
        return Opener()
    monkeypatch.setattr(g.urllib.request, 'build_opener', opener)
    client = g.Client()
    assert client.call('GetSandUsageStatus', {}, g.Identity('a', 7, 'fixture')) == {'usagePercent': 5}
    request, timeout = seen[0]
    assert request.full_url == g.API + 'GetSandUsageStatus'
    assert request.get_method() == 'POST' and json.loads(request.data) == {}
    assert request.get_header('Authorization') == 'Bearer fixture'
    assert request.get_header('X-cursor-team-id') == '7'
    assert timeout == 15
    def denied(self, request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, 'sensitive-body', {}, None)
    monkeypatch.setattr(Opener, 'open', denied)
    with pytest.raises(g.GrokBotError) as error:
        client.call('GetSandUsageStatus', {}, snapshot().identity)
    assert 'sensitive-body' not in str(error.value)
    with pytest.raises(g.GrokBotError):
        client.call('Unknown', {}, snapshot().identity)


def test_restore_is_previewed_and_backup_failure_never_writes(tmp_path, monkeypatch):
    client = FakeClient()
    original = client.snapshot()
    change = g.prepare(original, [{'name': 'remote', 'op': 'update',
                                  'entry': {'url': 'https://example.com/updated'}}])
    backup = g.apply(client, change, tmp_path)
    restored = g.prepare_restore(client.snapshot(), json.loads(backup.read_text()))
    assert restored.config == original.config
    assert len(client.writes) == 1  # restore preview is read-only
    g.apply(client, restored, tmp_path)
    assert client.state.config == original.config
    bad = json.loads(backup.read_text())
    bad['account'] = 'other'
    with pytest.raises(g.GrokBotError, match='不同'):
        g.prepare_restore(client.snapshot(), bad)
    def failed_backup(*args):
        raise OSError('disk full')
    monkeypatch.setattr(g, 'atomic_json', failed_backup)
    with pytest.raises(OSError):
        g.apply(client, g.prepare(client.snapshot(), [{'name': 'remote', 'op': 'remove'}]), tmp_path)
    assert len(client.writes) == 2


def test_usage_collector_missing_and_unknown_not_zero(tmp_path, monkeypatch):
    from agentdock.tools.tokengauge import gauge
    monkeypatch.setattr(g, 'user_data_dir', lambda: tmp_path)
    assert gauge.grokbot_collect().missing
    (tmp_path / 'sand-secrets.json').write_text('{}')
    monkeypatch.setattr(g, 'Client', FakeClient)
    result = gauge.grokbot_collect()
    assert result.accounts[0].windows[0].used == 12.5
    assert result.accounts[0].windows[0].resets_at is not None
    assert '共用' in result.accounts[0].note
    for percent in (None, True, float('nan'), float('inf'), -1, '10'):
        assert g.usage_values({'usagePercent': percent})[0] is None
    assert g.usage_values({'usagePercent': 0})[0] == 0
    assert g.usage_values({'usagePercent': 12, 'usesPooledEnterpriseAllowance': True})[0] is None


def test_ui_cancel_and_apply_run_off_thread(tmp_path, ui_app, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    from agentdock.agents import Profile
    from agentdock.library import Library
    from agentdock.ui.grokbot_view import GrokBotMcpDialog
    client = FakeClient()
    dialog = GrokBotMcpDialog(None, Library(tmp_path / 'repo', tmp_path / 'data'),
        Profile('p', 'GrokBot', 'grokbot', str(tmp_path / 'settings.json')), tmp_path / 'backup', client)

    def wait():
        until = time.monotonic() + 3
        while dialog.busy and time.monotonic() < until:
            ui_app.processEvents()
            time.sleep(.01)
        assert not dialog.busy

    wait()
    assert dialog.tree.topLevelItemCount() == 3
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    dialog.propose([{'name': 'remote', 'op': 'remove'}])
    assert not client.writes and not (tmp_path / 'backup').exists()
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Ok)
    dialog.propose([{'name': 'remote', 'op': 'remove'}])
    assert dialog.busy
    dialog.reject()
    assert dialog.busy  # cannot abandon an in-flight write
    wait()
    assert len(client.writes) == 1
    assert not dialog.add_btn.isEnabled()
    dialog.reload()
    wait()
    assert dialog.tree.topLevelItemCount() == 2
    dialog.close()
