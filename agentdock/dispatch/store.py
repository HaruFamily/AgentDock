"""Tasks sent from AgentDock (or taken over from a client), persisted in data/dispatch.json.

A row mirrors one client session. Its state is only "running" while the client confirms it;
after a restart it is "unknown" until the client is asked again.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentdock.files import atomic_json, load_json

FINAL = ('done', 'failed', 'interrupted')
STATES = ('running', 'unknown', *FINAL)
SOURCES = {'opencode': 'OpenCode', 'claude-code': 'Claude Code', 'codex': 'Codex', 'grok-cli': 'Grok'}
MAX_TEXT = 20000
MAX_RECENT = 8


def owner_for(profile_id: str | None, client: str) -> str:
    """Same owner as the client's Inbox MCP and activity hook, so all of them share one card."""
    identity = f'agentdock-{profile_id}' if profile_id else f'agentdock-{client}'
    return hashlib.sha256(identity.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def key_for(client: str, session_id: str) -> str:
    return f'{client}:{session_id}'


def work_id(row: dict) -> str:
    return f"session:{row['client']}:{row['session_id']}"


class DispatchStore:
    def __init__(self, directory: Path, emit: Callable[[str, dict | None], None]):
        self.file = directory / 'dispatch.json'
        self.emit = emit
        self.lock = threading.RLock()
        data = load_json(self.file, {})
        if not isinstance(data, dict):
            data = {}
        rows = data.get('rows') if isinstance(data.get('rows'), dict) else {}
        # Nothing saved proves that a client is still running.
        self.rows = {k: {**r, 'state': 'unknown' if r.get('state') == 'running' else r.get('state', 'unknown')}
                     for k, r in rows.items() if isinstance(r, dict) and r.get('session_id')}
        self.recent = [d for d in data.get('recent_dirs', []) if isinstance(d, str)][:MAX_RECENT]
        self.offline = ''

    def _save(self) -> None:
        atomic_json(self.file, {'rows': self.rows, 'recent_dirs': self.recent})

    # -- writes ---------------------------------------------------------------
    def track(self, client: str, session_id: str, *, owner: str, title: str, directory: str = '',
              prompt: str = '', state: str = 'running', project: str | None = None) -> dict:
        key = key_for(client, session_id)
        with self.lock:
            old = self.rows.get(key, {})
            row = {'client': client, 'session_id': session_id, 'owner': old.get('owner', owner),
                   'source': SOURCES.get(client, client), 'title': (title or old.get('title') or session_id)[:160],
                   'directory': directory or old.get('directory', ''), 'created_at': old.get('created_at', _now()),
                   'updated_at': _now(), 'state': state, 'prompt': prompt[:MAX_TEXT] or old.get('prompt', ''),
                   'reply': old.get('reply', ''), 'reply_message': old.get('reply_message', ''), 'doing': '',
                   'error': '', 'permissions': old.get('permissions', {}), 'forms': old.get('forms', {}),
                   'read': True, 'id': str(uuid.uuid4())}
            if project is not None or 'project' in old:
                row['project'] = project if project is not None else old['project']
            self.rows = {**self.rows, key: row}
            if directory:
                self.recent = [directory, *[d for d in self.recent if d.lower() != directory.lower()]][:MAX_RECENT]
            self._save()
        self.emit('change', None)
        return copy.deepcopy(row)

    def update(self, key: str, **changes: Any) -> dict | None:
        """Apply client observations. Reaching a final state marks the card unread once."""
        with self.lock:
            old = self.rows.get(key)
            if old is None:
                return None
            row = {**old, **changes}
            for name in ('prompt', 'reply', 'error'):
                if isinstance(row.get(name), str):
                    row[name] = row[name][-MAX_TEXT:]
            if row == old:
                return copy.deepcopy(old)
            asked = set(row.get('permissions', {})) - set(old.get('permissions', {}))
            questioned = set(row.get('forms', {})) - set(old.get('forms', {}))
            finished = row['state'] in FINAL and old['state'] not in FINAL
            if finished:
                row['doing'] = ''
            if finished or asked or questioned:
                row['read'] = False
                row['id'] = str(uuid.uuid4())
            elif row['state'] == 'running' and old['state'] != 'running':
                row['id'] = str(uuid.uuid4())
            row['updated_at'] = _now()
            self.rows = {**self.rows, key: row}
            self._save()
        self.emit('dispatch-attention' if asked or questioned else 'change', copy.deepcopy(row))
        return copy.deepcopy(row)

    def rename(self, key: str, title: str) -> None:
        """A name the user gave; clients no longer overwrite it."""
        title = ' '.join(title.split())[:160]
        with self.lock:
            row = self.rows.get(key)
            if row is None or not title:
                return
            self.rows = {**self.rows, key: {**row, 'title': title, 'custom_title': True}}
            self._save()
        self.emit('change', None)

    def set_project(self, key: str, project: str) -> None:
        """An explicit move; '' means unsorted. Only organises the list, the session's folder stays."""
        with self.lock:
            row = self.rows.get(key)
            if row is None or row.get('project') == project:
                return
            self.rows = {**self.rows, key: {**row, 'project': project}}
            self._save()
        self.emit('change', None)

    def mark_read(self, key: str, row_id: str) -> bool:
        with self.lock:
            row = self.rows.get(key)
            if not row or row['id'] != row_id or row['read']:
                return False
            self.rows = {**self.rows, key: {**row, 'read': True}}
            self._save()
        return True

    def remove(self, key: str) -> None:
        """Only the card; the client session is untouched."""
        with self.lock:
            if key not in self.rows:
                return
            self.rows = {k: v for k, v in self.rows.items() if k != key}
            self._save()
        self.emit('change', None)

    def set_offline(self, message: str) -> None:
        if message != self.offline:
            self.offline = message
            self.emit('change', None)

    # -- reads ----------------------------------------------------------------
    def get(self, key: str) -> dict | None:
        row = self.rows.get(key)
        return copy.deepcopy(row) if row else None

    def find(self, client: str, session_id: str) -> dict | None:
        return self.get(key_for(client, session_id))

    def keys(self, client: str | None = None) -> list[str]:
        return [k for k, r in self.rows.items() if client is None or r['client'] == client]

    def tasks(self) -> list[dict]:
        tasks = []
        for key, row in self.rows.items():
            approvals = {pid: _permission_text(p) for pid, p in row.get('permissions', {}).items()}
            state = 'approval' if approvals else 'waiting' if row.get('forms') else \
                row['state'] if row['state'] in ('running', 'unknown') else '' if row['read'] else 'unread'
            tasks.append({'owner': row['owner'], 'work_id': work_id(row), 'work_title': row['title'],
                          'source': row['source'], 'state': state, 'updated_at': row['updated_at'], 'pending': [],
                          'latest': {**copy.deepcopy(row), 'key': key, 'kind': 'dispatch', 'approvals': approvals,
                                     'offline': self.offline}})
        return tasks

    def attention(self) -> int:
        return sum(bool(r.get('permissions') or r.get('forms')) or (not r['read'] and r['state'] in FINAL)
                   for r in self.rows.values())


def _permission_text(permission: dict) -> str:
    resources = ', '.join(str(r) for r in permission.get('resources', []) if r)
    action = str(permission.get('action', ''))
    return f'{action}：{resources}' if resources else action
