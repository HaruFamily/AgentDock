"""Projects: a name, a root folder where new conversations run, and a default Agent.

A conversation belongs to the project it was explicitly moved to; otherwise to the project
whose root contains its working folder; otherwise it is unsorted.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agentdock.files import atomic_json, load_json

UNSORTED = ''
PROJECT_ICONS = ('folder', 'tools', 'chat', 'shield', 'agent')


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ''


class ProjectStore:
    def __init__(self, directory: Path, emit: Callable[[], None] = lambda: None):
        self.file = directory / 'projects.json'
        self.emit = emit
        self.lock = threading.RLock()
        data = load_json(self.file, {})
        rows = data.get('projects') if isinstance(data, dict) else None
        self.projects: list[dict[str, Any]] = [
            {'id': str(p['id']), 'name': str(p.get('name') or 'Project'), 'root': str(p.get('root', '')),
             'agent': str(p.get('agent') or 'opencode'),
             'icon': p.get('icon') if p.get('icon') in PROJECT_ICONS else 'folder',
             'color': p.get('color', '') if re.fullmatch(r'#[0-9a-fA-F]{6}', str(p.get('color', ''))) else ''}
            for p in (rows or []) if isinstance(p, dict) and p.get('id')]

    def _save(self) -> None:
        atomic_json(self.file, {'projects': self.projects})
        self.emit()

    def list(self) -> list[dict]:
        return [dict(p) for p in self.projects]

    def get(self, pid: str) -> dict | None:
        return next((dict(p) for p in self.projects if p['id'] == pid), None)

    def add(self, name: str, root: str, agent: str = 'opencode') -> dict:
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError('名稱需為 1–80 字。')
        if not os.path.isdir(root):
            raise ValueError('找不到這個資料夾。')
        project = {'id': str(uuid.uuid4()), 'name': name, 'root': os.path.normpath(root), 'agent': agent, 'color': '', 'icon': 'folder'}
        with self.lock:
            self.projects = [*self.projects, project]
            self._save()
        return dict(project)

    def update(self, pid: str, **changes: Any) -> dict:
        with self.lock:
            current = self.get(pid)
            if current is None:
                raise KeyError('專案已不存在。')
            if 'name' in changes:
                changes['name'] = str(changes['name']).strip()
                if not changes['name'] or len(changes['name']) > 80:
                    raise ValueError('名稱需為 1–80 字。')
            if 'root' in changes:
                if not os.path.isdir(changes['root']):
                    raise ValueError('找不到這個資料夾。')
                changes['root'] = os.path.normpath(changes['root'])
            if 'icon' in changes and changes['icon'] not in PROJECT_ICONS:
                raise ValueError('請選擇有效的專案圖示。')
            if 'color' in changes and changes['color'] != '' and not re.fullmatch(r'#[0-9a-fA-F]{6}', str(changes['color'])):
                raise ValueError('請選擇有效的圖示顏色。')
            updated = {**current, **{k: v for k, v in changes.items() if k in ('name', 'root', 'agent', 'color', 'icon')}}
            self.projects = [updated if p['id'] == pid else p for p in self.projects]
            self._save()
        return dict(updated)

    def remove(self, pid: str) -> None:
        """Conversations stay; they fall back to folder matching or unsorted."""
        with self.lock:
            self.projects = [p for p in self.projects if p['id'] != pid]
            self._save()

    def project_of(self, row: dict) -> str:
        """The project id of a conversation, or UNSORTED."""
        ids = {p['id'] for p in self.projects}
        explicit = row.get('project')
        if explicit is not None:
            return explicit if explicit in ids else UNSORTED
        folder = _norm(row.get('directory', ''))
        best = ''
        best_len = -1
        for p in self.projects:
            root = _norm(p['root'])
            if root and (folder == root or folder.startswith(root.rstrip(os.sep) + os.sep)) and len(root) > best_len:
                best, best_len = p['id'], len(root)
        return best
