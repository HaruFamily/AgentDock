import base64
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import tomlkit

from agentdock import codebase_integration as cbm
from agentdock import extensions as extmod
from agentdock.agents import AgentManager
from agentdock.extensions import ExtensionManager
from agentdock.library import Library

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    root = tmp_path / 'repo'
    asset = root / 'agentdock/assets/opencode/cbm-augment.ts'
    asset.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'agentdock/assets/opencode/cbm-augment.ts', asset)
    library = Library(root, tmp_path / 'data')
    exe = tmp_path / '版本 1' / 'codebase-memory-mcp.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fake; never execute')
    library.save({'key': 'codebase-memory-mcp', 'type': 'command', 'command': str(exe)})
    library.save_extension(cbm.PRESET)
    manager = AgentManager(tmp_path / 'data')
    profiles = {}
    for kind, name in [('codex', '.codex/config.toml'), ('claude-code', '.claude.json'), ('opencode', '.config/opencode/opencode.jsonc')]:
        path = tmp_path / 'home' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# keep comment\nmodel = "test"\n' if kind == 'codex' else '{}', 'utf-8')
        profiles[kind] = manager.add(kind, kind, str(path))
    return library, manager, profiles, ExtensionManager(library)


def operations(env):
    library, manager, profiles, extension = env
    mcp_ops = [{'profile': p.id, 'server': 'codebase-memory-mcp', 'op': 'add',
                'entry': library.render(library.get('codebase-memory-mcp'), p.kind, p)} for p in profiles.values()]
    ext_ops = [{'profile': p.id, 'ext': cbm.PRESET['key'], 'op': 'install'} for p in profiles.values()]
    return mcp_ops, ext_ops


def test_install_shared_plan_preview_and_idempotence(env):
    library, manager, profiles, extension = env
    mcp_ops, ext_ops = operations(env)
    before = {p.path: Path(p.path).read_text('utf-8') for p in profiles.values()}
    plan = extension.prepare_all(manager, mcp_ops, ext_ops)
    codex = Path(profiles['codex'].path)
    assert sum(f.path == codex for f in plan.files) == 1
    assert before == {p.path: Path(p.path).read_text('utf-8') for p in profiles.values()}
    results = extension.apply(plan)
    assert any(r['backup'] for r in results)
    assert '# keep comment' in codex.read_text('utf-8')
    assert not (codex.parent / 'hooks.json').exists()
    assert all(extension.in_sync(cbm.PRESET, p) for p in profiles.values())
    assert not extension.prepare_all(manager, [], ext_ops).files
    plugin = Path(profiles['opencode'].path).parent / 'plugins/cbm-augment.ts'
    assert '__CBM_LAUNCH__' not in plugin.read_text('utf-8')
    remove = [{**o, 'op': 'uninstall'} for o in ext_ops]
    extension.apply(extension.prepare_all(manager, [], remove))
    assert not plugin.exists()
    assert all(extension.status(cbm.PRESET, p) is False for p in profiles.values())
    assert 'codebase-memory-mcp' in tomlkit.parse(codex.read_text('utf-8'))['mcp_servers']


def test_adopt_existing_hooks_preserve_foreign_and_repair_partial(env):
    library, manager, profiles, extension = env
    mcp_ops, ext_ops = operations(env)
    codex = Path(profiles['codex'].path)
    old = {'type': 'command', 'command': "'C:/old/codebase-memory-mcp.exe' hook-augment",
           'command_windows': "& 'C:/old/codebase-memory-mcp.exe' hook-augment", 'timeout': 5}
    foreign = {'type': 'command', 'command': 'echo codebase-memory-mcp hook-augment'}
    doc = tomlkit.parse(codex.read_text('utf-8'))
    doc['hooks'] = {'SessionStart': [{'matcher': '*', 'hooks': [old, foreign]}]}
    codex.write_text(tomlkit.dumps(doc), 'utf-8')
    duplicate = codex.parent / 'hooks.json'
    duplicate.write_text(json.dumps({'hooks': {'SessionStart': [{'matcher': '*', 'hooks': [old]}]}, 'other': True}), 'utf-8')
    settings = Path(profiles['claude-code'].path).parent / '.claude/settings.json'
    settings.parent.mkdir()
    legacy = "cmd.exe /d /v:off /s /c '\"\"%USERPROFILE%\\.claude\\hooks\\cbm-session-reminder.cmd\"\"'"
    settings.write_text(json.dumps({'model': 'keep', 'hooks': {'SessionStart': [{'matcher': 'startup', 'hooks': [{'type': 'command', 'command': legacy}, foreign]}]}}), 'utf-8')
    assert extension.status(cbm.PRESET, profiles['codex'])
    assert extension.status(cbm.PRESET, profiles['claude-code'])
    assert not extension.in_sync(cbm.PRESET, profiles['codex'])
    plan = extension.prepare_all(manager, mcp_ops, ext_ops)
    extension.apply(plan)
    assert json.loads(duplicate.read_text()) == {'other': True}
    data = tomlkit.parse(codex.read_text('utf-8'))
    assert data['hooks']['SessionStart'][0]['hooks'] == [foreign]
    assert len(cbm.owned_groups(data)['SessionStart']) == 1
    assert json.loads(settings.read_text())['hooks']['SessionStart'][0]['hooks'] == [foreign]
    assert all(extension.in_sync(cbm.PRESET, p) for p in profiles.values())


def test_upgrade_shared_binary_and_stale_dependencies(env, tmp_path):
    library, manager, profiles, extension = env
    mcp_ops, ext_ops = operations(env)
    extension.apply(extension.prepare_all(manager, mcp_ops, ext_ops))
    old_plugin = Path(profiles['opencode'].path).parent / 'plugins/cbm-augment.ts'
    old = old_plugin.read_text('utf-8')
    exe = tmp_path / '版本 2' / 'codebase-memory-mcp.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'fake')
    library.save({**library.get('codebase-memory-mcp'), 'command': str(exe)}, replace='codebase-memory-mcp')
    assert len(extension.refresh_ops(cbm.PRESET, manager.list())) == 3
    plan = extension.prepare_all(manager, [], ext_ops)
    assert old_plugin.read_text('utf-8') == old
    # All three MCP configs and their adapters change together.
    extension.apply(plan)
    assert all(extension.in_sync(cbm.PRESET, p) for p in profiles.values())
    assert old != old_plugin.read_text('utf-8')
    plan = extension.prepare_all(manager, [], ext_ops)
    library.save({**library.get('codebase-memory-mcp'), 'env': {'CBM_CACHE_DIR': '{DATA}/cache'}}, replace='codebase-memory-mcp')
    with pytest.raises(ValueError, match='預覽'):
        extension.apply(plan)


def test_conflict_rollback_and_dependency_failure(env, monkeypatch):
    library, manager, profiles, extension = env
    mcp_ops, ext_ops = operations(env)
    plan = extension.prepare_all(manager, mcp_ops, ext_ops)
    codex = Path(profiles['codex'].path)
    codex.write_text('# changed\n', 'utf-8')
    with pytest.raises(ValueError, match='預覽'):
        extension.apply(plan)
    plan = extension.prepare_all(manager, mcp_ops, ext_ops)
    before = {f.path: (f.path.exists(), f.original) for f in plan.files}
    real_write = extmod.atomic_write
    fail = plan.files[-1].path
    def write(path, content):
        if path == fail:
            raise OSError('injected failure')
        real_write(path, content)
    monkeypatch.setattr(extmod, 'atomic_write', write)
    with pytest.raises(RuntimeError, match='已復原'):
        extension.apply(plan)
    assert all(path.exists() == existed and (not existed or path.read_text('utf-8') == content)
               for path, (existed, content) in before.items())
    library.remove('codebase-memory-mcp')
    assert library.ext_problems(cbm.PRESET)
    with pytest.raises(ValueError, match='MCP 庫'):
        extension.prepare(ext_ops, manager.list())


def test_missing_mcp_and_unknown_commands_are_not_silently_adopted(env):
    library, manager, profiles, extension = env
    _, ops = operations(env)
    with pytest.raises(ValueError, match='待套用'):
        extension.prepare(ops, manager.list())
    for command in ['echo codebase-memory-mcp hook-augment', 'codebase-memory-mcp hook-augment && echo hi',
                    'some-other.exe hook-augment', 'echo cbm-session-reminder.cmd']:
        assert not cbm.owned_command(command)


def test_hook_bridge_context_only_and_fail_open(monkeypatch):
    from agentdock.codebase_hook import run
    calls = []
    reply = {'hookSpecificOutput': {'additionalContext': 'graph', 'permissionDecision': 'deny'}}
    def fake(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, json.dumps(reply), '')
    monkeypatch.setattr(subprocess, 'run', fake)
    launch = {'command': 'fake.exe', 'env': {'CBM_CACHE_DIR': 'isolated'}}
    payload = json.dumps({'hook_event_name': 'PreToolUse', 'tool_name': 'Grep', 'tool_input': {'pattern': 'Foo'}})
    assert json.loads(run(launch, payload)) == {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': 'graph'}}
    assert calls[0][0] == ['fake.exe', 'hook-augment']
    assert calls[0][1]['input'] == payload and calls[0][1]['timeout'] == 3
    assert calls[0][1]['env']['CBM_CACHE_DIR'] == 'isolated'
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('fake', 3)
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert run(launch, payload) == ''
    assert run(launch, 'bad json') == ''


def test_dialog_and_update_queue(env, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from agentdock.ui.agents_view import ExtensionDialog, AgentsView
    QApplication.instance() or QApplication([])
    library, manager, profiles, extension = env
    mcp_ops, ext_ops = operations(env)
    extension.apply(extension.prepare_all(manager, mcp_ops, ext_ops))
    dlg = ExtensionDialog(None, library, library.get_extension(cbm.PRESET['key']))
    dlg._save()
    assert dlg.saved == library.get_extension(cbm.PRESET['key'])
    new = ExtensionDialog(None, library)
    new._load({**cbm.PRESET, 'key': 'another-integration'})
    new._save()
    assert new.saved['integration'] == 'codebase-memory'
    library.remove_extension('another-integration')
    view = AgentsView(manager, library)
    library.save({**library.get('codebase-memory-mcp'), 'env': {'CBM_CACHE_DIR': '{DATA}/new-cache'}}, replace='codebase-memory-mcp')
    assert view.queue_updates('codebase-memory-mcp') == 3
    assert len(view.ext_ops) == 3
    view.close()


def test_opencode_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for OpenCode adapter verification')
    result = subprocess.run([node, '--experimental-vm-modules', str(ROOT / 'tests/cbm_plugin_contract.mjs'),
                             str(ROOT / 'agentdock/assets/opencode/cbm-augment.ts')],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
