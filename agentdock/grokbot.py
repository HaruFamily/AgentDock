"""Grok Bot desktop account adapter. No GUI imports and no credential writes.

Protocol verified against Grok Bot 0.71.2's DashboardService. The desktop
secret store supplies a short-lived access token; Grok Bot owns token renewal.
"""
from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentdock.files import atomic_json

API = 'https://api2.cursor.sh/aiserver.v1.DashboardService/'
LIMIT = 4 * 1024 * 1024


class GrokBotError(RuntimeError):
    pass


def user_data_dir() -> Path:
    return Path(os.environ.get('SAND_USER_DATA_DIR') or
                Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming') / 'Grok Bot')


def _json_file(path: Path) -> dict:
    try:
        if path.stat().st_size > LIMIT:
            raise ValueError()
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise GrokBotError('無法讀取 GrokBot 登入資料，請先開啟並登入 GrokBot。') from None


def _unprotect(raw: bytes) -> bytes:
    if os.name != 'nt':
        raise GrokBotError('GrokBot 登入接入目前只支援 Windows。')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise GrokBotError('無法解密 GrokBot 登入，請以相同 Windows 使用者執行。')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _decrypt(value: str, directory: Path) -> str:
    try:
        if value.startswith('plaintext:v1:'):
            return base64.b64decode(value[13:], validate=True).decode('utf-8')
        if value.startswith('scoped:v1:'):
            value = value.split(':', 3)[3]
        raw = base64.b64decode(value, validate=True)
        if raw.startswith(b'v10'):
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            encrypted = base64.b64decode(_json_file(directory / 'Local State')['os_crypt']['encrypted_key'], validate=True)
            if not encrypted.startswith(b'DPAPI'):
                raise ValueError()
            key = _unprotect(encrypted[5:])
            return AESGCM(key).decrypt(raw[3:15], raw[15:], None).decode('utf-8')
        return _unprotect(raw).decode('utf-8')
    except GrokBotError:
        raise
    except Exception:
        raise GrokBotError('GrokBot 登入格式無法辨識，請重新開啟 GrokBot。') from None


@dataclass(frozen=True)
class Identity:
    account: str
    team: int | None
    token: str = field(repr=False, compare=False)
    label: str = field(default='', compare=False)


def credentials(directory: Path | None = None) -> Identity:
    directory = directory or user_data_dir()
    data = _json_file(directory / 'sand-secrets.json')
    if data.get('version', 1) != 1:
        raise GrokBotError('GrokBot 登入格式已改變。')
    try:
        if 'cursor-accounts' in data:
            accounts = json.loads(data['cursor-accounts'])
            active = accounts['active']
            if not active:
                raise ValueError()
            slot = accounts['accounts'][active]
        else:
            active, slot = None, data
        token = _decrypt(slot['cursor-access-token'], directory)
        payload = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
        principal = payload.get('sub')
        if not isinstance(principal, str) or not principal:
            raise ValueError()
        account = hashlib.sha256(principal.encode()).hexdigest()
        if active is not None and account != active:
            raise ValueError()
        if not isinstance(payload.get('exp'), (int, float)) or payload['exp'] <= time.time() + 15:
            raise GrokBotError('GrokBot 登入已過期，請開啟 GrokBot 更新登入後重試。')
        selected = slot.get('cursor-selected-team-id')
        team = int(_decrypt(selected, directory)) if selected else None
        if team is not None and team <= 0:
            raise ValueError()
        label = payload.get('email')
        if not isinstance(label, str) or not label:
            label = principal
        return Identity(account, team, token, label)
    except GrokBotError:
        raise
    except (KeyError, ValueError, TypeError, IndexError):
        raise GrokBotError('GrokBot 帳號資料不完整，請重新登入 GrokBot。') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, directory: Path | None = None):
        self.directory = directory

    def identity(self) -> Identity:
        return credentials(self.directory)

    def call(self, method: str, payload: dict, identity: Identity) -> dict:
        if method not in {'GetMcpConfig', 'SetMcpConfig', 'GetSandUsageStatus'}:
            raise GrokBotError('不支援的 GrokBot 操作。')
        headers = {'Authorization': 'Bearer ' + identity.token, 'Content-Type': 'application/json',
                   'Connect-Protocol-Version': '1', 'x-cursor-client-type': 'sand',
                   'x-cursor-client-source': 'sand-desktop', 'x-cursor-client-os': 'win32',
                   'x-sand-box-namespace': 'prod', 'x-ghost-mode': 'true'}
        if identity.team is not None:
            headers['x-cursor-team-id'] = str(identity.team)
        request = urllib.request.Request(API + method, json.dumps(payload).encode(), headers, method='POST')
        try:
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
                raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise GrokBotError('GrokBot 登入或權限不足，請在 GrokBot 確認帳號後重試。') from None
            raise GrokBotError(f'GrokBot 服務回傳 HTTP {e.code}。') from None
        except (OSError, ValueError):
            raise GrokBotError('無法確認 GrokBot 服務回應，請重新整理後確認狀態。') from None

    def snapshot(self) -> 'Snapshot':
        identity = self.identity()
        reply = self.call('GetMcpConfig', {'teamScope': False, 'redactSecrets': False}, identity)
        try:
            encoded = reply.get('configJson', '')
            if not isinstance(encoded, str) or set(reply) - {'configJson', 'serverMetadataByName'}:
                raise ValueError()
            config = json.loads(encoded) if encoded else {'mcpServers': {}}
            servers = config['mcpServers']
            metadata = reply.get('serverMetadataByName', {})
            if not isinstance(servers, dict) or not isinstance(metadata, dict):
                raise ValueError()
            if any(not isinstance(v, dict) for v in servers.values()) or any(not isinstance(v, dict) for v in metadata.values()):
                raise ValueError()
            return Snapshot(identity, config, metadata)
        except (KeyError, ValueError, TypeError):
            raise GrokBotError('GrokBot MCP 回應格式已改變，未修改設定。') from None

    def usage(self) -> dict:
        return self.call('GetSandUsageStatus', {}, self.identity())

    def write(self, snapshot: 'Snapshot', config: dict) -> None:
        ids = {name: str(info['serverId']) for name, info in snapshot.metadata.items()
               if name in config['mcpServers'] and info.get('serverId') not in (None, 0, '0')}
        self.call('SetMcpConfig', {'teamScope': False, 'configJson': json.dumps(config),
                                  'serverIdsByName': ids}, snapshot.identity)


@dataclass
class Snapshot:
    identity: Identity
    config: dict
    metadata: dict

    def same(self, other: 'Snapshot') -> bool:
        return self.identity == other.identity and self.config == other.config and self.metadata == other.metadata


def remote_entry(entry: dict) -> dict:
    """Only HTTP/SSE is runnable without installing tools on Grok Bot's cloud computer."""
    from urllib.parse import urlsplit
    if not isinstance(entry, dict) or set(entry) - {'url', 'type', 'headers'}:
        raise GrokBotError('僅支援遠端 MCP 的 url、type 與 headers。')
    url = entry.get('url')
    if not isinstance(url, str):
        raise GrokBotError('GrokBot 的本機指令在雲端電腦執行，不能使用這台 Windows 的 MCP 指令。')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise GrokBotError('遠端 MCP 必須使用 HTTPS 網址。')
    if entry.get('type', 'http') not in ('http', 'sse'):
        raise GrokBotError('遠端 MCP 類型必須是 http 或 sse。')
    headers = entry.get('headers', {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        raise GrokBotError('headers 必須是文字鍵值。')
    return copy.deepcopy(entry)


@dataclass
class Change:
    original: Snapshot
    config: dict
    summary: list[str]


def prepare(snapshot: Snapshot, operations: list[dict]) -> Change:
    import re
    config = copy.deepcopy(snapshot.config)
    servers = config['mcpServers']
    summary = []
    for op in operations:
        name = op['name']
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', name) or name in ('__proto__', 'constructor', 'prototype'):
            raise GrokBotError('MCP 名稱僅可使用英文、數字、點、底線及連字號。')
        if name == 'cursor-box-computer':
            raise GrokBotError('此名稱由 GrokBot 的內建 MCP 使用。')
        if snapshot.metadata.get(name, {}).get('pluginId') not in (None, 0, '0'):
            raise GrokBotError('外掛管理的 MCP 請在 GrokBot 修改。')
        action = op['op']
        if action == 'add':
            if name in servers:
                raise GrokBotError(f'{name} 已存在。')
            servers[name] = remote_entry(op['entry'])
            summary.append(f'{name} → 加入 {servers[name]["url"]}')
        elif action in ('update', 'remove'):
            if name not in servers:
                raise GrokBotError(f'{name} 已不存在，請重新整理。')
            remote_entry(servers[name])  # Do not replace unfamiliar configs or cloud commands.
            if action == 'remove':
                del servers[name]
                summary.append(f'{name} → 移除（OAuth 可能需要重新登入）')
            else:
                servers[name] = remote_entry(op['entry'])
                summary.append(f'{name} → 更新 {servers[name]["url"]}')
        else:
            raise GrokBotError('不支援的 MCP 操作。')
    return Change(snapshot, config, summary)


def apply(client: Client, change: Change, backup_dir: Path) -> Path:
    """Backup before the single account write. Never replay an uncertain write."""
    import uuid
    current = client.snapshot()
    if not change.original.same(current):
        raise GrokBotError('GrokBot 帳號或設定在預覽後改變，請重新整理並預覽。')
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f'grokbot-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json'
    atomic_json(backup, {'account': current.identity.account, 'team': current.identity.team,
                         'config': current.config, 'metadata': current.metadata})
    # Recheck after backup creation; the service offers no compare-and-swap token.
    fresh = client.snapshot()
    if not current.same(fresh):
        raise GrokBotError('GrokBot 設定已改變，未套用；請重新預覽。')
    try:
        client.write(fresh, change.config)
        result = client.snapshot()
        if result.identity != fresh.identity or result.config != change.config:
            raise GrokBotError('GrokBot 回讀與預覽不同。')
    except Exception:
        raise GrokBotError(f'無法確認套用結果，未自動重送或覆寫；請重新整理。原設定備份：{backup}') from None
    return backup


def prepare_restore(snapshot: Snapshot, backup: dict) -> Change:
    if backup.get('account') != snapshot.identity.account or backup.get('team') != snapshot.identity.team:
        raise GrokBotError('備份屬於不同的 GrokBot 帳號或團隊。')
    try:
        old, wanted = snapshot.config['mcpServers'], backup['config']['mcpServers']
        if not isinstance(wanted, dict):
            raise ValueError()
        operations = [{'name': name, 'op': 'remove'} for name in old if name not in wanted]
        operations += [{'name': name, 'op': 'update' if name in old else 'add', 'entry': entry}
                       for name, entry in wanted.items() if old.get(name) != entry]
        return prepare(snapshot, operations)
    except (KeyError, TypeError, ValueError):
        raise GrokBotError('備份格式無法辨識。') from None


def usage_values(reply: dict) -> tuple[float | None, str | None, str | None, str | None]:
    plan = reply.get('grokPlanLabel') or reply.get('cursorPlanName')
    if not isinstance(plan, str):
        plan = None
    if reply.get('usesPooledEnterpriseAllowance') is True:
        return None, None, plan, '共用企業額度，未提供個人百分比'
    percent = reply.get('usagePercent')
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or percent < 0:
        return None, None, plan, '未回傳用量百分比'
    reset = reply.get('nextResetTimestampUtc')
    return float(percent), reset if isinstance(reset, str) else None, plan, ('與 Grok／Cursor 共用額度' if reply.get('isUnified') else None)
