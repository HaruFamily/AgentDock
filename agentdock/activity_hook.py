"""Notification-only hook adapter. Never returns an approval decision."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys

# Executed by absolute file path, independent of the client's working directory.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def normalize(client, raw):
    raw = hook_input(client, raw)
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
    if client == 'grok-cli':
        if event == 'StopCancelled':
            state = 'interrupted'
        elif event == 'Notification' and raw.get('notification_type') == 'permission_prompt':
            state, detail = 'approval', '請回 Grok CLI 批准'
    if not state:
        return None
    # Desktop composer suggestions run their own ephemeral sessions and fire
    # ordinary lifecycle hooks. Inspect only the explicit app-generated prefix;
    # never forward/store the prompt or read a transcript to classify them.
    prompt = raw.get('prompt')
    background = (client == 'codex' and event == 'UserPromptSubmit'
                  and isinstance(prompt, str) and prompt.startswith(
                      'This is an app-generated request for a suggested next user message. '
                      'The user did not write this message.'))
    cwd = str(raw.get('cwd', '')).replace('\\', '/').rstrip('/').split('/')[-1]
    return {'client': client, 'session_id': session[:200], 'state': state,
            'title': f"{cwd or '原生工作階段'} · {session[:8]}"[:160], 'detail': detail,
            'request_id': str(raw.get('tool_use_id') or '')[:200],
            **({'background': True} if background else {})}


def hook_input(client, raw):
    if client != 'grok-cli':
        # Grok imports Claude hooks by default. Never report their events with
        # a Claude owner, even if a future version adds snake_case aliases.
        return {} if 'hookEventName' in raw and 'sessionId' in raw else raw
    return {'session_id': raw.get('sessionId'), 'hook_event_name': raw.get('hook_event_name'),
            'agent_id': raw.get('subagentType') or raw.get('agentId'),
            'cwd': raw.get('cwd'), 'tool_use_id': raw.get('toolUseId'),
            'notification_type': raw.get('notificationType'), 'tool_name': raw.get('toolName', ''),
            'tool_input': None if raw.get('toolInputTruncated') else raw.get('toolInput')}


def conversation_link(client, raw):
    raw = hook_input(client, raw)
    session = raw.get('session_id')
    name = raw.get('tool_name', '')
    args = raw.get('tool_input')
    if (raw.get('agent_id') or raw.get('hook_event_name') not in ('PreToolUse', 'PostToolUse')
            or not isinstance(session, str) or not session
            or not re.fullmatch(r'(?:mcp__)?(?:inbox|agentchat)(?:__|_)(?:ask_user|report_to_user)', name)
            or not isinstance(args, dict) or not isinstance(args.get('work_id'), str)):
        return None
    return {'work_id': args['work_id'], 'conversation_id': f'session:{client}:{session}'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', choices=['codex', 'claude-code', 'grok-cli'], required=True)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--data', required=True)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(sys.stdin.read(1_000_001))
        payload = normalize(args.client, raw)
        link = conversation_link(args.client, raw)
        if payload or link:
            os.environ['AGENTDOCK_DATA_DIR'] = args.data
            from agentdock.client import _find
            client = _find()
            if client:
                client.owner = hashlib.sha256(args.identity.encode()).hexdigest()
                if link:
                    client.request('/conversation-links', link, timeout=0.7)
                if payload:
                    client.request('/activity', payload, timeout=0.7)
    except Exception:
        # Observability cannot block tools or change the client's permission flow.
        pass
    sys.stdout.write('{}\n')


if __name__ == '__main__':
    main()
