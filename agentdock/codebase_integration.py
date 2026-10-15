"""Codebase Memory adapters. Read/plan only; ExtensionManager owns all writes."""
from __future__ import annotations

import base64
import copy
import json
import re
import shlex
from pathlib import Path, PureWindowsPath

from agentdock.agents import _parse, _servers
from agentdock.paths import console_python

PRESET = {
    'key': 'codebase-memory-integration', 'type': 'command',
    'command': '{MCP}', 'mcp': 'codebase-memory-mcp', 'integration': 'codebase-memory', 'path': False,
    'description': 'Codebase Memory 整合：由 AgentDock 管理 OpenCode 外掛及 Codex／Claude Code hooks，沿用所選 MCP 的執行檔。技能與 AGENTS.md 仍由原安裝管理；上游重裝後可用「全部更新」恢復接入。',
    'hooks': {
        'codex': {'event': 'SessionStart', 'matcher': '*', 'command': '{MCP}', 'events': ['SubagentStart']},
        'claude-code': {'event': 'SessionStart', 'matcher': '*', 'command': '{MCP}',
                        'events': ['SubagentStart', 'PreToolUse', 'PostToolUse']},
        'opencode': {'plugin': 'cbm-augment.ts', 'source': '{AGENTDOCK}/agentdock/assets/opencode/cbm-augment.ts'},
    },
}


def managed(entry):
    return entry.get('integration') == 'codebase-memory'


def launch(library, entry):
    source = library.get(entry.get('mcp', ''))
    if not source:
        raise ValueError('請先在 MCP 庫加入 ' + entry.get('mcp', 'codebase-memory-mcp') + '。')
    if source['type'] not in ('github', 'download', 'command'):
        raise ValueError('Codebase Memory 整合需要本機原生執行檔，不能使用遠端或套件啟動器。')
    spec = library.spec(source, None)
    exe = Path(spec['command'])
    if not exe.is_absolute() or not exe.is_file():
        raise ValueError('Codebase Memory MCP 必須指向存在的執行檔完整路徑。')
    if spec['args']:
        raise ValueError('Codebase Memory MCP 含自訂啟動參數，請先確認 hook-augment 的接法。')
    return {'command': str(exe), 'env': spec['env']}


def desired_groups(library, entry, kind):
    payload = base64.b64encode(json.dumps(launch(library, entry), ensure_ascii=False).encode()).decode()
    argv = [console_python(), str(library.root / 'agentdock/codebase_hook.py'), '--launch', payload]
    command = ' '.join(shlex.quote(a.replace('\\', '/')) for a in argv)
    handler = {'type': 'command', 'command': command, 'timeout': 5}
    if kind == 'codex':
        handler['command_windows'] = '& ' + ' '.join("'" + a.replace("'", "''") + "'" for a in argv)
    matchers = {'SessionStart': 'startup|resume|clear|compact', 'SubagentStart': '*'}
    if kind == 'claude-code':
        matchers.update(PreToolUse='Grep|Glob|Bash', PostToolUse='Read')
    return {event: [{'matcher': matcher, 'hooks': [copy.deepcopy(handler)]}]
            for event, matcher in matchers.items()}


def owned_command(command):
    """Recognize exact CBM invocations, never a substring in an unrelated command."""
    if not isinstance(command, str):
        return False
    try:
        args = shlex.split(command.replace('\\', '/'))
    except ValueError:
        return False
    if args[:1] == ['&']:
        args = args[1:]
    if len(args) == 2 and PureWindowsPath(args[0]).name.lower() in ('codebase-memory-mcp', 'codebase-memory-mcp.exe'):
        return args[1] == 'hook-augment'
    if len(args) == 4 and args[1].endswith('/agentdock/codebase_hook.py') and args[2] == '--launch':
        return PureWindowsPath(args[0]).name.lower() in ('python', 'python.exe', 'python3')
    # Upstream Windows Claude adapters. Preserve their files; replace only their registrations.
    if len(args) == 6 and args[:5] == ['cmd.exe', '/d', '/v:off', '/s', '/c']:
        path = args[5].strip('"')
        return bool(re.fullmatch(r'%USERPROFILE%/\.claude/hooks/cbm-(session-reminder|subagent-reminder|code-discovery-gate)\.cmd', path))
    return False


def owned_handler(handler):
    if not isinstance(handler, dict) or handler.get('type') != 'command':
        return False
    commands = [handler[k] for k in ('command', 'command_windows', 'commandWindows') if k in handler]
    return bool(commands) and all(owned_command(c) for c in commands)


def owned_groups(data):
    hooks = data.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('hooks 區塊格式必須是物件。')
    out = {}
    for event in ('SessionStart', 'SubagentStart', 'PreToolUse', 'PostToolUse'):
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f'hooks.{event} 必須是陣列。')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise ValueError(f'hooks.{event} 的 hook 格式不正確。')
            ours = [h for h in group.get('hooks', []) if owned_handler(h)]
            if ours:
                out.setdefault(event, []).append({**group, 'hooks': ours})
    return out


def reconcile(data, desired):
    """Remove only recognized handlers; preserve unrelated handlers and TOML comments."""
    owned_groups(data)  # validate before editing
    hooks = data.get('hooks', {})
    for event in ('SessionStart', 'SubagentStart', 'PreToolUse', 'PostToolUse'):
        groups = hooks.get(event, [])
        for i in range(len(groups) - 1, -1, -1):
            inner = groups[i].get('hooks', [])
            if not any(owned_handler(h) for h in inner):
                continue
            for j in range(len(inner) - 1, -1, -1):
                if owned_handler(inner[j]):
                    del inner[j]
            if not inner:
                del groups[i]
        if event in hooks and not groups:
            del hooks[event]
    if desired:
        if 'hooks' not in data:
            data['hooks'] = {}
        for event, groups in desired.items():
            if event not in data['hooks']:
                data['hooks'][event] = []
            for group in groups:
                data['hooks'][event].append(copy.deepcopy(group))
    elif 'hooks' in data and not data['hooks']:
        del data['hooks']


def mcp_matches(library, entry, profile, text):
    source = library.get(entry['mcp'])
    current = _servers(profile.kind, _parse(profile.kind, text)).get(entry['mcp'])
    return bool(source and current and library.in_sync(source, profile.kind, profile, current))
