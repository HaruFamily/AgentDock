"""Fail-open CBM hook bridge. No GUI imports; stdout is only hook context JSON."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys


def run(launch, payload):
    try:
        event = json.loads(payload).get('hook_event_name')
        if event not in ('SessionStart', 'SubagentStart', 'PreToolUse', 'PostToolUse'):
            return ''
        result = subprocess.run([launch['command'], 'hook-augment'], input=payload,
                                capture_output=True, timeout=3, text=True, encoding='utf-8',
                                env={**os.environ, **launch.get('env', {}), 'CBM_LOG_LEVEL': 'error'},
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or len(result.stdout) > 1024 * 1024:
            return ''
        extra = json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext')
        if not isinstance(extra, str) or not extra.strip():
            return ''
        return json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': extra}}, ensure_ascii=False)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError):
        return ''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--launch', required=True)
    args = parser.parse_args()
    try:
        launch = json.loads(base64.b64decode(args.launch, validate=True))
        payload = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(payload) <= 256 * 1024:
            output = run(launch, payload.decode('utf-8-sig'))
            if output:
                sys.stdout.buffer.write(output.encode('utf-8'))
    except (ValueError, TypeError, OSError):
        pass
