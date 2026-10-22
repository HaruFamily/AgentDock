"""Background connection to the clients that AgentDock sends tasks to.

UI calls return immediately; the network work runs on worker threads and every failure is
reported through `notify`. Decisions (permissions, answers, stops) only arrive from UI clicks.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Callable

from agentdock.dispatch.adapter import Adapter, ClientError
from agentdock.dispatch.opencode import CLIENT, OpenCodeError, OpenCodeService, message_text
from agentdock.dispatch.store import FINAL, DispatchStore, key_for

OUTCOMES = {'succeeded': 'done', 'failed': 'failed', 'interrupted': 'interrupted'}
RETRY_SECONDS = (1, 2, 5, 10)


def title_for(text: str) -> str:
    line = next((l.strip() for l in text.splitlines() if l.strip()), '')
    return line[:60] + ('…' if len(line) > 60 else '')


def default_adapters() -> list[Adapter]:
    from agentdock.dispatch.claude import ClaudeClient
    from agentdock.dispatch.codex import CodexClient
    from agentdock.dispatch.grok import GrokClient
    return [ClaudeClient(), CodexClient(), GrokClient()]


class Dispatcher:
    """OpenCode through its shared service; Claude Code and Codex through adapters (child processes)."""

    def __init__(self, store: DispatchStore, notify: Callable[[str], None] = lambda _m: None,
                 connect: Callable[[bool], OpenCodeService] = OpenCodeService.connect,
                 adapters: list[Adapter] | None = None):
        self.store, self.notify, self._connect = store, notify, connect
        self._service: OpenCodeService | None = None
        self._lock = threading.Lock()
        self._listener: threading.Thread | None = None
        self._closed = threading.Event()
        self._ready = threading.Event()   # subscribed and caught up
        self._holds = 0                   # sends in progress keep the listener alive
        self.adapters: dict[str, Adapter] = {}
        for adapter in adapters if adapters is not None else default_adapters():
            adapter.emit = lambda sid, event, client=adapter.client: self.handle(event, client)
            adapter.remember = lambda options, client=adapter.client: self.store.remember_options(client, options)
            self.adapters[adapter.client] = adapter
        self.clients = (CLIENT, *self.adapters)
        self.listeners: list[Callable[[str, dict], None]] = []   # live events of tracked sessions

    # -- public, called from the UI thread -----------------------------------
    def resume(self) -> None:
        """At start-up: ask OpenCode about cards that were not finished, without starting it.

        Claude Code and Codex ran as children of the previous AgentDock, so their unfinished turns
        ended with it.
        """
        for key, row in list(self.store.rows.items()):
            if row.get('client') in self.adapters and (row['state'] not in FINAL or row.get('permissions')
                                                       or row.get('forms')):
                self.store.update(key, state='interrupted', permissions={}, forms={},
                                  error='AgentDock 結束時中斷。' if row['state'] not in FINAL else row.get('error', ''))
        if self._needs_watch():
            self._ensure_listener()

    def start_task(self, directory: str, text: str, owner: str, project: str | None = None,
                   started: Callable[[str], None] | None = None, client: str = CLIENT,
                   settings: dict | None = None, files: list[str] | None = None) -> None:
        settings = {k: v for k, v in (settings or {}).items() if v}
        if client in self.adapters:
            self._work(lambda: self._start_adapter(self.adapters[client], directory, text, owner, project, started,
                                                   settings, files or []), '無法送出任務')
        else:
            self._work(lambda: self._start(directory, text, owner, project, started, settings, files or []),
                       '無法送出任務')

    def options(self, client: str, key: str | None, directory: str, done: Callable[[dict], None]) -> None:
        """The pickers' choices: cached ones at once, then the client's current list."""
        cached = self.store.options.get(client, {})
        if cached:
            done(cached)

        def run():
            row = self.store.get(key) if key else None
            adapter = self.adapters.get(client)
            if adapter is not None:
                fresh = adapter.options(row, directory)
            else:
                service = self._service_for(start=False)
                fresh = service.options(directory or (row or {}).get('directory', ''))
                self.store.remember_options(client, fresh)
            fresh = {**self.store.options.get(client, {}), **{k: v for k, v in (fresh or {}).items() if v}}
            if fresh:
                done(fresh)

        def quietly():
            # The pickers simply keep what they have when a client cannot list them (not installed, offline).
            try:
                run()
            except Exception as e:
                logging.info('options of %s: %s', client, e)
        threading.Thread(target=quietly, name='AgentDock options', daemon=True).start()

    def configure(self, key: str, **changes: str) -> None:
        """Model / effort / mode for this conversation; applied now when the client allows it."""
        self.store.update(key, **changes)

        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            if adapter is not None:
                adapter.configure(row, changes)
            else:
                self._configure_opencode(self._service_for(True), row, changes)
        self._work(run, '無法變更設定', key)

    def compact(self, key: str) -> None:
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            self.store.update(key, state='running', error='')
            try:
                if adapter is not None:
                    adapter.compact(row)
                else:
                    with self._listening():
                        self._service_for(True).compact(row['session_id'])
            except (ClientError, OpenCodeError) as e:
                self.store.update(key, state='failed', error=str(e))
                raise
        self._work(run, '無法壓縮對話', key)

    @staticmethod
    def _configure_opencode(service: OpenCodeService, row: dict, changes: dict) -> None:
        if ('model' in changes or 'effort' in changes) and row.get('model'):
            service.set_model(row['session_id'], row['model'], row.get('effort', ''))
        if changes.get('mode'):
            service.set_agent(row['session_id'], changes['mode'])

    def history(self, key: str, cursor: str | None, done: Callable[[list[dict], str | None], None],
                failed: Callable[[str], None] | None = None) -> None:
        """Load a page of stored messages (oldest first in the callback) on a worker thread."""
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            if adapter is not None:
                try:
                    messages, older = adapter.history(row, cursor)
                except ClientError as e:
                    if failed:
                        failed(str(e))
                        return
                    raise
                done(messages, older)
                return
            try:
                messages, older = self._service_for(start=True).message_page(row['session_id'], 60, cursor)
            except OpenCodeError as e:
                self._drop_service()
                if failed:
                    failed(str(e))
                    return
                raise
            done(list(reversed(messages)), older)
        self._work(run, '無法讀取對話紀錄', key)

    def send(self, key: str, text: str, files: list[str] | None = None) -> None:
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            if adapter is not None:
                self.store.update(key, state='running', prompt=text, error='')
                try:
                    adapter.prompt(row, text, files or [])
                except ClientError as e:
                    self.store.update(key, state='failed', error=str(e))
                    raise
                return
            service = self._service_for(start=True)
            with self._listening():
                self.store.update(key, state='running', prompt=text, error='')
                try:
                    service.prompt(row['session_id'], text, files or [])
                except OpenCodeError as e:
                    self.store.update(key, state='failed', error=str(e))
                    raise
        self._work(run, '無法送出訊息', key)

    def interrupt(self, key: str) -> None:
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            if adapter is not None:
                adapter.interrupt(row)
            else:
                self._service_for(True).interrupt(row['session_id'])
        self._work(run, '無法停止', key)

    def reply_permission(self, key: str, request_id: str, decision: str) -> None:
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            try:
                if adapter is not None:
                    adapter.reply_permission(row, request_id, decision)
                else:
                    self._service_for(True).reply_permission(row['session_id'], request_id, decision)
            except ClientError:
                self._drop_request(key, 'permissions', request_id)   # it no longer waits; do not keep asking
                raise
            self._drop_request(key, 'permissions', request_id)
        self._work(run, '無法回覆授權', key)

    def reply_form(self, key: str, form_id: str, answer: dict | None) -> None:
        def run():
            row = self._row(key)
            adapter = self._adapter(row)
            try:
                if adapter is not None:
                    adapter.reply_form(row, form_id, answer)
                elif answer is None:
                    self._service_for(True).cancel_form(row['session_id'], form_id)
                else:
                    self._service_for(True).reply_form(row['session_id'], form_id, answer)
            except ClientError:
                self._drop_request(key, 'forms', form_id)
                raise
            self._drop_request(key, 'forms', form_id)
        self._work(run, '無法回覆問題', key)

    def _drop_request(self, key: str, field: str, request_id: str) -> None:
        row = self.store.get(key)
        if row is not None:
            self.store.update(key, **{field: {k: v for k, v in row.get(field, {}).items() if k != request_id}})

    def delete(self, key: str, remote: bool, done: Callable[[], None] = lambda: None) -> None:
        """Remove a conversation; with `remote`, delete it in the client first (then only on success)."""
        def run():
            row = self._row(key)
            if row['state'] == 'running':
                raise ValueError('這個對話還在執行，請先停止。')
            if remote:
                adapter = self._adapter(row)
                if adapter is not None:
                    adapter.delete(row)
                else:
                    self._service_for(True).delete_session(row['session_id'])
            done()
        self._work(run, '無法刪除對話', key)

    def adopt(self, client: str, session_id: str, owner: str, title: str) -> None:
        """Take over a session that was started in the client, so the Inbox can continue it."""
        if client != CLIENT:
            self.notify('這個 Agent 還不能從 AgentDock 接手。')
            return
        def run():
            key = key_for(client, session_id)
            self.store.track(client, session_id, owner=owner, title=title, state='unknown')
            self._reconcile(self._service_for(True), key)
            self._ensure_listener()
        self._work(run, '無法接手')

    def close(self) -> None:
        self._closed.set()
        for adapter in self.adapters.values():
            try:
                adapter.close()
            except Exception:
                logging.exception('dispatch close')

    # -- internals ------------------------------------------------------------
    def _work(self, job: Callable[[], None], failure: str, key: str | None = None) -> None:
        def run():
            try:
                job()
            except (OpenCodeError, ClientError, KeyError, ValueError) as e:
                self.notify(f'{failure}：{e}')
            except Exception as e:  # never let a worker kill the app
                logging.exception('dispatch')
                self.notify(f'{failure}：{e}')
        threading.Thread(target=run, name='AgentDock dispatch', daemon=True).start()

    def _row(self, key: str) -> dict:
        row = self.store.get(key)
        if row is None:
            raise KeyError('這張卡片已移除。')
        return row

    def _adapter(self, row: dict) -> Adapter | None:
        return self.adapters.get(row.get('client', CLIENT))

    def _start_adapter(self, adapter: Adapter, directory: str, text: str, owner: str, project: str | None,
                       started: Callable[[str], None] | None, settings: dict | None = None,
                       files: list[str] | None = None) -> None:
        if not text.strip():
            raise ValueError('內容是空的。')
        session_id = adapter.new_session(directory, settings or {})
        key = key_for(adapter.client, session_id)
        self.store.track(adapter.client, session_id, owner=owner, title=title_for(text), directory=directory,
                         prompt=text, project=project, settings=settings)
        if started:
            started(key)
        try:
            adapter.prompt(self._row(key), text, files or [])
        except ClientError as e:
            self.store.update(key, state='failed', error=str(e))
            raise

    def _service_for(self, start: bool) -> OpenCodeService:
        with self._lock:
            if self._service is None:
                self._service = self._connect(start)
            return self._service

    def _drop_service(self) -> None:
        with self._lock:
            self._service = None

    def _start(self, directory: str, text: str, owner: str, project: str | None = None,
               started: Callable[[str], None] | None = None, settings: dict | None = None,
               files: list[str] | None = None) -> None:
        if not text.strip():
            raise ValueError('內容是空的。')
        service = self._service_for(start=True)
        title = title_for(text)
        with self._listening():
            session = service.create_session(directory, title)
            key = key_for(CLIENT, session['id'])
            self.store.track(CLIENT, session['id'], owner=owner, title=title, directory=directory, prompt=text,
                             project=project, settings=settings)
            if settings:
                self._configure_opencode(service, self._row(key), settings)
            if started:
                started(key)
            try:
                service.prompt(session['id'], text, files or [])
            except OpenCodeError as e:
                self.store.update(key, state='failed', error=str(e))
                raise

    @contextlib.contextmanager
    def _listening(self):
        """Be subscribed before sending, so even an instant reply is seen as events."""
        with self._lock:
            self._holds += 1
        try:
            self._ensure_listener()
            self._ready.wait(5)
            yield
        finally:
            with self._lock:
                self._holds -= 1

    def _ensure_listener(self) -> None:
        with self._lock:
            if self._listener is not None:
                return
            self._listener = threading.Thread(target=self._listen, name='AgentDock OpenCode events', daemon=True)
            self._listener.start()

    def _needs_watch(self) -> bool:
        return any(r['client'] == CLIENT and (r['state'] not in FINAL or r.get('permissions') or r.get('forms'))
                   for r in self.store.rows.values())

    def _wanted(self) -> bool:
        return not self._closed.is_set() and (self._holds > 0 or self._needs_watch())

    def _keep_listening(self) -> bool:
        # Decided under the same lock as _ensure_listener, so a send never lands on a listener that is leaving.
        with self._lock:
            if not self._wanted():
                self._listener = None
                self._ready.clear()
                return False
            return True

    def _listen(self) -> None:
        failures = 0
        while self._keep_listening():
            self._ready.clear()
            try:
                service = self._service_for(start=False)
                events = service.events(timeout=90)
                # Subscribe first, then read the current state, so nothing falls in between.
                first = next(events, None)
                for key in self.store.keys(CLIENT):
                    self._reconcile(service, key)
                self.store.set_offline('')
                self._ready.set()
                failures = 0
                if first:
                    self.handle(first)
                for event in events:
                    if self._closed.is_set():
                        break
                    self.handle(event)
                    if not self._wanted():
                        break
                else:
                    raise OpenCodeError('OpenCode 連線中斷。')
                continue
            except OpenCodeError as e:
                self._drop_service()
                self.store.set_offline(str(e))
            except Exception:
                logging.exception('OpenCode events')
                self._drop_service()
                self.store.set_offline('OpenCode 連線中斷。')
            self._ready.set()  # a waiting send fails fast instead of hanging
            if self._closed.wait(RETRY_SECONDS[min(failures, len(RETRY_SECONDS) - 1)]):
                with self._lock:
                    self._listener = None
                return
            failures += 1

    def _reconcile(self, service: OpenCodeService, key: str) -> None:
        row = self.store.get(key)
        if row is None:
            return
        sid = row['session_id']
        try:
            session = service.session(sid)
        except OpenCodeError as e:
            if '404' in str(e):
                self.store.update(key, state='failed', error='OpenCode 找不到這個工作階段。', permissions={}, forms={})
                return
            raise
        changes = {'title': row['title'] if row.get('custom_title') else session.get('title') or row['title'],
                   'directory': (session.get('location') or {}).get('directory') or row['directory'],
                   'permissions': {p['id']: _permission(p) for p in service.permissions(sid)},
                   'forms': {f['id']: f for f in service.forms(sid)}}
        messages = service.messages(sid)
        if sid in service.active():
            changes['state'] = 'running'
        elif session.get('outcome'):
            changes['state'] = OUTCOMES.get(session['outcome'], 'done')
            idle = next((m for m in messages if m.get('type') == 'idle'), {})
            if changes['state'] == 'failed':
                changes['error'] = (idle.get('error') or {}).get('message') or row.get('error') or '執行失敗'
        elif row['state'] == 'unknown':
            changes['state'] = 'done'   # never ran, nothing to wait for
        prompt = next((m for m in messages if m.get('type') == 'user'), None)
        reply = next((m for m in messages if m.get('type') == 'assistant' and message_text(m)), None)
        if prompt:
            changes['prompt'] = message_text(prompt)
        if reply:
            changes['reply'], changes['reply_message'] = message_text(reply), reply['id']
        self.store.update(key, **changes)

    def handle(self, event: dict, client: str = CLIENT) -> None:
        kind = event.get('type', '')
        data = event.get('data') or {}
        sid = data.get('sessionID') or (data.get('form') or {}).get('sessionID')
        if not sid:
            return
        key = key_for(client, sid)
        row = self.store.get(key)
        if row is None:
            return
        for listener in list(self.listeners):
            try:
                listener(key, event)
            except Exception:  # a closed view must not stop the event stream
                logging.exception('dispatch listener')
        if kind == 'session.execution.started':
            self.store.update(key, state='running', error='')
        elif kind == 'session.inbox.enqueued' and (data.get('item') or {}).get('type') == 'user':
            self.store.update(key, prompt=str((data['item'].get('payload') or {}).get('text', '')))
        elif kind == 'session.tool.input.started':
            self.store.update(key, doing=str(data.get('name', '')))
        elif kind == 'session.text.ended':
            text = str(data.get('text', ''))
            message = data.get('assistantMessageID', '')
            if message and message == row.get('reply_message') and row.get('reply'):
                text = row['reply'] + '\n\n' + text
            self.store.update(key, reply=text, reply_message=message)
        elif kind == 'permission.asked':
            self.store.update(key, permissions={**row.get('permissions', {}), data['id']: _permission(data)})
        elif kind == 'permission.replied':
            rid = data.get('requestID') or data.get('id')
            self.store.update(key, permissions={k: v for k, v in row.get('permissions', {}).items() if k != rid})
        elif kind == 'form.created' and data.get('form', {}).get('id'):
            self.store.update(key, forms={**row.get('forms', {}), data['form']['id']: data['form']})
        elif kind.startswith('form.') and data.get('id'):
            self.store.update(key, forms={k: v for k, v in row.get('forms', {}).items() if k != data['id']})
        elif kind == 'session.execution.succeeded':
            self.store.update(key, state='done', permissions={}, forms={})
        elif kind == 'session.execution.failed':
            error = (data.get('error') or {}).get('message') or '執行失敗'
            self.store.update(key, state='failed', error=str(error), permissions={}, forms={})
        elif kind == 'session.execution.interrupted':
            self.store.update(key, state='interrupted', permissions={}, forms={})


def _permission(data: dict) -> dict:
    return {'action': str(data.get('action', '')), 'resources': [str(r) for r in data.get('resources', [])][:5]}
