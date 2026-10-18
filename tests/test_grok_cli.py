import base64
import copy
import json

import pytest
import tomlkit

from agentdock.agents import AgentManager, default_path
from agentdock.extensions import ExtensionManager, PRESETS
from agentdock.library import Library
from agentdock.activity_hook import normalize, conversation_link
from agentdock.tools.tokengauge import gauge


def test_grok_native_config_and_disable_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('GROK_HOME', str(tmp_path / 'grok'))
    assert default_path('grok-cli') == str(tmp_path / 'grok' / 'config.toml')
    cfg = tmp_path / 'config.toml'
    cfg.write_text('# keep\nmodel = "grok"\ndisabled_mcp_servers = ["other", "inbox"]\n', 'utf-8')
    manager = AgentManager(tmp_path / 'data')
    profile = manager.add('Grok CLI', 'grok-cli', str(cfg))
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    entry = library.render(library.get('inbox'), profile.kind, profile)
    assert entry['tool_timeout_sec'] == 1860
    def change(op, **kw):
        plan = manager.prepare([dict(profile=profile.id, server='inbox', op=op, **kw)])
        return manager.apply(plan)
    assert change('add', entry=entry)[0]['backup']
    assert '# keep' in cfg.read_text('utf-8')
    assert tomlkit.parse(cfg.read_text('utf-8'))['disabled_mcp_servers'] == ['other']
    change('disable')
    change('update', entry={**entry, 'command': 'new'})
    assert not manager.inspect()[0]['servers'][0]['enabled']
    change('enable')
    assert manager.inspect()[0]['servers'][0]['enabled']
    change('remove')
    doc = tomlkit.parse(cfg.read_text('utf-8'))
    assert not doc.get('mcp_servers')
    assert doc['disabled_mcp_servers'] == ['other', 'inbox']
    remote = library.render({'key': 'remote', 'type': 'remote', 'url': 'https://example.com/mcp'}, profile.kind, profile)
    plan = manager.prepare([dict(profile=profile.id, server='remote', op='add', entry=remote)])
    cfg.write_text(cfg.read_text('utf-8') + '\n# concurrent change\n', 'utf-8')
    with pytest.raises(ValueError, match='預覽'):
        manager.apply(plan)


def test_grok_hooks_preserve_other_files_and_preview(tmp_path):
    cfg = tmp_path / 'grok' / 'config.toml'
    cfg.parent.mkdir()
    cfg.write_text('# native config\n', 'utf-8')
    hooks = cfg.parent / 'hooks'
    hooks.mkdir()
    other = hooks / 'personal.json'
    other.write_text('{"hooks": {}}', 'utf-8')
    own = hooks / 'agentdock.json'
    personal = {'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': 'personal-format'}]}
    own.write_text(json.dumps({'hooks': {'PreToolUse': [personal]}}), 'utf-8')
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    spec = copy.deepcopy(next(e for e in PRESETS if e['key'] == 'inbox-activity'))
    library.save_extension(spec)
    manager = AgentManager(tmp_path / 'data')
    profile = manager.add('Grok', 'grok-cli', str(cfg))
    extensions = ExtensionManager(library)
    op = dict(profile=profile.id, ext='inbox-activity', op='install')
    before = own.read_text('utf-8')
    plan = extensions.prepare([op], manager.list())
    assert own.read_text('utf-8') == before
    assert extensions.apply(plan)[0]['backup']
    doc = json.loads(own.read_text('utf-8'))
    assert doc['hooks']['PreToolUse'][0] == personal
    hook = doc['hooks']['PreToolUse'][1]
    assert hook['matcher'] == '.*'
    command = hook['hooks'][0]['command']
    decoded = base64.b64decode(command.split()[-1]).decode('utf-16-le')
    assert "'--client' 'grok-cli'" in decoded
    assert 'StopCancelled' in doc['hooks']
    assert extensions.status(spec, profile)
    extensions.apply(extensions.prepare([{**op, 'op': 'uninstall'}], manager.list()))
    assert json.loads(own.read_text('utf-8'))['hooks']['PreToolUse'] == [personal]
    assert other.read_text('utf-8') == '{"hooks": {}}'
    assert cfg.read_text('utf-8') == '# native config\n'


def test_grok_hook_wire_and_imported_claude_suppression():
    raw = dict(sessionId='full-session', hookEventName='pre_tool_use', hook_event_name='PreToolUse',
               cwd='D:/project', toolName='inbox__ask_user', toolInput={'work_id': 'work'}, toolUseId='call')
    assert normalize('grok-cli', raw)['state'] == 'running'
    assert conversation_link('grok-cli', raw) == {'work_id': 'work', 'conversation_id': 'session:grok-cli:full-session'}
    assert normalize('claude-code', raw) is None
    assert conversation_link('claude-code', raw) is None
    assert conversation_link('grok-cli', {**raw, 'toolInputTruncated': True}) is None
    assert normalize('grok-cli', {**raw, 'subagentType': 'explorer'}) is None
    assert normalize('grok-cli', {**raw, 'hook_event_name': 'StopCancelled'})['state'] == 'interrupted'
    assert normalize('grok-cli', {**raw, 'hook_event_name': 'Notification', 'notificationType': 'permission_prompt'})['state'] == 'approval'


def test_shared_quota_display_and_fallback():
    legacy = gauge.ProviderResult('grok', 'Grok', [gauge.Account('xAI', [gauge.Window('week', 10, 100)])])
    bot = gauge.ProviderResult('grokbot', 'GrokBot', [gauge.Account(None, [gauge.Window('week', 12, 100)], unified=True)])
    claude = gauge.ProviderResult('claude', 'Claude Code')
    shown = gauge.display_results([claude, legacy, bot])
    assert [r.name for r in shown] == ['Claude', 'Grok']
    assert len(shown[1].accounts) == 1 and shown[1].accounts[0].windows[0].used == 12
    assert bot.key == 'grokbot' and claude.name == 'Claude Code'  # cached sources are unchanged
    bot.stale = True
    shown = gauge.display_results([legacy, bot])
    assert shown[0].accounts[0].windows[0].used == 10
    bot.stale = False
    bot.accounts[0].unified = False
    assert len(gauge.display_results([legacy, bot])[0].accounts) == 2


def test_unified_quota_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gauge, 'APPDATA', tmp_path)
    monkeypatch.setattr(gauge, 'LATEST_FILE', tmp_path / 'latest.json')
    result = gauge.ProviderResult('grokbot', 'GrokBot', [gauge.Account(None, unified=True)])
    gauge.save_latest([result], 123)
    at, cached = gauge.load_latest()
    assert at == 123 and cached[0].accounts[0].unified


def test_grok_combined_transaction_rolls_back(tmp_path, monkeypatch):
    from agentdock import extensions as extmod
    cfg = tmp_path / 'config.toml'
    original = '# retained\n'
    cfg.write_text(original, 'utf-8')
    manager = AgentManager(tmp_path / 'data')
    profile = manager.add('Grok', 'grok-cli', str(cfg))
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    library.save_extension(copy.deepcopy(next(e for e in PRESETS if e['key'] == 'inbox-activity')))
    extensions = ExtensionManager(library)
    plan = extensions.prepare_all(manager,
        [dict(profile=profile.id, server='test', op='add', entry={'command': 'test'})],
        [dict(profile=profile.id, ext='inbox-activity', op='install')])
    fail = tmp_path / 'fail.txt'
    plan.files.append(extmod.FileChange(fail, '', False, 'test'))
    write = extmod.atomic_write
    def failing(path, text):
        if path == fail:
            raise OSError('simulated failure')
        write(path, text)
    monkeypatch.setattr(extmod, 'atomic_write', failing)
    with pytest.raises(RuntimeError, match='已復原'):
        extensions.apply(plan)
    assert cfg.read_text('utf-8') == original
    assert not (tmp_path / 'hooks' / 'agentdock.json').exists()
