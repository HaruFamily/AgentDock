"""Claude Code through its SDK protocol: `claude --input-format stream-json --output-format stream-json`.

The same protocol the Claude Agent SDK uses, spoken directly (the SDK package would bundle a
second copy of the CLI). One process per conversation that is in use; it closes itself after
`IDLE_SECONDS` without work and the next message resumes the session with `--resume`.
Permission prompts arrive as `can_use_tool` control requests (`--permission-prompt-tool stdio`),
so the user's own Claude Code permission rules and mode still decide what needs asking.
History is read from Claude Code's transcript, `~/.claude/projects/<cwd>/<session>.jsonl`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from agentdock.dispatch.adapter import (MAX_OUTPUT, Adapter, ChildProcess, ClientError, child_env, first_file,
                                        npm_dir, page, text_content)

CLIENT = 'claude-code'
IDLE_SECONDS = 600
INTERRUPTED = '[Request interrupted by user'
SKIP_USER = re.compile(r'^\s*<(local-command-|command-name|command-message|command-args|session-start-hook|'
                       r'system-reminder|task-notification|bash-|user-prompt-submit-hook)')
DENIED = 'The user denied this in AgentDock.'
SKIPPED = 'The user skipped this question.'


def find_executable() -> str | None:
    """The real claude.exe, not the npm .cmd shim (a shim would flash a console window)."""
    candidates: list[Path | str | None] = []
    found = shutil.which('claude')
    if found:
        path = Path(found)
        if path.suffix.lower() in ('.cmd', '.ps1', ''):
            candidates.append(path.parent / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe')
        candidates.append(path)
    for folder in npm_dir():
        candidates.append(folder / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe')
    candidates.append(Path.home() / '.local' / 'bin' / 'claude.exe')
    candidates.append(Path.home() / '.local' / 'bin' / 'claude')
    return first_file(candidates)


def projects_dir() -> Path:
    base = os.environ.get('CLAUDE_CONFIG_DIR')
    return (Path(base) if base else Path.home() / '.claude') / 'projects'


def transcript_file(directory: str, session_id: str) -> Path | None:
    name = re.sub(r'[^a-zA-Z0-9]', '-', directory or '')
    exact = projects_dir() / name[:200] / f'{session_id}.jsonl'
    if exact.is_file():
        return exact
    # Long paths get a hash suffix, and a session may have moved: look in every project.
    for found in projects_dir().glob(f'*/{session_id}.jsonl'):
        return found
    return None


def summary(tool: str, data: dict) -> str:
    for name in ('command', 'file_path', 'notebook_path', 'path', 'pattern', 'url', 'query', 'description', 'plan'):
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return ' '.join(value.split())[:300]
    text = json.dumps(data, ensure_ascii=False)
    return text[:300] if data else tool


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content[:MAX_OUTPUT]
    if isinstance(content, list):
        return '\n'.join(str(c.get('text', '')) for c in content
                         if isinstance(c, dict) and c.get('type') == 'text')[:MAX_OUTPUT]
    return ''


def _user_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(str(c.get('text', '')) for c in content if isinstance(c, dict) and c.get('type') == 'text')
    return ''


def read_history(path: Path | None) -> list[dict]:
    """The visible conversation in `path`, as OpenCode-shaped messages, oldest first."""
    if path is None:
        return []
    entries: list[dict] = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get('uuid'):
                    entries.append(entry)
    except OSError as e:
        raise ClientError(f'無法讀取 Claude Code 對話紀錄：{e}') from e
    by_id = {e['uuid']: e for e in entries}
    leaf = next((e for e in reversed(entries) if e.get('type') in ('user', 'assistant')
                 and not e.get('isSidechain') and not e.get('teamName')), None)
    chain: list[dict] = []
    seen: set[str] = set()
    while leaf is not None and leaf['uuid'] not in seen:
        seen.add(leaf['uuid'])
        chain.append(leaf)
        leaf = by_id.get(leaf.get('parentUuid') or '')
    chain.reverse()

    messages: list[dict] = []
    tools: dict[str, dict] = {}
    for entry in chain:
        message = entry.get('message') or {}
        if entry.get('type') == 'assistant' and not entry.get('isSidechain'):
            mid = message.get('id') or entry['uuid']
            if not messages or messages[-1].get('type') != 'assistant' or messages[-1]['id'] != mid:
                messages.append({'type': 'assistant', 'id': mid, 'content': []})
            for block in message.get('content') or []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text':
                    messages[-1]['content'].append({'type': 'text', 'text': str(block.get('text', ''))})
                elif block.get('type') == 'tool_use':
                    part = {'type': 'tool', 'id': block.get('id', ''), 'name': str(block.get('name', '')),
                            'state': {'input': block.get('input') if isinstance(block.get('input'), dict) else {},
                                      'status': 'running'}}
                    tools[part['id']] = part
                    messages[-1]['content'].append(part)
        elif entry.get('type') == 'user' and not entry.get('isMeta') and not entry.get('isCompactSummary'):
            content = message.get('content')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id') in tools:
                        state = tools[block['tool_use_id']]['state']
                        output = _result_text(block.get('content'))
                        if block.get('is_error'):
                            state.update(status='error', error=output)
                        else:
                            state.update(status='completed', content=text_content(output))
            text = _user_text(content).strip()
            if not text or SKIP_USER.match(text):
                continue
            if text.startswith(INTERRUPTED):
                messages.append({'type': 'idle', 'id': entry['uuid'], 'outcome': 'interrupted'})
            else:
                messages.append({'type': 'user', 'id': entry['uuid'], 'text': text})
    return messages


class Session:
    """One running claude process for one conversation."""

    def __init__(self, owner: 'ClaudeClient', sid: str, directory: str, fresh: bool):
        self.owner, self.sid = owner, sid
        self.pending: dict[str, dict] = {}       # control request id -> can_use_tool request
        self.busy = False
        self.queued = 0                          # messages sent and not finished; Claude Code runs them in turn
        self.interrupting = False
        self._blocks: dict[int, dict] = {}
        self._mid = ''
        self._text_count: dict[str, int] = {}
        self._streamed: set[str] = set()
        self._tool_message: dict[str, str] = {}
        self._timer: threading.Timer | None = None
        self._next = 0
        args = [*owner.command(), '--output-format', 'stream-json', '--verbose', '--input-format', 'stream-json',
                '--include-partial-messages', '--permission-prompt-tool', 'stdio',
                '--session-id' if fresh else '--resume', sid]
        env = child_env({'CLAUDE_CODE_ENTRYPOINT': 'sdk-py'}, drop=('CLAUDECODE',))
        if not os.path.isdir(directory):
            raise ClientError(f'找不到資料夾：{directory}')
        self.process = ChildProcess(args, directory, env, self._line, self._exit, 'Claude Code')
        self._control({'subtype': 'initialize', 'hooks': None})

    # -- requests -------------------------------------------------------------
    def _control(self, request: dict) -> None:
        self._next += 1
        self.process.send({'type': 'control_request', 'request_id': f'req_{self._next}_{uuid.uuid4().hex[:8]}',
                           'request': request})

    def respond(self, request_id: str, response: dict) -> None:
        self.process.send({'type': 'control_response',
                           'response': {'subtype': 'success', 'request_id': request_id, 'response': response}})

    def prompt(self, text: str) -> None:
        self._cancel_timer()
        message_id = str(uuid.uuid4())
        self.queued += 1
        self.owner.event(self.sid, 'session.inbox.enqueued', inboxID=message_id,
                         item={'type': 'user', 'payload': {'text': text}})
        if not self.busy:
            self.busy, self.interrupting = True, False
            self.owner.event(self.sid, 'session.execution.started')
        self.process.send({'type': 'user', 'message': {'role': 'user', 'content': text},
                           'parent_tool_use_id': None, 'session_id': self.sid, 'uuid': message_id})

    def interrupt(self) -> None:
        self.interrupting = True
        self.queued = min(self.queued, 1)   # Claude Code drops queued messages on interrupt
        self._control({'subtype': 'interrupt'})

    def close(self) -> None:
        self._cancel_timer()
        self.process.stop()

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None

    # -- output ---------------------------------------------------------------
    def _line(self, message: dict) -> None:
        kind = message.get('type')
        if message.get('parent_tool_use_id'):
            return   # a subagent's own conversation
        if kind == 'system' and message.get('subtype') == 'init':
            self.owner.started(self.sid)
        elif kind == 'stream_event':
            self._stream(message.get('event') or {})
        elif kind == 'assistant':
            self._assistant(message.get('message') or {})
        elif kind == 'user':
            self._tool_results((message.get('message') or {}).get('content'))
        elif kind == 'result':
            self._result(message)
        elif kind == 'control_request':
            self._request(message.get('request_id', ''), message.get('request') or {})
        elif kind == 'control_cancel_request':
            rid = message.get('request_id', '')
            request = self.pending.pop(rid, None)
            if request is not None:
                kind = 'form.cancelled' if request.get('tool_name') == 'AskUserQuestion' else 'permission.replied'
                self.owner.event(self.sid, kind, id=rid, requestID=rid)

    def _stream(self, event: dict) -> None:
        kind = event.get('type')
        if kind == 'message_start':
            self._mid = (event.get('message') or {}).get('id') or uuid.uuid4().hex
            self._streamed.add(self._mid)
            self._blocks = {}
            self.owner.event(self.sid, 'session.step.started', assistantMessageID=self._mid)
            return
        mid, index = self._mid, event.get('index', 0)
        if not mid:
            return
        if kind == 'content_block_start':
            block = event.get('content_block') or {}
            if block.get('type') == 'text':
                ordinal = self._text_count.get(mid, 0)
                self._text_count[mid] = ordinal + 1
                self._blocks[index] = {'type': 'text', 'ordinal': ordinal, 'text': str(block.get('text', ''))}
                self.owner.event(self.sid, 'session.text.started', assistantMessageID=mid, ordinal=ordinal)
            elif block.get('type') == 'tool_use':
                tid, name = str(block.get('id', '')), str(block.get('name', ''))
                self._blocks[index] = {'type': 'tool', 'id': tid, 'name': name, 'json': ''}
                self._tool_message[tid] = mid
                self.owner.event(self.sid, 'session.tool.input.started', assistantMessageID=mid, id=tid, name=name)
        elif kind == 'content_block_delta':
            block, delta = self._blocks.get(index), event.get('delta') or {}
            if block is None:
                return
            if block['type'] == 'text' and delta.get('type') == 'text_delta':
                block['text'] += str(delta.get('text', ''))
                self.owner.event(self.sid, 'session.text.delta', assistantMessageID=mid, ordinal=block['ordinal'],
                                 delta=str(delta.get('text', '')))
            elif block['type'] == 'tool' and delta.get('type') == 'input_json_delta':
                block['json'] += str(delta.get('partial_json', ''))
        elif kind == 'content_block_stop':
            block = self._blocks.pop(index, None)
            if block is None:
                return
            if block['type'] == 'text':
                self.owner.event(self.sid, 'session.text.ended', assistantMessageID=mid, ordinal=block['ordinal'],
                                 text=block['text'])
            else:
                try:
                    data = json.loads(block['json']) if block['json'] else {}
                except ValueError:
                    data = {}
                self.owner.event(self.sid, 'session.tool.called', assistantMessageID=mid, id=block['id'],
                                 name=block['name'], input=data if isinstance(data, dict) else {})

    def _assistant(self, message: dict) -> None:
        """Only used when the stream had no partial events for this message."""
        mid = message.get('id') or ''
        if not mid or mid in self._streamed:
            return
        self.owner.event(self.sid, 'session.step.started', assistantMessageID=mid)
        for block in message.get('content') or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'text':
                ordinal = self._text_count.get(mid, 0)
                self._text_count[mid] = ordinal + 1
                self.owner.event(self.sid, 'session.text.ended', assistantMessageID=mid, ordinal=ordinal,
                                 text=str(block.get('text', '')))
            elif block.get('type') == 'tool_use':
                tid = str(block.get('id', ''))
                self._tool_message[tid] = mid
                self.owner.event(self.sid, 'session.tool.input.started', assistantMessageID=mid, id=tid,
                                 name=str(block.get('name', '')))
                self.owner.event(self.sid, 'session.tool.called', assistantMessageID=mid, id=tid,
                                 name=str(block.get('name', '')),
                                 input=block.get('input') if isinstance(block.get('input'), dict) else {})

    def _tool_results(self, content: Any) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get('type') != 'tool_result':
                continue
            tid = str(block.get('tool_use_id', ''))
            mid = self._tool_message.get(tid)
            if not mid:
                continue
            output = _result_text(block.get('content'))
            if block.get('is_error'):
                self.owner.event(self.sid, 'session.tool.failed', assistantMessageID=mid, id=tid,
                                 error={'message': output})
            else:
                self.owner.event(self.sid, 'session.tool.success', assistantMessageID=mid, id=tid,
                                 content=text_content(output))

    def _result(self, message: dict) -> None:
        end = f"end:{message.get('uuid') or uuid.uuid4().hex}"
        self._clear_pending()
        if self.interrupting or str(message.get('terminal_reason', '')).startswith('aborted'):
            self.owner.event(self.sid, 'session.execution.interrupted', end)
        elif message.get('subtype') == 'success' and not message.get('is_error'):
            self.owner.event(self.sid, 'session.execution.succeeded', end)
        else:
            errors = [str(e) for e in message.get('errors') or [] if not str(e).startswith('[ede_diagnostic]')]
            text = str(message.get('result') or '') if message.get('is_error') else ''
            self.owner.event(self.sid, 'session.execution.failed', end,
                             error={'message': text or '；'.join(errors) or 'Claude Code 執行失敗'})
        self.queued = max(0, self.queued - 1)
        self.interrupting = False
        if self.queued:
            self.owner.event(self.sid, 'session.execution.started')   # the next queued message runs now
            return
        self.busy = False
        self._timer = threading.Timer(IDLE_SECONDS, lambda: self.owner.retire(self))
        self._timer.daemon = True
        self._timer.start()

    def _clear_pending(self) -> None:
        for rid, request in list(self.pending.items()):
            kind = 'form.cancelled' if request.get('tool_name') == 'AskUserQuestion' else 'permission.replied'
            self.owner.event(self.sid, kind, id=rid, requestID=rid)
        self.pending.clear()

    def _request(self, rid: str, request: dict) -> None:
        if request.get('subtype') != 'can_use_tool':
            self.process.send({'type': 'control_response', 'response': {
                'subtype': 'error', 'request_id': rid, 'error': f"AgentDock 不支援 {request.get('subtype')}"}})
            return
        self.pending[rid] = request
        tool = str(request.get('tool_name', ''))
        data = request.get('input') if isinstance(request.get('input'), dict) else {}
        if tool == 'AskUserQuestion':
            fields = []
            for q in data.get('questions') or []:
                if not isinstance(q, dict) or not q.get('question'):
                    continue
                fields.append({'key': str(q['question']), 'title': str(q.get('header') or ''),
                               'description': str(q['question']),
                               'type': 'array' if q.get('multiSelect') else 'string', 'custom': True,
                               'options': [{'value': str(o.get('label', '')), 'label': str(o.get('label', '')),
                                            'description': str(o.get('description') or '')}
                                           for o in q.get('options') or [] if isinstance(o, dict)]})
            self.owner.event(self.sid, 'form.created', form={'id': rid, 'sessionID': self.sid, 'fields': fields})
            return
        resources = [summary(tool, data)]
        if request.get('description') and request['description'] not in resources:
            resources.append(str(request['description'])[:300])
        self.owner.event(self.sid, 'permission.asked', id=rid, action=tool, resources=resources)

    def _exit(self, code: int | None, stderr: str) -> None:
        self._cancel_timer()
        self.owner.forget(self)
        if self.busy:
            self._clear_pending()
            self.busy, self.queued = False, 0
            if self.interrupting:
                self.owner.event(self.sid, 'session.execution.interrupted', f'end:exit:{uuid.uuid4().hex}')
            else:
                detail = stderr or f'結束代碼 {code}'
                self.owner.event(self.sid, 'session.execution.failed', f'end:exit:{uuid.uuid4().hex}',
                                 error={'message': f'Claude Code 意外結束：{detail}'})


class ClaudeClient(Adapter):
    client = CLIENT
    name = 'Claude Code'

    def __init__(self, find=find_executable):
        super().__init__()
        self._find = find
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._fresh: set[str] = set()
        self._closed = False

    def command(self) -> list[str]:
        found = self._find()
        if not found:
            raise ClientError('找不到 Claude Code（claude）。請先安裝並登入一次。')
        return list(found) if isinstance(found, (list, tuple)) else [found]

    def new_session(self, directory: str) -> str:
        sid = str(uuid.uuid4())
        with self._lock:
            self._fresh.add(sid)
        return sid

    def _session(self, row: dict) -> Session:
        sid = row['session_id']
        with self._lock:
            if self._closed:
                raise ClientError('AgentDock 正在結束。')
            session = self._sessions.get(sid)
            if session is not None and session.process.alive():
                return session
            # A new session stays "fresh" until Claude Code has started it, so a failed first start
            # does not leave a session id that --resume cannot find.
            session = Session(self, sid, row.get('directory') or os.path.expanduser('~'), sid in self._fresh)
            self._sessions[sid] = session
            return session

    def _live(self, sid: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
        return session if session is not None and session.process.alive() else None

    def started(self, sid: str) -> None:
        with self._lock:
            self._fresh.discard(sid)

    def forget(self, session: Session) -> None:
        with self._lock:
            if self._sessions.get(session.sid) is session:
                del self._sessions[session.sid]

    def retire(self, session: Session) -> None:
        """Idle for a while: close the process; the next message resumes the session."""
        if not session.busy and not session.pending:
            self.forget(session)
            session.close()

    def running(self, sid: str) -> bool:
        session = self._live(sid)
        return bool(session and session.busy)

    def prompt(self, row: dict, text: str) -> None:
        self._session(row).prompt(text)

    def interrupt(self, row: dict) -> None:
        session = self._live(row['session_id'])
        if session is None or not session.busy:
            self.event(row['session_id'], 'session.execution.interrupted', f'end:{uuid.uuid4().hex}')
            return
        session.interrupt()

    def _pending(self, row: dict, request_id: str) -> tuple[Session, dict]:
        session = self._live(row['session_id'])
        request = session.pending.get(request_id) if session else None
        if session is None or request is None:
            raise ClientError('這個請求已失效（Claude Code 已不在等待）。')
        return session, request

    def reply_permission(self, row: dict, request_id: str, decision: str) -> None:
        session, request = self._pending(row, request_id)
        data = request.get('input') if isinstance(request.get('input'), dict) else {}
        if decision == 'reject':
            response: dict = {'behavior': 'deny', 'message': DENIED}
        else:
            response = {'behavior': 'allow', 'updatedInput': data}
            if decision == 'always':
                suggestions = [s for s in request.get('permission_suggestions') or [] if isinstance(s, dict)]
                rules = [s for s in suggestions if s.get('type') == 'addRules']
                response['updatedPermissions'] = rules or [s for s in suggestions if s.get('type') != 'setMode']
        session.pending.pop(request_id, None)
        session.respond(request_id, response)
        self.event(row['session_id'], 'permission.replied', requestID=request_id)

    def reply_form(self, row: dict, form_id: str, answer: dict | None) -> None:
        session, request = self._pending(row, form_id)
        data = request.get('input') if isinstance(request.get('input'), dict) else {}
        if answer is None:
            response: dict = {'behavior': 'deny', 'message': SKIPPED}
        else:
            answers = {k: ', '.join(map(str, v)) if isinstance(v, list) else str(v) for k, v in answer.items()}
            response = {'behavior': 'allow', 'updatedInput': {**data, 'answers': answers}}
        session.pending.pop(form_id, None)
        session.respond(form_id, response)
        self.event(row['session_id'], 'form.replied', id=form_id)

    def history(self, row: dict, cursor: str | None) -> tuple[list[dict], str | None]:
        return page(read_history(transcript_file(row.get('directory', ''), row['session_id'])), cursor)

    def delete(self, row: dict) -> None:
        """Claude Code keeps a session as its transcript (plus a folder for subagents and tool output)."""
        sid = row['session_id']
        session = self._live(sid)
        if session is not None:
            if session.busy:
                raise ClientError('這個對話還在執行。')
            self.forget(session)
            session.close()
        path = transcript_file(row.get('directory', ''), sid)
        if path is None:
            return
        try:
            path.unlink()
            extra = path.with_suffix('')
            if extra.is_dir():
                shutil.rmtree(extra, ignore_errors=True)
        except OSError as e:
            raise ClientError(f'無法刪除 Claude Code 的紀錄：{e}') from e

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()
