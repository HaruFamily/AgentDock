"""OpenCode V2 background service: discovery, HTTP API and the event stream.

OpenCode 2.x runs one shared background server that its TUI also uses. Its address and
password are in `<state>/opencode/service.json`; `opencode service start` starts it.
Tasks therefore keep running when AgentDock exits, and the same sessions are visible in OpenCode.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

CLIENT = 'opencode'
NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW


class OpenCodeError(RuntimeError):
    """A message that can be shown to the user as is."""


def state_dir() -> Path:
    base = os.environ.get('XDG_STATE_HOME')
    return (Path(base) if base else Path.home() / '.local' / 'state') / 'opencode'


def find_executable() -> str | None:
    """The real opencode.exe, not the npm .cmd shim (a shim would flash a console window)."""
    candidates = []
    found = shutil.which('opencode')
    if found:
        path = Path(found)
        if path.suffix.lower() in ('.cmd', '.ps1', '') and path.parent.name.lower() == 'npm':
            candidates.append(path.parent / 'node_modules' / '@opencode' / 'cli' / 'bin' / 'opencode.exe')
        candidates.append(path)
    appdata = os.environ.get('APPDATA')
    if appdata:
        candidates.append(Path(appdata) / 'npm' / 'node_modules' / '@opencode' / 'cli' / 'bin' / 'opencode.exe')
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class OpenCodeService:
    def __init__(self, url: str, password: str, version: str = ''):
        self.url = url.rstrip('/')
        self.version = version
        token = base64.b64encode(f'opencode:{password}'.encode()).decode()
        self._auth = 'Basic ' + token
        # The service is local; a system proxy must never see the password.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- discovery ------------------------------------------------------------
    @classmethod
    def discover(cls) -> 'OpenCodeService | None':
        try:
            info = json.loads((state_dir() / 'service.json').read_text(encoding='utf-8'))
            service = cls(info['url'], info['password'], str(info.get('version', '')))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        try:
            found = service.request('GET', '/api/info', timeout=3)
        except OpenCodeError:
            return None
        service.version = str(found.get('version') or service.version)
        return service

    @classmethod
    def connect(cls, start: bool = False) -> 'OpenCodeService':
        service = cls.discover()
        if service is None and start:
            exe = find_executable()
            if not exe:
                raise OpenCodeError('找不到 OpenCode（opencode.exe）。')
            try:
                subprocess.run([exe, 'service', 'start'], capture_output=True, timeout=60,
                               stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False)
            except (OSError, subprocess.SubprocessError) as e:
                raise OpenCodeError(f'無法啟動 OpenCode 服務：{e}') from e
            service = cls.discover()
        if service is None:
            raise OpenCodeError('OpenCode 服務未執行。' if not start else 'OpenCode 服務沒有回應。')
        major = service.version.split('.')[0]
        if major.isdigit() and int(major) < 2:
            raise OpenCodeError(f'需要 OpenCode 2.x，目前是 {service.version}。')
        return service

    # -- HTTP -----------------------------------------------------------------
    def _open(self, method: str, path: str, body: Any = None, timeout: float = 15):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={'Authorization': self._auth, 'Content-Type': 'application/json',
                                                  'Accept': 'application/json'})
        return self._opener.open(request, timeout=timeout)

    def request(self, method: str, path: str, body: Any = None, timeout: float = 15) -> Any:
        try:
            with self._open(method, path, body, timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = json.loads(e.read() or b'{}').get('message', '')
            except Exception:
                pass
            raise OpenCodeError(f'OpenCode 回應 {e.code}' + (f'：{detail}' if detail else '')) from e
        except (OSError, ValueError) as e:
            raise OpenCodeError(f'無法連線 OpenCode：{e}') from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as e:
            raise OpenCodeError('OpenCode 回應格式不正確。') from e

    def events(self, timeout: float = 60) -> Iterator[dict]:
        """Server-sent events; the server sends a heartbeat, so a silent socket means it is gone."""
        try:
            response = self._open('GET', '/api/event', timeout=timeout)
        except urllib.error.HTTPError as e:
            raise OpenCodeError(f'OpenCode 回應 {e.code}') from e
        except OSError as e:
            raise OpenCodeError(f'無法連線 OpenCode：{e}') from e
        with response:
            data: list[str] = []
            for raw in response:
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if line.startswith('data:'):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    try:
                        yield json.loads('\n'.join(data))
                    except ValueError:
                        pass
                    data = []

    # -- API used by AgentDock -----------------------------------------------
    def create_session(self, directory: str, title: str) -> dict:
        return self.request('POST', '/api/session', {'title': title, 'location': {'directory': directory}})['data']

    def prompt(self, session_id: str, text: str, files: list[str] | None = None) -> dict:
        body: dict = {'text': text}
        if files:
            body['files'] = [{'uri': Path(f).resolve().as_uri(), 'name': Path(f).name} for f in files]
        return self.request('POST', f'/api/session/{_q(session_id)}/prompt', body)['data']

    def fork(self, session_id: str, before: str | None) -> str:
        body = {'before': before} if before else {}
        return self.request('POST', f'/api/session/{_q(session_id)}/fork', body)['data']['id']

    def sessions(self, limit: int = 100) -> list[dict]:
        page = self.request('GET', f"/api/session?{urllib.parse.urlencode({'limit': limit, 'order': 'desc'})}") or {}
        out = []
        for s in page.get('data', []):
            if s.get('parentID'):
                continue   # subagent sessions live inside their parent
            updated = (s.get('time') or {}).get('updated') or (s.get('time') or {}).get('created') or 0
            out.append({'id': s['id'], 'title': str(s.get('title') or s['id']),
                        'directory': str((s.get('location') or {}).get('directory') or ''),
                        'updated': float(updated) / 1000 if updated > 1e11 else float(updated)})
        return out

    def context(self, session_id: str) -> dict:
        """Tokens in the last reply's context: {'used', 'model'}."""
        for message in self.messages(session_id, 10):
            tokens = message.get('tokens') if message.get('type') == 'assistant' else None
            if isinstance(tokens, dict):
                cache = tokens.get('cache') or {}
                used = sum(int(tokens.get(k) or 0) for k in ('input', 'output', 'reasoning')) + \
                    int(cache.get('read') or 0) + int(cache.get('write') or 0)
                model = message.get('model') or {}
                return {'used': used, 'model': f"{model.get('providerID')}/{model.get('id')}" if model else ''}
        return {}

    def compact(self, session_id: str) -> None:
        self.request('POST', f'/api/session/{_q(session_id)}/compact', {})

    def set_model(self, session_id: str, model: str, variant: str = '') -> None:
        provider, _, name = model.partition('/')
        ref = {'id': name, 'providerID': provider, **({'variant': variant} if variant else {})}
        self.request('POST', f'/api/session/{_q(session_id)}/model', {'model': ref})

    def set_agent(self, session_id: str, agent: str) -> None:
        self.request('POST', f'/api/session/{_q(session_id)}/agent', {'agent': agent})

    def options(self, directory: str) -> dict:
        """Models (provider/model, variants as efforts) and primary agents as the modes."""
        query = '?' + urllib.parse.urlencode({'location[directory]': directory}) if directory else ''
        models = []
        for m in (self.request('GET', '/api/model' + query) or {}).get('data', []):
            if m.get('enabled', True) and m.get('status') != 'deprecated':
                models.append({'id': f"{m.get('providerID')}/{m.get('id')}", 'label': str(m.get('name') or m.get('id')),
                               'efforts': [str(v.get('id')) for v in m.get('variants') or [] if v.get('id')],
                               'context': int((m.get('limit') or {}).get('context') or 0)})
        raw_agents = (self.request('GET', '/api/agent' + query) or {}).get('data', [])
        agents = [{'id': str(a['id']), 'label': str(a.get('name') or a['id'])}
                  for a in raw_agents if a.get('mode') in ('primary', 'all') and not a.get('hidden')]
        default = next((a['id'] for a in agents if a['id'] == 'build'), '')
        build = next((a for a in raw_agents if a.get('id') == (default or 'build')), {})
        model, variant = self._default_model(directory, query, build, {m['id'] for m in models})
        defaults = {**({'model': model} if model else {}), **({'effort': {model: variant}} if model and variant else {})}
        return {'models': models, 'modes': agents, **({'mode_default': default} if default else {}),
                'defaults': defaults, 'effort_unset': '標準'}   # no variant: the model's own reasoning

    def _default_model(self, directory: str, query: str, agent: dict, known: set[str]) -> tuple[str, str]:
        """The model (and variant) OpenCode uses when none is chosen, in its own order: the agent's model,
        the config's `model`, the model it last used (state model.json), then the latest conversation's."""
        def ref(value) -> tuple[str, str]:
            if isinstance(value, str) and '/' in value:
                return value, ''
            if isinstance(value, dict):
                name = value.get('modelID') or value.get('id') or ''
                provider = value.get('providerID') or ''
                return (f'{provider}/{name}' if provider and name else ''), str(value.get('variant') or '')
            return '', ''

        candidates = [ref(agent.get('model'))]
        if agent.get('variant') and candidates[0][0]:
            candidates[0] = (candidates[0][0], str(agent['variant']))
        try:
            config = (self.request('GET', '/api/config' + query) or {}).get('data') or {}
        except Exception:
            config = {}
        candidates.append(ref(config.get('model')))
        candidates += [ref(c.get('model')) for c in config_files(directory)]
        variants: dict = {}
        try:
            state = json.loads((state_dir() / 'model.json').read_text(encoding='utf-8'))
            variants = state.get('variant') if isinstance(state.get('variant'), dict) else {}
            candidates += [ref(r) for r in (state.get('recent') or [])[:1]]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            latest = self.sessions(limit=1)
            for message in self.messages(latest[0]['id'], 10) if latest else []:
                if message.get('type') == 'assistant' and message.get('model'):
                    candidates.append(ref(message['model']))
                    break
        except Exception:
            pass
        for model, variant in candidates:
            if model in known:
                return model, variant or str(variants.get(model) or '')
        return '', ''

    def interrupt(self, session_id: str) -> bool:
        return bool((self.request('POST', f'/api/session/{_q(session_id)}/interrupt') or {}).get('interrupted'))

    def reply_permission(self, session_id: str, request_id: str, decision: str) -> None:
        if decision not in ('once', 'always', 'reject'):
            raise ValueError(decision)
        self.request('POST', f'/api/session/{_q(session_id)}/permission/{_q(request_id)}/reply', {'decision': decision})

    def reply_form(self, session_id: str, form_id: str, answer: dict) -> None:
        self.request('POST', f'/api/session/{_q(session_id)}/form/{_q(form_id)}/reply', {'answer': answer})

    def delete_session(self, session_id: str) -> None:
        self.request('DELETE', f'/api/session/{_q(session_id)}')

    def cancel_form(self, session_id: str, form_id: str) -> None:
        self.request('DELETE', f'/api/session/{_q(session_id)}/form/{_q(form_id)}')

    def session(self, session_id: str) -> dict:
        return self.request('GET', f'/api/session/{_q(session_id)}')['data']

    def active(self) -> set[str]:
        return set((self.request('GET', '/api/session/active') or {}).get('data', {}))

    def permissions(self, session_id: str) -> list[dict]:
        return (self.request('GET', f'/api/session/{_q(session_id)}/permission') or {}).get('data', [])

    def forms(self, session_id: str) -> list[dict]:
        return (self.request('GET', f'/api/session/{_q(session_id)}/form') or {}).get('data', [])

    def messages(self, session_id: str, limit: int = 20) -> list[dict]:
        """Newest first."""
        return self.message_page(session_id, limit)[0]

    def message_page(self, session_id: str, limit: int = 50, cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Newest first, and the cursor for the next (older) page, or None at the start."""
        # The cursor already carries the order; OpenCode rejects both together.
        params = {'limit': limit, 'cursor': cursor} if cursor else {'limit': limit, 'order': 'desc'}
        page = self.request('GET', f'/api/session/{_q(session_id)}/message?{urllib.parse.urlencode(params)}') or {}
        data = page.get('data', [])
        older = (page.get('cursor') or {}).get('next') if len(data) >= limit else None
        return data, older


def config_files(directory: str) -> list[dict]:
    """OpenCode's config files that can set `model`: the project's first, then the user's."""
    from agentdock.agents import strip_jsonc
    out = []
    folders = [Path(directory)] if directory else []
    folders.append(Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'opencode')
    for folder in folders:
        for name in ('opencode.json', 'opencode.jsonc'):
            try:
                data = json.loads(strip_jsonc((folder / name).read_text(encoding='utf-8'))[0] or '{}')
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def _q(value: str) -> str:
    return urllib.parse.quote(value, safe='')


def message_text(message: dict) -> str:
    """Plain text of a V2 user or assistant message."""
    if message.get('type') == 'user':
        return str(message.get('text') or (message.get('payload') or {}).get('text') or '')
    parts = [c.get('text', '') for c in message.get('content', []) if c.get('type') == 'text']
    return '\n\n'.join(p for p in parts if p)
