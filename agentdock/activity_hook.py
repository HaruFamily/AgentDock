"""Notification-only hook adapter. Never returns an approval decision."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

# Executed by absolute file path, independent of the client's working directory.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def normalize(client, raw):
    session = raw.get('session_id')
    if not isinstance(session, str) or not session or raw.get('agent_id'):
        return None
    event = raw.get('hook_event_name')
    state = {'UserPromptSubmit': 'running', 'PreToolUse': 'running', 'PostToolUse': 'running',
             'PostToolUseFailure': 'running', 'Stop': 'idle', 'SessionEnd': 'ended',
             'Interrupt': 'interrupted', 'StopFailure': 'error'}.get(event)
    detail = ''
    if client == 'codex' and event == 'PermissionRequest':
        state, detail = 'approval', '授權請求已發出，請回 Codex 確認是否需手動批准'
    if client == 'claude-code' and event == 'Notification' and raw.get('notification_type') == 'permission_prompt':
        state, detail = 'approval', '請回 Claude Code 批准'
    if not state:
        return None
    cwd = str(raw.get('cwd', '')).replace('\\', '/').rstrip('/').split('/')[-1]
    return {'client': client, 'session_id': session[:200], 'state': state,
            'title': f"{cwd or '原生工作階段'} · {session[:8]}"[:160], 'detail': detail,
            'request_id': str(raw.get('tool_use_id') or '')[:200]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', choices=['codex', 'claude-code'], required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--data', required=True)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(sys.stdin.read(1_000_001))
        payload = normalize(args.client, raw)
        if payload:
            os.environ['AGENTDOCK_DATA_DIR'] = args.data
            from agentdock.client import _find
            client = _find()
            if client:
                client.owner = hashlib.sha256(args.identity.encode()).hexdigest()
                client.request('/activity', payload, timeout=0.7)
    except Exception:
        # Observability cannot block tools or change the client's permission flow.
        pass
    sys.stdout.write('{}\n')


if __name__ == '__main__':
    main()
