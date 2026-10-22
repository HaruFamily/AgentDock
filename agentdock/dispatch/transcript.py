"""A conversation's visible history, built from stored messages and kept current by live events.

Items, oldest first:
  {'kind': 'user', 'id', 'text', 'files'}
  {'kind': 'assistant', 'id', 'parts': [{'type': 'text', 'key', 'text'} | {'type': 'reasoning', 'key', 'text'} |
                                         {'type': 'tool', 'key', 'name', 'input', 'status', 'output', 'error', 'diff'}]}
  {'kind': 'end', 'id', 'outcome', 'error'}
"""
from __future__ import annotations

from typing import Any

MAX_OUTPUT = 4000


def _tool_output(content: Any) -> str:
    if not isinstance(content, list):
        return ''
    text = '\n'.join(str(c.get('text', '')) for c in content if isinstance(c, dict) and c.get('type') == 'text')
    return text[:MAX_OUTPUT]


def _error(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get('message') or value.get('type') or '')
    return str(value or '')


def item_from_message(message: dict) -> dict | None:
    kind = message.get('type')
    mid = message.get('id', '')
    if kind == 'user':
        payload = message.get('payload') or {}
        return {'kind': 'user', 'id': mid, 'text': str(message.get('text') or payload.get('text') or ''),
                'files': [str(f) for f in message.get('files') or payload.get('files') or []]}
    if kind == 'assistant':
        parts = []
        ordinal = 0   # live events number text blocks among text blocks only
        thought = 0   # and reasoning blocks among reasoning blocks
        for index, part in enumerate(message.get('content') or []):
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                if part.get('text'):
                    parts.append({'type': 'text', 'key': f'text:{ordinal}', 'text': str(part['text'])})
                ordinal += 1
            elif part.get('type') == 'reasoning':
                if part.get('text'):
                    parts.append({'type': 'reasoning', 'key': f'reasoning:{thought}', 'text': str(part['text'])})
                thought += 1
            elif part.get('type') == 'tool':
                state = part.get('state') or {}
                parts.append({'type': 'tool', 'key': f"tool:{part.get('id', index)}", 'name': str(part.get('name', '')),
                              'input': state.get('input') if isinstance(state.get('input'), dict) else {},
                              'status': str(state.get('status', '')), 'output': _tool_output(state.get('content')),
                              'error': _error(state.get('error')), 'diff': str(state.get('diff') or '')})
        return {'kind': 'assistant', 'id': mid, 'parts': parts, 'error': _error(message.get('error'))}
    if kind == 'idle':
        return {'kind': 'end', 'id': mid, 'outcome': str(message.get('outcome', '')), 'error': _error(message.get('error'))}
    return None


class Transcript:
    def __init__(self, messages_oldest_first: list[dict] | None = None):
        self.items: list[dict] = []
        self._ids: set[str] = set()
        self.prepend(messages_oldest_first or [])

    def prepend(self, messages_oldest_first: list[dict]) -> list[dict]:
        older = [i for i in (item_from_message(m) for m in messages_oldest_first) if i and i['id'] not in self._ids]
        self._ids.update(i['id'] for i in older)
        self.items = older + self.items
        return older

    def _assistant(self, mid: str) -> dict:
        for item in reversed(self.items):
            if item['kind'] == 'assistant' and item['id'] == mid:
                return item
        item = {'kind': 'assistant', 'id': mid, 'parts': [], 'error': ''}
        self.items.append(item)
        self._ids.add(mid)
        return item

    @staticmethod
    def _part(item: dict, key: str, **defaults: Any) -> dict:
        for part in item['parts']:
            if part['key'] == key:
                return part
        part = {'key': key, **defaults}
        item['parts'].append(part)
        return part

    def apply(self, event: dict) -> dict | None:
        """Apply one live event; return the changed item (same object) or None."""
        kind = event.get('type', '')
        data = event.get('data') or {}
        mid = data.get('assistantMessageID', '')
        if kind == 'session.inbox.enqueued':
            item = data.get('item') or {}
            uid = data.get('inboxID', '')
            if item.get('type') != 'user' or not uid or uid in self._ids:
                return None
            payload = item.get('payload') or {}
            user = {'kind': 'user', 'id': uid, 'text': str(payload.get('text', '')),
                    'files': [str(f) for f in payload.get('files') or []]}
            self.items.append(user)
            self._ids.add(uid)
            return user
        if kind == 'session.step.started' and mid:
            return self._assistant(mid)
        if kind in ('session.text.started', 'session.text.delta', 'session.text.ended') and mid:
            item = self._assistant(mid)
            part = self._part(item, f"text:{data.get('ordinal', 0)}", type='text', text='')
            if kind == 'session.text.delta':
                part['text'] += str(data.get('delta', ''))
            elif kind == 'session.text.ended':
                part['text'] = str(data.get('text', part['text']))
            return item
        if kind in ('session.reasoning.started', 'session.reasoning.delta', 'session.reasoning.ended') and mid:
            item = self._assistant(mid)
            part = self._part(item, f"reasoning:{data.get('ordinal', 0)}", type='reasoning', text='')
            if kind == 'session.reasoning.delta':
                part['text'] += str(data.get('delta', ''))
            elif kind == 'session.reasoning.ended':
                part['text'] = str(data.get('text', part['text']))
            return item
        if kind.startswith('session.tool.') and mid and data.get('id'):
            item = self._assistant(mid)
            part = self._part(item, f"tool:{data['id']}", type='tool', name=str(data.get('name', '')), input={},
                              status='pending', output='', error='', diff='')
            if data.get('diff'):
                part['diff'] = str(data['diff'])
            if data.get('name'):
                part['name'] = str(data['name'])
            if kind == 'session.tool.called':
                part['input'] = data.get('input') if isinstance(data.get('input'), dict) else {}
                part['status'] = 'running'
            elif kind == 'session.tool.success':
                part['status'], part['output'] = 'completed', _tool_output(data.get('content'))
            elif kind == 'session.tool.failed':
                part['status'], part['error'] = 'error', _error(data.get('error'))
            elif kind not in ('session.tool.input.started', 'session.tool.input.ended', 'session.tool.progress'):
                return None
            return item
        if kind in ('session.execution.succeeded', 'session.execution.failed', 'session.execution.interrupted'):
            end = {'kind': 'end', 'id': event.get('id') or f'end:{len(self.items)}',
                   'outcome': kind.rsplit('.', 1)[1], 'error': _error(data.get('error'))}
            self.items.append(end)
            self._ids.add(end['id'])
            return end
        return None


def tool_summary(part: dict) -> str:
    """One line for a collapsed tool row."""
    data = part.get('input') or {}
    for name in ('command', 'filePath', 'path', 'pattern', 'url', 'query', 'description'):
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return ' '.join(value.split())[:120]
    if part.get('name') == 'question':
        questions = data.get('questions') or []
        if questions and isinstance(questions[0], dict):
            return str(questions[0].get('question', ''))[:120]
    return ''
