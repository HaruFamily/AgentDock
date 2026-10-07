"""Native lifecycle observations, independent of agent-authored task results."""
from __future__ import annotations

import copy
import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentdock.files import atomic_json, load_json

FRESH_SECONDS = 120
SOURCES = {'codex': 'Codex', 'claude-code': 'Claude Code', 'opencode': 'OpenCode'}


class ActivityInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client: Literal['codex', 'claude-code', 'opencode']
    session_id: str = Field(min_length=1, max_length=200)
    state: Literal['running', 'approval', 'approval_done', 'idle', 'ended', 'interrupted', 'error']
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field('', max_length=300)
    request_id: str = Field('', max_length=200)


class ActivityStore:
    def __init__(self, directory: Path, emit):
        self.file = directory / 'activity.json'
        self.emit = emit
        self.lock = threading.RLock()
        self.rows = load_json(self.file, {})
        # A saved observation is never proof that a client is still running.
        self.live = {}

    def receive(self, owner, raw):
        payload = ActivityInput.model_validate(raw).model_dump()
        key = hashlib.sha256(f"{owner}:{payload['client']}:{payload['session_id']}".encode()).hexdigest()
        with self.lock:
            old = self.rows.get(key, {})
            approvals = dict(old.get('approvals', {}))
            state = payload['state']
            if state == 'approval':
                approvals[payload['request_id'] or 'pending'] = payload['detail']
            elif state == 'approval_done':
                approvals.pop(payload['request_id'] or 'pending', None)
                state = 'running'
            elif state in ('idle', 'ended', 'interrupted', 'error'):
                approvals.clear()
            # Unrelated parallel tool activity must not dismiss an approval.
            if approvals:
                state = 'approval'
            notify = payload['state'] == 'approval' and (old.get('state') != 'approval' or approvals != old.get('approvals'))
            row = {**payload, 'state': state, 'approvals': approvals, 'owner': owner,
                   'id': str(uuid.uuid4()), 'at': time.time(),
                   'read': False if notify or state in ('idle', 'ended', 'interrupted', 'error') else True}
            rows = {**self.rows, key: row}
            atomic_json(self.file, rows)
            self.rows = rows
            self.live[key] = time.monotonic()
        self.emit('native-approval' if notify else 'change', copy.deepcopy(row))
        return row['id']

    def tasks(self):
        tasks = []
        for key, row in self.rows.items():
            state = row['state']
            seen = self.live.get(key)
            fresh = seen is not None and time.monotonic() - seen <= FRESH_SECONDS
            if state in ('running', 'approval') and not fresh:
                state = 'unknown'
            stamp = datetime.fromtimestamp(row['at'], timezone.utc).isoformat()
            tasks.append({'owner': row['owner'], 'work_id': 'native:' + key,
                          'work_title': row['title'], 'source': SOURCES[row['client']],
                          'state': state, 'updated_at': stamp, 'pending': [],
                          'latest': {**row, 'kind': 'native', 'state': state}})
        return tasks

    def attention(self):
        return sum(t['state'] == 'approval' or (not t['latest']['read'] and t['state'] not in ('running', 'unknown'))
                   for t in self.tasks())

    def mark_read(self, owner, work_id, ids):
        key = work_id.removeprefix('native:')
        with self.lock:
            row = self.rows.get(key)
            if not row or row['owner'] != owner or row['id'] not in ids or row['read']:
                return False
            rows = {**self.rows, key: {**row, 'read': True}}
            atomic_json(self.file, rows)
            self.rows = rows
        return True

    def remove(self, owner, work_id):
        key = work_id.removeprefix('native:')
        with self.lock:
            if key not in self.rows or self.rows[key]['owner'] != owner:
                return
            rows = {k: v for k, v in self.rows.items() if k != key}
            atomic_json(self.file, rows)
            self.rows = rows
            self.live.pop(key, None)
        self.emit('change', None)
