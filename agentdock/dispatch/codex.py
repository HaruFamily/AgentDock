"""Codex through `codex app-server` (JSON-RPC over stdio, one JSON object per line).

One app-server process serves every Codex conversation while AgentDock runs. Threads are Codex's
own sessions on disk, so a conversation continues after a restart with `thread/resume`.
Approvals arrive as server requests and wait for a click; the user's Codex approval policy and
sandbox settings decide what needs asking.
"""
from __future__ import annotations

import os
import logging
import shutil
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from agentdock.dispatch.adapter import (MAX_OUTPUT, Adapter, ChildProcess, ClientError, ReplyTransportError, child_env, first_file,
                                        image_type, npm_dir, page, text_content, with_file_list)

CLIENT = 'codex'
TIMEOUT = 60
OUTCOMES = {'completed': 'succeeded', 'failed': 'failed', 'interrupted': 'interrupted'}
COMMAND_DECISIONS = {'once': 'accept', 'always': 'acceptForSession', 'reject': 'decline'}
LEGACY_DECISIONS = {'once': 'approved', 'always': 'approved_for_session', 'reject': 'denied'}
APPROVALS = ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
             'item/permissions/requestApproval', 'execCommandApproval', 'applyPatchApproval')
USER_INPUT = 'item/tool/requestUserInput'
# Codex's own presets (/approvals): what may run without asking, and where.
MODES = [{'id': 'read-only', 'label': '唯讀'}, {'id': 'auto', 'label': '自動'}, {'id': 'full', 'label': '完全存取'}]
MODE_POLICY = {'read-only': ('on-request', {'type': 'readOnly'}),
               'auto': ('on-request', {'type': 'workspaceWrite'}),
               'full': ('never', {'type': 'dangerFullAccess'})}


def _is_codex_binary(path: Path) -> bool:
    name = path.name.lower()
    return name == 'codex.exe' or (name.startswith('codex-') and name.endswith('.exe')
                                   and ('x86_64' in name or 'aarch64' in name))


def find_executable() -> str | None:
    """codex.exe itself: the npm shim starts it through node, which is one more process and a window."""
    candidates: list[Path | str | None] = []
    found = shutil.which('codex')
    folders = npm_dir()
    if found and Path(found).suffix.lower() == '.exe':
        candidates.append(found)   # a standalone install on PATH
    elif found and Path(found).suffix.lower() in ('.cmd', '.ps1', ''):
        folders.insert(0, Path(found).parent)
    newest_first = lambda paths: sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders:
        package = folder / 'node_modules' / '@openai'
        if package.is_dir():
            candidates.extend(newest_first(p for p in package.glob('codex*/**/*.exe') if _is_codex_binary(p)))
    local = os.environ.get('LOCALAPPDATA')
    if local:
        bins = Path(local) / 'OpenAI' / 'Codex' / 'bin'
        if bins.is_dir():
            candidates.extend(newest_first(bins.glob('*/codex.exe')))
    if found:
        candidates.append(found)
    return first_file(candidates)


def _text(content: Any) -> str:
    if isinstance(content, list):
        return '\n'.join(str(c.get('text', '')) for c in content if isinstance(c, dict) and c.get('type') == 'text')
    return str(content or '')


def _files(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    return [Path(str(c.get('path') or c.get('url') or '圖片')).name for c in content
            if isinstance(c, dict) and c.get('type') in ('localImage', 'image')]


def _reasoning(item: dict) -> str:
    parts = item.get('summary') or item.get('content') or []
    return '\n\n'.join(str(p.get('text', p) if isinstance(p, dict) else p) for p in parts if p)


def command_text(source: dict) -> str:
    """The command as the agent wrote it, without the shell wrapper Codex adds (bash -lc / powershell)."""
    actions = [a.get('command') for a in source.get('commandActions') or [] if isinstance(a, dict)]
    actions = [str(a) for a in actions if a]
    if actions:
        return ' && '.join(actions)
    command = source.get('command') or ''
    return ' '.join(map(str, command)) if isinstance(command, list) else str(command)


def tool_of(item: dict) -> dict | None:
    """A Codex thread item as a tool part (name, input, status, output, error), or None if it is not a tool."""
    kind = item.get('type')
    status = str(item.get('status') or 'completed')
    error = ''
    if kind == 'commandExecution':
        name, data = 'shell', {'command': command_text(item)}
        output = str(item.get('aggregatedOutput') or '')
        if status == 'failed':
            error = output or f"結束代碼 {item.get('exitCode')}"
    elif kind == 'fileChange':
        changes = [c for c in item.get('changes') or [] if isinstance(c, dict)]
        name, data = 'edit', {'path': ', '.join(str(c.get('path', '')) for c in changes)}
        output = ''
        diff = '\n'.join(f"--- {c.get('path', '')}\n+++ {c.get('path', '')}\n{c.get('diff', '')}".rstrip()
                         for c in changes)
    elif kind == 'mcpToolCall':
        name = f"{item.get('server', '')}.{item.get('tool', '')}"
        data = item.get('arguments') if isinstance(item.get('arguments'), dict) else {}
        result = item.get('result') if isinstance(item.get('result'), dict) else {}
        output = _text(result.get('content'))
        if item.get('error'):
            error = str((item['error'] or {}).get('message') if isinstance(item['error'], dict) else item['error'])
    elif kind == 'dynamicToolCall':
        name = str(item.get('tool', ''))
        data = item.get('arguments') if isinstance(item.get('arguments'), dict) else {}
        output = _text(item.get('contentItems'))
        if item.get('success') is False:
            error = output or '失敗'
    elif kind == 'webSearch':
        name, data, output = 'websearch', {'query': str(item.get('query') or '')}, ''
    elif kind == 'imageView':
        name, data, output = 'view_image', {'path': str(item.get('path') or '')}, ''
    else:
        return None
    if status == 'declined':
        error = '已拒絕'
    state = 'running' if status == 'inProgress' else 'error' if (error or status in ('failed', 'declined')) else 'completed'
    tool = {'name': name, 'input': data, 'status': state, 'output': output[:MAX_OUTPUT], 'error': error}
    if kind == 'fileChange' and diff.strip():
        tool['diff'] = diff[:MAX_OUTPUT * 5]
    return tool


def messages_of(turns: list[dict]) -> list[dict]:
    """Codex turns as OpenCode-shaped messages, oldest first."""
    out: list[dict] = []
    for turn in turns:
        for item in turn.get('items') or []:
            if not isinstance(item, dict):
                continue
            kind, iid = item.get('type'), str(item.get('id', ''))
            if kind == 'userMessage':
                text, files = _text(item.get('content')), _files(item.get('content'))
                if text or files:
                    out.append({'type': 'user', 'id': iid, 'text': text, **({'files': files} if files else {})})
            elif kind == 'reasoning':
                text = _reasoning(item)
                if text:
                    out.append({'type': 'assistant', 'id': iid, 'content': [{'type': 'reasoning', 'text': text}]})
            elif kind == 'agentMessage':
                out.append({'type': 'assistant', 'id': iid, 'content': [{'type': 'text', 'text': str(item.get('text', ''))}]})
            else:
                tool = tool_of(item)
                if tool:
                    state = {'input': tool['input'], 'status': tool['status'], 'content': text_content(tool['output'])}
                    if tool.get('diff'):
                        state['diff'] = tool['diff']
                    if tool['error']:
                        state['error'] = {'message': tool['error']}
                    out.append({'type': 'assistant', 'id': iid,
                                'content': [{'type': 'tool', 'id': iid, 'name': tool['name'], 'state': state}]})
        status = turn.get('status')
        if status in ('failed', 'interrupted'):
            out.append({'type': 'idle', 'id': f"end:{turn.get('id', '')}", 'outcome': status,
                        'error': turn.get('error') or ''})
    return out


class CodexClient(Adapter):
    client = CLIENT
    name = 'Codex'

    def __init__(self, find=find_executable):
        super().__init__()
        self._find = find
        self._lock = threading.Lock()
        self._process: ChildProcess | None = None
        self._next = 0
        self._calls: dict[int, dict] = {}
        self._loaded: set[str] = set()
        self._turns: dict[str, str] = {}               # thread -> running turn
        self._requests: dict[str, dict] = {}           # our request id -> {'id', 'method', 'params', 'thread'}
        self._items: dict[str, dict] = {}              # item id -> item, for approval details
        self._errors: dict[str, str] = {}              # thread -> last error of the running turn
        self._finished: dict[str, deque[str]] = {}     # ignore late completions after terminal errors
        self._reply_lock = threading.Lock()
        self._ready = threading.Event()                # the current process answered initialize
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
                        raise ClientError('找不到 Codex（codex）。請先安裝並登入一次。')
                    self._loaded.clear()
                    self._ready.clear()
                    holder: dict = {}
                    command = list(exe) if isinstance(exe, (list, tuple)) else [exe]
                    # The default optional-MCP grace is only 1s. A turn sent immediately
                    # after start/resume/revert can otherwise omit still-starting tools.
                    # Zero waits for each server's own bounded startup timeout.
                    current = ChildProcess([*command, '-c', 'mcp_optional_startup_grace_ms=0', 'app-server'],
                                           os.path.expanduser('~'), child_env(), self._line,
                                           lambda code, err: self._exit(holder.get('process'), code, err), 'Codex')
                    holder['process'] = self._process = current
            if not starting:
                # Another worker is starting it: wait for its initialize.
                if not self._ready.wait(TIMEOUT):
                    raise ClientError('Codex 沒有回應（initialize）。')
                if self._process is current:
                    return current
                continue
            try:
                initialized = self._call(current, 'initialize', {'clientInfo': {'name': 'agentdock', 'title': 'AgentDock',
                                                                  'version': '1'}})
                logging.info('Codex connected: executable=%s userAgent=%s codexHome=%s',
                             command[0], initialized.get('userAgent'), initialized.get('codexHome'))
                current.send({'method': 'initialized'})
            except ClientError:
                current.stop()
                raise
            self._ready.set()
            return current

    def _call(self, process: ChildProcess, method: str, params: dict, timeout: float = TIMEOUT) -> dict:
        with self._lock:
            self._next += 1
            call_id = self._next
            waiter = {'done': threading.Event()}
            self._calls[call_id] = waiter
        try:
            process.send({'id': call_id, 'method': method, 'params': params})
            if not waiter['done'].wait(timeout):
                raise ClientError(f'Codex 沒有回應（{method}）。')
        finally:
            with self._lock:
                self._calls.pop(call_id, None)
        if 'error' in waiter:
            error = waiter['error']
            message = error.get('message') if isinstance(error, dict) else str(error)
            raise ClientError(f'Codex：{message}')
        return waiter.get('result') or {}

    def call(self, method: str, params: dict) -> dict:
        return self._call(self._ensure(), method, params)

    def _line(self, message: dict) -> None:
        if 'method' not in message:
            with self._lock:
                waiter = self._calls.get(message.get('id'))
            if waiter is not None:
                if 'error' in message:
                    waiter['error'] = message['error']
                else:
                    waiter['result'] = message.get('result')
                waiter['done'].set()
            return
        if 'id' in message:
            self._server_request(message)
        else:
            self._notification(message.get('method', ''), message.get('params') or {})

    def _exit(self, process: ChildProcess | None, code: int | None, stderr: str) -> None:
        with self._lock:
            if process is not None and process is not self._process:
                return   # an older process; its state was already replaced
            self._process = None
        self._ready.set()   # nobody waits for a process that is gone
        with self._lock:
            calls = list(self._calls.values())
            turns = dict(self._turns)
            requests = dict(self._requests)
            self._turns.clear()
            self._requests.clear()
            self._loaded.clear()
            closed = self._closed
        for waiter in calls:
            waiter['error'] = {'message': stderr or f'Codex 已結束（{code}）'}
            waiter['done'].set()
        for rid, request in requests.items():
            self._resolved(request['thread'], rid, request)
        if closed:
            return
        for thread in turns:
            self.event(thread, 'session.execution.failed', f'end:exit:{uuid.uuid4().hex}',
                       error={'message': 'Codex 意外結束' + (f'：{stderr}' if stderr else '')})

    def _respond(self, request: dict, result: dict) -> None:
        process = self._process
        if process is None:
            raise ClientError('Codex 已結束。')
        process.send({'id': request['id'], 'result': result})

    # -- server requests --------------------------------------------------------
    def _server_request(self, message: dict) -> None:
        method, params = message['method'], message.get('params') or {}
        thread = str(params.get('threadId') or params.get('conversationId') or '')
        if method not in APPROVALS and method != USER_INPUT:
            reply: dict[str, Any] = {'id': message['id']}
            if method == 'mcpServer/elicitation/request':
                reply['result'] = {'action': 'decline'}
            else:
                reply['error'] = {'code': -32601, 'message': f'AgentDock 不支援 {method}'}
            if self._process:
                self._process.send(reply)
            return
        rid = f"r{message['id']}"
        request = {'id': message['id'], 'method': method, 'params': params, 'thread': thread}
        with self._lock:
            self._requests[rid] = request
        if method == USER_INPUT:
            fields = []
            for q in params.get('questions') or []:
                if not isinstance(q, dict) or not q.get('id'):
                    continue
                options = [{'value': str(o.get('label', '')), 'label': str(o.get('label', '')),
                            'description': str(o.get('description') or '')}
                           for o in q.get('options') or [] if isinstance(o, dict)]
                fields.append({'key': str(q['id']), 'title': str(q.get('header') or ''),
                               'description': str(q.get('question') or ''), 'type': 'string',
                               'custom': bool(q.get('isOther')) or not options, 'options': options})
            self.event(thread, 'form.created', form={'id': rid, 'sessionID': thread, 'fields': fields})
            return
        action, resources = self._describe(method, params)
        self.event(thread, 'permission.asked', id=rid, action=action, resources=resources)

    def _describe(self, method: str, params: dict) -> tuple[str, list[str]]:
        reason = str(params.get('reason') or '')
        item = self._items.get(str(params.get('itemId', ''))) or {}
        if method in ('item/commandExecution/requestApproval', 'execCommandApproval'):
            command = command_text(params) or command_text(item)
            resources = [command] if command else []
            action = 'shell'
        elif method in ('item/fileChange/requestApproval', 'applyPatchApproval'):
            changes = item.get('changes') or []
            paths = [str(c.get('path', '')) for c in changes if isinstance(c, dict)]
            if not paths and isinstance(params.get('fileChanges'), dict):
                paths = list(params['fileChanges'])
            if not paths and params.get('grantRoot'):
                paths = [str(params['grantRoot'])]
            resources, action = paths, 'edit'
        else:
            permissions = params.get('permissions') or {}
            resources = [str(permissions)[:300]] if permissions else []
            action = 'permissions'
        if reason:
            resources.append(reason)
        return action, [r[:300] for r in resources][:5]

    def _take(self, row: dict, rid: str) -> dict:
        with self._lock:
            request = self._requests.get(rid)
        if request is None:
            raise ClientError('這個請求已失效（Codex 已不在等待）。')
        return request

    def _send_reply(self, row: dict, rid: str, result: dict) -> dict:
        with self._reply_lock:
            request = self._take(row, rid)
            try:
                self._respond(request, result)
            except ClientError as e:
                raise ReplyTransportError(str(e)) from e
            with self._lock:
                self._requests.pop(rid, None)
            return request

    def reply_permission(self, row: dict, request_id: str, decision: str) -> None:
        request = self._take(row, request_id)
        method, params = request['method'], request['params']
        if method in ('execCommandApproval', 'applyPatchApproval'):
            result: dict = {'decision': LEGACY_DECISIONS.get(decision, 'denied')}
        elif method == 'item/permissions/requestApproval':
            granted = {} if decision == 'reject' else (params.get('permissions') or {})
            result = {'permissions': granted, 'scope': 'session' if decision == 'always' else 'turn'}
        else:
            result = {'decision': COMMAND_DECISIONS.get(decision, 'decline')}
        self._send_reply(row, request_id, result)
        self.event(request['thread'], 'permission.replied', requestID=request_id)

    def reply_form(self, row: dict, form_id: str, answer: dict | None) -> None:
        request = self._take(row, form_id)
        answers = {} if answer is None else {
            k: {'answers': [str(x) for x in v] if isinstance(v, list) else [str(v)]} for k, v in answer.items()}
        self._send_reply(row, form_id, {'answers': answers})
        self.event(request['thread'], 'form.cancelled' if answer is None else 'form.replied', id=form_id)

    def _resolved(self, thread: str, rid: str, request: dict) -> None:
        if request['method'] == USER_INPUT:
            self.event(thread, 'form.cancelled', id=rid)
        else:
            self.event(thread, 'permission.replied', requestID=rid)

    # -- notifications ----------------------------------------------------------
    def _notification(self, method: str, params: dict) -> None:
        thread = str(params.get('threadId') or '')
        if not thread:
            return
        if method == 'turn/started':
            with self._lock:
                self._turns[thread] = str((params.get('turn') or {}).get('id', ''))
                self._errors.pop(thread, None)
            self.event(thread, 'session.execution.started')
        elif method == 'item/started':
            self._item(thread, params.get('item') or {}, done=False)
        elif method == 'item/completed':
            self._item(thread, params.get('item') or {}, done=True)
        elif method == 'item/reasoning/summaryTextDelta':
            mid = str(params.get('itemId', ''))
            self.event(thread, 'session.reasoning.delta', assistantMessageID=mid,
                       ordinal=int(params.get('summaryIndex') or 0), delta=str(params.get('delta', '')))
        elif method == 'item/agentMessage/delta':
            self.event(thread, 'session.text.delta', assistantMessageID=str(params.get('itemId', '')), ordinal=0,
                       delta=str(params.get('delta', '')))
        elif method == 'thread/tokenUsage/updated':
            usage = params.get('tokenUsage') or {}
            last = usage.get('last') or {}
            if last.get('totalTokens'):
                self.event(thread, 'session.context', used=int(last['totalTokens']),
                           limit=int(usage.get('modelContextWindow') or 0))
        elif method == 'error':
            error = params.get('error') or {}
            logging.warning('Codex error: thread=%s turn=%s willRetry=%s code=%s', thread,
                            params.get('turnId'), params.get('willRetry'),
                            error.get('codexErrorInfo') if isinstance(error, dict) else None)
            if params.get('willRetry'):
                self.event(thread, 'session.execution.retrying')
                return
            with self._lock:
                turn_id = str(params.get('turnId') or self._turns.get(thread) or '')
                active = self._turns.get(thread)
                if active and turn_id and active != turn_id:
                    return
            self._notification('turn/completed', {'threadId': thread, 'turn': {
                'id': turn_id, 'status': 'failed', 'error': error}})
        elif method == 'serverRequest/resolved':
            rid = f"r{params.get('requestId')}"
            with self._lock:
                request = self._requests.pop(rid, None)
            if request is not None:
                self._resolved(thread, rid, request)
        elif method == 'turn/completed':
            turn = params.get('turn') or {}
            with self._lock:
                tid = str(turn.get('id') or '')
                if tid and (tid in self._finished.get(thread, ()) or
                            (self._turns.get(thread) and self._turns[thread] != tid)):
                    return
                self._finished.setdefault(thread, deque(maxlen=64)).append(tid)
                self._turns.pop(thread, None)
                last_error = self._errors.pop(thread, '')
                stale = [rid for rid, r in self._requests.items() if r['thread'] == thread]
                requests = {rid: self._requests.pop(rid) for rid in stale}
            for rid, request in requests.items():
                self._resolved(thread, rid, request)
            outcome = OUTCOMES.get(str(turn.get('status')), 'succeeded')
            data: dict[str, Any] = {}
            if outcome == 'failed':
                error = turn.get('error') or {}
                data['error'] = {'message': (error.get('message') if isinstance(error, dict) else str(error))
                                 or last_error or 'Codex 執行失敗'}
            self.event(thread, f'session.execution.{outcome}', f"end:{turn.get('id', '')}", **data)

    def _item(self, thread: str, item: dict, done: bool) -> None:
        kind, iid = item.get('type'), str(item.get('id', ''))
        if not iid:
            return
        if kind == 'userMessage':
            if not done:
                self.event(thread, 'session.inbox.enqueued', inboxID=iid, item={'type': 'user', 'payload': {
                    'text': _text(item.get('content')), 'files': _files(item.get('content'))}})
            return
        if kind == 'reasoning':
            if not done:
                self.event(thread, 'session.step.started', assistantMessageID=iid)
            else:
                for index, part in enumerate(item.get('summary') or []):
                    text = str(part.get('text', part) if isinstance(part, dict) else part)
                    self.event(thread, 'session.reasoning.ended', assistantMessageID=iid, ordinal=index, text=text)
            return
        if kind == 'agentMessage':
            if not done:
                self.event(thread, 'session.step.started', assistantMessageID=iid)
                self.event(thread, 'session.text.started', assistantMessageID=iid, ordinal=0)
            else:
                self.event(thread, 'session.text.ended', assistantMessageID=iid, ordinal=0,
                           text=str(item.get('text', '')))
            return
        tool = tool_of(item)
        if tool is None:
            return
        if not done:
            self._items[iid] = item
            self.event(thread, 'session.step.started', assistantMessageID=iid)
            self.event(thread, 'session.tool.input.started', assistantMessageID=iid, id=iid, name=tool['name'])
            self.event(thread, 'session.tool.called', assistantMessageID=iid, id=iid, name=tool['name'],
                       input=tool['input'])
            return
        self._items.pop(iid, None)
        if tool['status'] == 'error':
            self.event(thread, 'session.tool.failed', assistantMessageID=iid, id=iid, error={'message': tool['error']})
        else:
            self.event(thread, 'session.tool.success', assistantMessageID=iid, id=iid,
                       content=text_content(tool['output']), **({'diff': tool['diff']} if tool.get('diff') else {}))

    # -- the adapter interface ----------------------------------------------------
    def new_session(self, directory: str, settings: dict | None = None) -> str:
        if not os.path.isdir(directory):
            raise ClientError(f'找不到資料夾：{directory}')
        result = self.call('thread/start', {'cwd': directory, **self._thread_settings(settings or {})})
        thread = str((result.get('thread') or {}).get('id', ''))
        if not thread:
            raise ClientError('Codex 沒有建立對話。')
        with self._lock:
            self._loaded.add(thread)
        return thread

    def _load(self, row: dict) -> None:
        thread = row['session_id']
        self._ensure()
        with self._lock:
            loaded = thread in self._loaded
        if not loaded:
            self.call('thread/resume', {'threadId': thread, 'excludeTurns': True})
            with self._lock:
                self._loaded.add(thread)

    @staticmethod
    def _thread_settings(settings: dict) -> dict:
        params: dict[str, Any] = {}
        if settings.get('model'):
            params['model'] = settings['model']
        if settings.get('mode') in MODE_POLICY:
            approval, sandbox = MODE_POLICY[settings['mode']]
            params['approvalPolicy'] = approval
            params['sandbox'] = {'readOnly': 'read-only', 'workspaceWrite': 'workspace-write',
                                 'dangerFullAccess': 'danger-full-access'}[sandbox['type']]
        return params

    def prompt(self, row: dict, text: str, files: list[str] | None = None) -> None:
        self._load(row)
        thread = row['session_id']
        files = files or []
        images = [f for f in files if image_type(f)]
        message = [{'type': 'text', 'text': with_file_list(text, [f for f in files if f not in images]),
                    'text_elements': []}] + [{'type': 'localImage', 'path': f} for f in images]
        with self._lock:
            turn = self._turns.get(thread)
        if turn:
            # A message while Codex works joins the running turn, as typing in the Codex app does.
            try:
                self.call('turn/steer', {'threadId': thread, 'expectedTurnId': turn, 'input': message})
                return
            except ClientError:
                with self._lock:
                    if self._turns.get(thread) == turn:
                        raise
                # The turn ended in between: start a new one.
        params: dict[str, Any] = {'threadId': thread, 'input': message}
        if row.get('model'):
            params['model'] = row['model']
        if row.get('effort'):
            params['effort'] = row['effort']
        if row.get('mode') in MODE_POLICY:
            params['approvalPolicy'], params['sandboxPolicy'] = MODE_POLICY[row['mode']]
        logging.info('Codex turn/start: thread=%s model=%s effort=%s', thread,
                     params.get('model', 'configured default'), params.get('effort', 'configured default'))
        self.call('turn/start', params)

    def options(self, row: dict | None, directory: str) -> dict:
        models = []
        defaults: dict = {'effort': {}}
        for m in self.call('model/list', {}).get('data') or []:
            if isinstance(m, dict) and not m.get('hidden'):
                name = str(m.get('model') or m.get('id'))
                if m.get('isDefault'):
                    defaults['model'] = name
                if m.get('defaultReasoningEffort'):
                    defaults['effort'][name] = str(m['defaultReasoningEffort'])
                models.append({'id': str(m.get('model') or m.get('id')), 'label': str(m.get('displayName') or m.get('id')),
                               'efforts': [str(e.get('reasoningEffort')) for e in m.get('supportedReasoningEfforts') or []
                                           if isinstance(e, dict) and e.get('reasoningEffort')]})
        try:   # the user's config.toml beats the built-in default
            config = (self.call('config/read', {}) or {}).get('config') or {}
        except Exception:
            config = {}
        if config.get('model') and any(m['id'] == config['model'] for m in models):
            defaults['model'] = str(config['model'])
        if config.get('model_reasoning_effort'):
            defaults['effort'] = str(config['model_reasoning_effort'])
        options = {'models': models, 'modes': MODES, 'mode_default': 'auto', 'defaults': defaults}
        self.remember(options)
        return options

    def fork(self, row: dict, message_id: str) -> str:
        """thread/fork, then drop the turns after the one holding `message_id` (thread/revert)."""
        thread = row['session_id']
        turns = (self.call('thread/read', {'threadId': thread, 'includeTurns': True}).get('thread') or {}).get('turns') or []
        index = next((i for i, turn in enumerate(turns)
                      if any(isinstance(it, dict) and it.get('id') == message_id for it in turn.get('items') or [])
                      or f"end:{turn.get('id')}" == message_id), None)
        if index is None:
            raise ClientError('找不到要分叉的那則訊息。')
        new = str(((self.call('thread/fork', {'threadId': thread, 'excludeTurns': True}).get('thread') or {}).get('id')) or '')
        if not new:
            raise ClientError('Codex 沒有建立分叉。')
        with self._lock:
            self._loaded.add(new)
        if index + 1 < len(turns):
            self.call('thread/revert', {'threadId': new, 'beforeTurnId': turns[index + 1]['id']})
        return new

    def rewind(self, row: dict, message_id: str) -> bool:
        """thread/revert to just before the turn that the message started."""
        thread = row['session_id']
        if self.running(thread):
            raise ClientError('這個對話還在執行。')
        self._load(row)
        turns = (self.call('thread/read', {'threadId': thread, 'includeTurns': True}).get('thread') or {}).get('turns') or []
        turn = next((t for t in turns
                     if any(isinstance(it, dict) and it.get('id') == message_id for it in t.get('items') or [])), None)
        if turn is None:
            raise ClientError('找不到要編輯的那則訊息。')
        self.call('thread/revert', {'threadId': thread, 'beforeTurnId': turn['id']})
        return True

    def sessions(self) -> list[dict]:
        out = []
        cursor = None
        seen = set()
        while True:
            result = self.call('thread/list', {'limit': 100, **({'cursor': cursor} if cursor else {})})
            for t in result.get('data') or []:
                if isinstance(t, dict) and t.get('id') and not t.get('parentThreadId'):
                    out.append({'id': str(t['id']), 'title': str(t.get('name') or t.get('preview') or t['id'])[:80],
                                'directory': str(t.get('cwd') or ''), 'updated': float(t.get('updatedAt') or 0)})
            cursor = result.get('nextCursor')
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return out

    def compact(self, row: dict) -> None:
        self._load(row)
        self.call('thread/compact/start', {'threadId': row['session_id']})

    def running(self, sid: str) -> bool:
        with self._lock:
            return sid in self._turns

    def interrupt(self, row: dict) -> None:
        thread = row['session_id']
        with self._lock:
            turn = self._turns.get(thread)
        if not turn:
            self.event(thread, 'session.execution.interrupted', f'end:{uuid.uuid4().hex}')
            return
        self.call('turn/interrupt', {'threadId': thread, 'turnId': turn})

    def history(self, row: dict, cursor: str | None) -> tuple[list[dict], str | None]:
        result = self.call('thread/read', {'threadId': row['session_id'], 'includeTurns': True})
        return page(messages_of((result.get('thread') or {}).get('turns') or []), cursor)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self._process
            self._process = None
        if process is not None:
            process.stop()
