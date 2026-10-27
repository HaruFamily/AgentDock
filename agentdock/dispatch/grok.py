"""Grok (Grok Build CLI) through ACP: `grok agent stdio`, JSON-RPC 2.0 over stdio.

One agent process serves every Grok conversation while AgentDock runs. Sessions are Grok's own
(`~/.grok/sessions`); one that this process has not opened yet is opened with `session/load`,
which replays its history as `session/update` notifications. That replay is the history shown in
the window; afterwards the live updates extend the same record.

- Replies: `agent_message_chunk`; tools: `tool_call` / `tool_call_update`. Updates carry
  `_meta.promptId`, which names the assistant message, so replayed and live items line up.
- Approvals: `session/request_permission` (ACP options allow_once / allow_always / reject_once).
- Questions: Grok's `_x.ai/ask_user_question` request, answered with
  `{outcome: "accepted", answers: {question: label | [labels] | text}}` or `{outcome: "cancelled"}`.
- Stop: the `session/cancel` notification; the prompt then ends with stopReason "cancelled".
The user's Grok permission mode and rules decide what needs asking; AgentDock never sets yolo.
"""
from __future__ import annotations

import os
from datetime import datetime
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from agentdock.dispatch.adapter import (MAX_OUTPUT, Adapter, ChildProcess, ClientError, child_env, first_file,
                                        image_type, npm_dir, page, read_image, text_content)

CLIENT = 'grok-cli'
TIMEOUT = 60
LOAD_TIMEOUT = 120
ASK = '_x.ai/ask_user_question'
REWIND = '_x.ai/rewind/execute'
# Set when a session is created (ACP session/new `_meta`); Grok has no way to switch them afterwards.
MODES = [{'id': 'ask', 'label': '詢問'}, {'id': 'auto', 'label': '自動判斷'}, {'id': 'yolo', 'label': '全部允許'}]
DECISIONS = {'once': ('allow_once',), 'always': ('allow_always', 'allow_once'), 'reject': ('reject_once', 'reject_always')}


def find_executable() -> str | None:
    """grok.exe from the official installer (~/.grok/bin) first, then npm's unpacked binary, then PATH."""
    home = Path.home()
    candidates: list[Path | str | None] = [home / '.grok' / 'bin' / 'grok.exe', home / '.grok' / 'bin' / 'grok']
    found = shutil.which('grok')
    folders = npm_dir()
    if found and Path(found).suffix.lower() == '.exe':
        candidates.append(found)
    elif found and Path(found).suffix.lower() in ('.cmd', '.ps1', ''):
        folders.insert(0, Path(found).parent)
    for folder in folders:
        native = folder / 'node_modules' / '@xai-official' / 'grok' / 'bin'
        candidates += [native / 'grok-native.exe', native / 'grok-native']
    if found:
        candidates.append(found)
    return first_file(candidates)


def _blocks_text(content: Any) -> str:
    """Text of ACP tool-call content: [{type: content, content: {type: text, text}} | {type: diff, …}]."""
    parts = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        inner = block.get('content') if block.get('type') == 'content' else block
        if isinstance(inner, dict) and inner.get('type') == 'text' and inner.get('text'):
            parts.append(str(inner['text']))
        elif block.get('type') == 'diff':
            parts.append(f"{block.get('path', '')}\n{block.get('newText', '')}".strip())
    return '\n'.join(parts)[:MAX_OUTPUT]


def _diff(content: Any) -> str:
    """Unified diff of ACP `diff` content blocks (path, oldText, newText)."""
    import difflib
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get('type') == 'diff':
            path = str(block.get('path', ''))
            old = str(block.get('oldText') or '').splitlines()
            new = str(block.get('newText') or '').splitlines()
            out += list(difflib.unified_diff(old, new, path, path, lineterm=''))
    return '\n'.join(out)[:MAX_OUTPUT * 5]


def _tool_name(update: dict) -> str:
    tool = ((update.get('_meta') or {}).get('x.ai/tool') or {})
    return str(tool.get('name') or update.get('kind') or update.get('title') or 'tool')


def _tool_input(update: dict) -> dict:
    tool = ((update.get('_meta') or {}).get('x.ai/tool') or {})
    data = tool.get('input') if isinstance(tool.get('input'), dict) else update.get('rawInput')
    if not isinstance(data, dict):
        return {}
    data = {k: v for k, v in data.items() if k != 'variant'}
    if not any(isinstance(data.get(k), str) and data[k] for k in ('command', 'path', 'filePath', 'pattern', 'query', 'url')):
        if update.get('title'):
            data.setdefault('description', str(update['title']))
    return data


class Record:
    """One session's conversation as OpenCode-shaped messages, fed by ACP session updates.

    Used for both the replay of `session/load` and live updates; with `emit` it also reports live
    events in the shape the chat window applies.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._tools: dict[str, dict] = {}
        self._segment: dict[str, dict | None] = {}     # message -> the text / reasoning part being written
        self._ordinal: dict[tuple[str, str], int] = {}
        self.current = ''                              # the assistant message of the running prompt
        self.tokens = 0                                # context size reported with the latest update
        self.model = ''

    def user(self, mid: str, text: str, files: list[str] | None = None) -> None:
        if mid not in self._by_id:
            message = {'type': 'user', 'id': mid, 'text': text, **({'files': files} if files else {})}
            self.messages.append(message)
            self._by_id[mid] = message

    def _assistant(self, mid: str, emit) -> dict:
        message = self._by_id.get(mid)
        if message is None:
            message = {'type': 'assistant', 'id': mid, 'content': []}
            self.messages.append(message)
            self._by_id[mid] = message
            if emit:
                emit('session.step.started', assistantMessageID=mid)
        return message

    def close_text(self, mid: str, emit) -> None:
        part = self._segment.get(mid)
        if part is not None:
            self._segment[mid] = None
            if emit:
                kind = 'session.reasoning.ended' if part['type'] == 'reasoning' else 'session.text.ended'
                emit(kind, assistantMessageID=mid, ordinal=part['ordinal'], text=part['text'])

    def prompt_index(self, message_id: str) -> int:
        """Which user prompt (0-based) `message_id` belongs to: Grok forks by prompt."""
        index = -1
        for message in self.messages:
            if message['type'] == 'user':
                index += 1
            if message['id'] == message_id:
                return max(index, 0)
        raise ClientError('找不到要分叉的那則訊息。')

    def cut(self, message_id: str) -> None:
        """Forget the message `message_id` and everything after it (after a rewind)."""
        index = next((i for i, m in enumerate(self.messages) if m['id'] == message_id), len(self.messages))
        for message in self.messages[index:]:
            self._by_id.pop(message['id'], None)
        self.messages = self.messages[:index]
        self.current = ''

    def apply(self, update: dict, meta: dict, emit: Callable[..., None] | None = None) -> None:
        kind = update.get('sessionUpdate')
        if isinstance(meta.get('totalTokens'), int) and meta['totalTokens'] > 0:
            self.tokens = meta['totalTokens']
        if kind == 'user_message_chunk':
            content = update.get('content') or {}
            previous = self.messages[-1] if self.messages else None
            if not (previous and previous['type'] == 'user' and previous.get('_chunked')):
                self.user(str(meta.get('eventId') or uuid.uuid4().hex), '')
                previous = self.messages[-1]
                previous['_chunked'] = True
            if content.get('type') == 'text' and content.get('text'):
                previous['text'] += str(content['text'])
            elif content.get('type') in ('image', 'resource_link', 'resource'):
                name = str(content.get('name') or content.get('uri') or '圖片').rsplit('/', 1)[-1]
                previous.setdefault('files', []).append(name)
            return
        if kind not in ('agent_message_chunk', 'agent_thought_chunk', 'tool_call', 'tool_call_update'):
            return
        mid = str(meta.get('promptId') or self.current or uuid.uuid4().hex)
        self.current = mid
        message = self._assistant(mid, emit)
        if kind in ('agent_message_chunk', 'agent_thought_chunk'):
            content = update.get('content') or {}
            if content.get('type') != 'text':
                return
            delta = str(content.get('text', ''))
            kind_of = 'text' if kind == 'agent_message_chunk' else 'reasoning'
            part = self._segment.get(mid)
            if part is not None and part['type'] != kind_of:
                self.close_text(mid, emit)
                part = None
            if part is None:
                ordinal = self._ordinal.get((mid, kind_of), 0)
                self._ordinal[(mid, kind_of)] = ordinal + 1
                part = {'type': kind_of, 'text': '', 'ordinal': ordinal}
                message['content'].append(part)
                self._segment[mid] = part
            part['text'] += delta
            if emit:
                event = 'session.text.delta' if kind_of == 'text' else 'session.reasoning.delta'
                emit(event, assistantMessageID=mid, ordinal=part['ordinal'], delta=delta)
            return
        tid = str(update.get('toolCallId', ''))
        if not tid:
            return
        self.close_text(mid, emit)
        tool = self._tools.get(tid)
        if tool is None:
            tool = {'type': 'tool', 'id': tid, 'name': _tool_name(update), 'state': {'input': {}, 'status': 'pending'}}
            self._tools[tid] = tool
            message['content'].append(tool)
            if emit:
                emit('session.tool.input.started', assistantMessageID=mid, id=tid, name=tool['name'])
        elif (update.get('_meta') or {}).get('x.ai/tool'):
            tool['name'] = _tool_name(update)
        data = _tool_input(update)
        if data and (data.get('command') or data != tool['state']['input']):
            tool['state']['input'] = {**tool['state']['input'], **data} if tool['state']['input'] else data
            if emit:
                emit('session.tool.called', assistantMessageID=mid, id=tid, name=tool['name'], input=tool['state']['input'])
        status = update.get('status')
        output = _blocks_text(update.get('content'))
        diff = _diff(update.get('content'))
        if diff:
            tool['state']['diff'] = diff
        if status == 'completed':
            tool['state'].update(status='completed', content=text_content(output))
            if emit:
                emit('session.tool.success', assistantMessageID=mid, id=tid, content=text_content(output),
                     **({'diff': tool['state']['diff']} if tool['state'].get('diff') else {}))
        elif status == 'failed':
            tool['state'].update(status='error', error={'message': output or '失敗'})
            if emit:
                emit('session.tool.failed', assistantMessageID=mid, id=tid, error={'message': output or '失敗'})
        elif status == 'in_progress':
            tool['state']['status'] = 'running'

    def end(self, mid: str, outcome: str, error: str = '') -> None:
        if outcome in ('failed', 'interrupted'):
            message = {'type': 'idle', 'id': f'end:{mid}', 'outcome': outcome, 'error': error}
            self.messages.append(message)

    def visible(self) -> list[dict]:
        def clean(part: dict) -> dict:
            return {k: v for k, v in part.items() if k != 'ordinal'}
        return [{**{k: v for k, v in m.items() if not k.startswith('_')},
                 **({'content': [clean(p) for p in m['content']]} if 'content' in m else {})} for m in self.messages]


class GrokClient(Adapter):
    client = CLIENT
    name = 'Grok'

    def __init__(self, find=find_executable):
        super().__init__()
        self._find = find
        self._lock = threading.Lock()
        self._process: ChildProcess | None = None
        self._ready = threading.Event()
        self._next = 0
        self._calls: dict[int, dict] = {}
        self._loaded: dict[str, threading.Event] = {}   # session -> finished loading in this process
        self._records: dict[str, Record] = {}
        self._loading: set[str] = set()                 # replay in progress: updates build history only
        self._running: dict[str, int] = {}              # session -> prompts sent and not finished (Grok queues them)
        self._requests: dict[str, dict] = {}            # our id -> {'id', 'method', 'params', 'session'}
        self._models: list[dict] = []
        self._default = ''
        self._closed = False

    # -- process and JSON-RPC ---------------------------------------------------
    def _ensure(self) -> ChildProcess:
        while True:
            with self._lock:
                if self._closed:
                    raise ClientError('AgentDock 正在結束。')
                current = self._process
                starting = current is None or not current.alive()
                if starting:
                    exe = self._find()
                    if not exe:
                        raise ClientError('找不到 Grok（grok）。請先安裝並登入一次。')
                    command = list(exe) if isinstance(exe, (list, tuple)) else [exe]
                    self._loaded.clear()
                    self._records.clear()
                    self._ready.clear()
                    holder: dict = {}
                    current = ChildProcess([*command, 'agent', 'stdio'], os.path.expanduser('~'), child_env(),
                                           self._line, lambda code, err: self._exit(holder.get('process'), code, err),
                                           'Grok')
                    holder['process'] = self._process = current
            if not starting:
                if not self._ready.wait(TIMEOUT):
                    raise ClientError('Grok 沒有回應（initialize）。')
                if self._process is current:
                    return current
                continue
            try:
                self._call(current, 'initialize', {
                    'protocolVersion': 1, 'clientInfo': {'name': 'agentdock', 'title': 'AgentDock', 'version': '1'},
                    # No fs / terminal: Grok uses its own tools on this computer.
                    'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}})
            except ClientError:
                current.stop()
                raise
            self._ready.set()
            return current

    def _send_request(self, process: ChildProcess, method: str, params: dict) -> dict:
        with self._lock:
            self._next += 1
            call_id = self._next
            waiter: dict = {'done': threading.Event(), 'method': method}
            self._calls[call_id] = waiter
        try:
            process.send({'jsonrpc': '2.0', 'id': call_id, 'method': method, 'params': params})
        except ClientError:
            with self._lock:
                self._calls.pop(call_id, None)
            raise
        waiter['id'] = call_id
        return waiter

    def _wait(self, waiter: dict, timeout: float) -> dict:
        try:
            if not waiter['done'].wait(timeout):
                raise ClientError(f"Grok 沒有回應（{waiter['method']}）。")
        finally:
            with self._lock:
                self._calls.pop(waiter['id'], None)
        if 'error' in waiter:
            raise ClientError(f"Grok：{_error_text(waiter['error'])}")
        return waiter.get('result') or {}

    def _call(self, process: ChildProcess, method: str, params: dict, timeout: float = TIMEOUT) -> dict:
        return self._wait(self._send_request(process, method, params), timeout)

    def _line(self, message: dict) -> None:
        method = message.get('method')
        if method is None:
            with self._lock:
                waiter = self._calls.get(message.get('id'))
            if waiter is not None:
                if 'error' in message:
                    waiter['error'] = message['error']
                else:
                    waiter['result'] = message.get('result')
                waiter['done'].set()
                callback = waiter.get('callback')
                if callback:
                    callback(waiter)
            return
        if 'id' in message:
            self._agent_request(message)
        elif method == 'session/update':
            params = message.get('params') or {}
            self._update(str(params.get('sessionId', '')), params.get('update') or {}, params.get('_meta') or {})

    def _exit(self, process: ChildProcess | None, code: int | None, stderr: str) -> None:
        with self._lock:
            if process is not None and process is not self._process:
                return
            self._process = None
            calls = list(self._calls.values())
            running = dict(self._running)
            requests = dict(self._requests)
            self._running.clear()
            self._requests.clear()
            self._loaded.clear()
            closed = self._closed
        self._ready.set()
        for waiter in calls:
            waiter['error'] = {'message': stderr or f'Grok 已結束（{code}）'}
            waiter['done'].set()
        for rid, request in requests.items():
            self._resolved(rid, request)
        if closed:
            return
        for sid in running:
            self.event(sid, 'session.execution.failed', f'end:exit:{uuid.uuid4().hex}',
                       error={'message': 'Grok 意外結束' + (f'：{stderr}' if stderr else '')})

    def _reply(self, request_id: Any, result: dict | None = None, error: dict | None = None) -> None:
        process = self._process
        if process is None:
            raise ClientError('Grok 已結束。')
        body: dict[str, Any] = {'jsonrpc': '2.0', 'id': request_id}
        body['error' if error is not None else 'result'] = error if error is not None else result
        process.send(body)

    # -- updates and agent requests -----------------------------------------------
    def _update(self, sid: str, update: dict, meta: dict) -> None:
        if not sid:
            return
        with self._lock:
            record = self._records.setdefault(sid, Record())
            live = sid not in self._loading
        record.apply(update, meta, (lambda kind, **data: self.event(sid, kind, **data)) if live else None)

    def _agent_request(self, message: dict) -> None:
        method, params = message['method'], message.get('params') or {}
        sid = str(params.get('sessionId', ''))
        if method not in ('session/request_permission', ASK) or not sid:
            try:
                self._reply(message['id'], error={'code': -32601, 'message': f'AgentDock 不支援 {method}'})
            except ClientError:
                pass
            return
        rid = f"g{message['id']}"
        request = {'id': message['id'], 'method': method, 'params': params, 'session': sid}
        with self._lock:
            self._requests[rid] = request
        if method == ASK:
            fields = []
            for q in params.get('questions') or []:
                if not isinstance(q, dict) or not q.get('question'):
                    continue
                fields.append({'key': str(q['question']), 'title': str(q.get('header') or ''),
                               'description': str(q['question']),
                               'type': 'array' if q.get('multiSelect') or q.get('multi_select') else 'string',
                               'custom': True,
                               'options': [{'value': str(o.get('label', '')), 'label': str(o.get('label', '')),
                                            'description': str(o.get('description') or '')}
                                           for o in q.get('options') or [] if isinstance(o, dict)]})
            self.event(sid, 'form.created', form={'id': rid, 'sessionID': sid, 'fields': fields})
            return
        call = params.get('toolCall') or {}
        tool = ((call.get('_meta') or {}).get('x.ai/tool') or {})
        data = _tool_input(call)
        detail = next((str(data[k]) for k in ('command', 'path', 'filePath', 'url', 'query') if data.get(k)), '')
        title = str(call.get('title') or '')
        resources = [r for r in (detail, '' if detail and detail in title else title) if r]
        self.event(sid, 'permission.asked', id=rid, action=str(tool.get('label') or tool.get('name') or call.get('kind') or 'tool'),
                   resources=[r[:300] for r in resources][:5])

    def _resolved(self, rid: str, request: dict) -> None:
        if request['method'] == ASK:
            self.event(request['session'], 'form.cancelled', id=rid)
        else:
            self.event(request['session'], 'permission.replied', requestID=rid)

    def _take(self, rid: str) -> dict:
        with self._lock:
            request = self._requests.pop(rid, None)
        if request is None:
            raise ClientError('這個請求已失效（Grok 已不在等待）。')
        return request

    def _drop_requests(self, sid: str, answer: bool) -> None:
        """Pending requests of a session end with its turn; ACP asks for an explicit cancel."""
        with self._lock:
            stale = {rid: r for rid, r in self._requests.items() if r['session'] == sid}
            for rid in stale:
                del self._requests[rid]
        for rid, request in stale.items():
            if answer:
                try:
                    result = {'outcome': 'cancelled'} if request['method'] == ASK else {'outcome': {'outcome': 'cancelled'}}
                    self._reply(request['id'], result)
                except ClientError:
                    pass
            self._resolved(rid, request)

    # -- the adapter interface ------------------------------------------------------
    def options(self, row: dict | None, directory: str) -> dict:
        result = self._call(self._ensure(), '_x.ai/models/list', {})
        listing = result.get('result') if isinstance(result.get('result'), dict) else result
        models = []
        defaults: dict = {'effort': {}}
        for m in listing.get('availableModels') or []:
            efforts = ((m.get('_meta') or {}).get('reasoningEfforts') or []) if isinstance(m, dict) else []
            chosen = next((e.get('value') for e in efforts if isinstance(e, dict)
                           and (e.get('default') or e.get('isDefault') or e.get('current'))), None)
            if chosen:
                defaults['effort'][str(m.get('modelId'))] = str(chosen)
            models.append({'id': str(m.get('modelId')), 'label': str(m.get('name') or m.get('modelId')),
                           'efforts': [str(e.get('value')) for e in efforts if isinstance(e, dict) and e.get('value')],
                           'context': int(((m.get('_meta') or {}).get('totalContextTokens')) or 0)})
        self._models, self._default = models, str(listing.get('currentModelId') or '')
        if self._default:
            defaults['model'] = self._default
        options = {'models': models, 'modes': MODES, 'mode_fixed': True, 'mode_default': 'ask', 'defaults': defaults}
        self.remember(options)
        return options

    def _set_options(self, process: ChildProcess, sid: str, settings: dict) -> None:
        for config, name in (('model', 'model'), ('reasoning_effort', 'effort')):
            if settings.get(name):
                self._call(process, 'session/set_config_option',
                           {'sessionId': sid, 'configId': config, 'value': settings[name]})
        if settings.get('model'):
            with self._lock:
                self._records.setdefault(sid, Record()).model = settings['model']

    def new_session(self, directory: str, settings: dict | None = None) -> str:
        if not os.path.isdir(directory):
            raise ClientError(f'找不到資料夾：{directory}')
        settings = settings or {}
        params: dict[str, Any] = {'cwd': directory, 'mcpServers': []}
        if settings.get('mode') == 'auto':
            params['_meta'] = {'autoMode': True}
        elif settings.get('mode') == 'yolo':
            params['_meta'] = {'yoloMode': True}   # the user chose it for this conversation
        process = self._ensure()
        result = self._call(process, 'session/new', params)
        sid = str(result.get('sessionId') or '')
        if not sid:
            raise ClientError('Grok 沒有建立對話。')
        self._set_options(process, sid, settings)
        done = threading.Event()
        done.set()
        with self._lock:
            self._loaded[sid] = done
            self._records.setdefault(sid, Record())
        return sid

    def _load(self, row: dict) -> Record:
        sid = row['session_id']
        process = self._ensure()
        with self._lock:
            event = self._loaded.get(sid)
            mine = event is None
            if mine:
                event = self._loaded[sid] = threading.Event()
                self._records[sid] = Record()
                self._loading.add(sid)
        if not mine:
            if not event.wait(LOAD_TIMEOUT):
                raise ClientError('Grok 沒有回應（session/load）。')
            with self._lock:
                record = self._records.get(sid)
            if record is None:
                raise ClientError('Grok 無法開啟這個對話。')
            return record
        directory = row.get('directory') or os.path.expanduser('~')
        try:
            self._call(process, 'session/load', {'sessionId': sid, 'cwd': directory, 'mcpServers': []}, LOAD_TIMEOUT)
        except ClientError:
            with self._lock:
                self._loaded.pop(sid, None)
                self._records.pop(sid, None)
            raise
        finally:
            with self._lock:
                self._loading.discard(sid)
            event.set()
        with self._lock:
            record = self._records[sid]
            record.current = ''
            record.model = record.model or row.get('model', '')
        return record

    def configure(self, row: dict, changes: dict) -> None:
        self._load(row)
        self._set_options(self._ensure(), row['session_id'], changes)

    def compact(self, row: dict) -> None:
        self.prompt(row, '/compact')

    def fork(self, row: dict, message_id: str) -> str:
        record = self._load(row)
        directory = row.get('directory') or os.path.expanduser('~')
        result = self._call(self._ensure(), '_x.ai/session/fork', {
            'sourceSessionId': row['session_id'], 'sourceCwd': directory, 'newCwd': directory,
            'targetPromptIndex': record.prompt_index(message_id)})
        new = str(result.get('newSessionId') or '')
        if not new:
            raise ClientError('Grok 沒有建立分叉。')
        return new

    def rewind(self, row: dict, message_id: str) -> bool:
        """Grok's own /rewind, conversation only (files stay as they are)."""
        sid = row['session_id']
        if self.running(sid):
            raise ClientError('這個對話還在執行。')
        record = self._load(row)
        prompts = [m['id'] for m in record.messages if m['type'] == 'user']
        if message_id not in prompts:
            raise ClientError('找不到要編輯的那則訊息。')
        self._call(self._ensure(), REWIND, {'sessionId': sid, 'targetPromptIndex': prompts.index(message_id),
                                            'mode': 'conversation_only'})
        with self._lock:
            record.cut(message_id)
        return True

    def sessions(self) -> list[dict]:
        out = []
        for s in self._call(self._ensure(), 'session/list', {}).get('sessions') or []:
            try:
                updated = datetime.fromisoformat(str(s.get('updatedAt')).replace('Z', '+00:00')).timestamp()
            except ValueError:
                updated = 0.0
            out.append({'id': str(s.get('sessionId')), 'title': str(s.get('title') or s.get('sessionId'))[:80],
                        'directory': str(s.get('cwd') or ''), 'updated': updated})
        return out

    def prompt(self, row: dict, text: str, files: list[str] | None = None) -> None:
        sid = row['session_id']
        files = files or []
        blocks: list[dict] = [{'type': 'text', 'text': text}]
        for f in files:
            if image_type(f):
                kind, data = read_image(f)
                blocks.append({'type': 'image', 'mimeType': kind, 'data': data})
            else:
                blocks.append({'type': 'resource_link', 'uri': Path(f).resolve().as_uri(), 'name': Path(f).name})
        record = self._load(row)
        process = self._ensure()
        mid = uuid.uuid4().hex
        record.user(mid, text, [Path(f).name for f in files])
        with self._lock:
            busy = self._running.get(sid, 0) > 0
            self._running[sid] = self._running.get(sid, 0) + 1
        if not busy:
            record.current = ''
        self.event(sid, 'session.inbox.enqueued', inboxID=mid,
                   item={'type': 'user', 'payload': {'text': text, 'files': [Path(f).name for f in files]}})
        if not busy:
            self.event(sid, 'session.execution.started')
        try:
            waiter = self._send_request(process, 'session/prompt', {'sessionId': sid, 'prompt': blocks})
        except ClientError:
            with self._lock:
                self._running[sid] = max(0, self._running.get(sid, 1) - 1)
                if not self._running[sid]:
                    del self._running[sid]
            raise
        waiter['callback'] = lambda w: self._finished(sid, record, w)
        if waiter['done'].is_set():   # answered before the callback was attached
            self._finished(sid, record, waiter)

    def _finished(self, sid: str, record: Record, waiter: dict) -> None:
        left = 0
        with self._lock:
            if waiter.get('finished'):
                return
            waiter['finished'] = True
            self._calls.pop(waiter.get('id'), None)
            left = max(0, self._running.get(sid, 1) - 1)
            if left:
                self._running[sid] = left
            else:
                self._running.pop(sid, None)
        mid = record.current or uuid.uuid4().hex
        record.close_text(mid, lambda kind, **data: self.event(sid, kind, **data))
        if record.tokens:
            model = record.model or self._default
            limit = next((m.get('context', 0) for m in self._models if m.get('id') == model), 0)
            self.event(sid, 'session.context', used=record.tokens, limit=limit)
        self._drop_requests(sid, answer=False)
        record.current = ''
        if 'error' in waiter:
            error = _error_text(waiter['error'])
            record.end(mid, 'failed', error)
            self.event(sid, 'session.execution.failed', f'end:{mid}', error={'message': error})
            if left:
                self.event(sid, 'session.execution.started')
            return
        reason = str((waiter.get('result') or {}).get('stopReason') or 'end_turn')
        if reason == 'cancelled':
            record.end(mid, 'interrupted')
            self.event(sid, 'session.execution.interrupted', f'end:{mid}')
        elif reason == 'refusal':
            record.end(mid, 'failed', 'Grok 拒絕回答。')
            self.event(sid, 'session.execution.failed', f'end:{mid}', error={'message': 'Grok 拒絕回答。'})
        else:
            self.event(sid, 'session.execution.succeeded', f'end:{mid}')
        if left:
            self.event(sid, 'session.execution.started')   # Grok runs the next queued prompt

    def running(self, sid: str) -> bool:
        with self._lock:
            return sid in self._running

    def interrupt(self, row: dict) -> None:
        sid = row['session_id']
        if not self.running(sid) or self._process is None:
            self.event(sid, 'session.execution.interrupted', f'end:{uuid.uuid4().hex}')
            return
        self._drop_requests(sid, answer=True)
        self._process.send({'jsonrpc': '2.0', 'method': 'session/cancel', 'params': {'sessionId': sid}})

    def reply_permission(self, row: dict, request_id: str, decision: str) -> None:
        request = self._take(request_id)
        options = [o for o in request['params'].get('options') or [] if isinstance(o, dict)]
        option = next((o for kind in DECISIONS.get(decision, ('reject_once',)) for o in options
                       if o.get('kind') == kind), None)
        if option is None:
            result = {'outcome': {'outcome': 'cancelled'}}
        else:
            result = {'outcome': {'outcome': 'selected', 'optionId': option.get('optionId')}}
        self._reply(request['id'], result)
        self.event(request['session'], 'permission.replied', requestID=request_id)

    def reply_form(self, row: dict, form_id: str, answer: dict | None) -> None:
        request = self._take(form_id)
        if answer is None:
            result: dict = {'outcome': 'cancelled'}
        else:
            result = {'outcome': 'accepted',
                      'answers': {k: [str(x) for x in v] if isinstance(v, list) else str(v) for k, v in answer.items()}}
        self._reply(request['id'], result)
        self.event(request['session'], 'form.cancelled' if answer is None else 'form.replied', id=form_id)

    def history(self, row: dict, cursor: str | None) -> tuple[list[dict], str | None]:
        return page(self._load(row).visible(), cursor)

    def delete(self, row: dict) -> None:
        sid = row['session_id']
        if self.running(sid):
            raise ClientError('這個對話還在執行。')
        self._call(self._ensure(), '_x.ai/session/delete', {'sessionId': sid})
        with self._lock:
            self._loaded.pop(sid, None)
            self._records.pop(sid, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
        if process is not None:
            process.stop()


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = str(error.get('message') or '')
        data = error.get('data')
        if isinstance(data, str) and data and data not in message:
            message = f'{message}：{data}' if message else data
        elif isinstance(data, dict) and data.get('message'):
            message = str(data['message'])
        return message or '執行失敗'
    return str(error or '執行失敗')
