"""Agent list + MCP management for Codex, OpenCode, Claude Code and Claude Desktop.

Every change is planned first (preview), then the original is re-checked, backed up and written.
Codex/OpenCode toggle `enabled`; Claude clients have no such flag, so disabled entries are
moved out of the config into data/disabled-<id>.json and restored on enable.
"""
from __future__ import annotations

import copy
import json
import re
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomlkit

from agentdock.files import atomic_json, atomic_write, backup_path, load_json, prune_backups, read_text

Kind = Literal["codex", "opencode", "claude-code", "claude-desktop"]
KINDS: dict[str, str] = {"codex": "Codex", "opencode": "OpenCode", "claude-code": "Claude Code", "claude-desktop": "Claude Desktop / Cowork"}
QAI_NAMES = ("inbox", "agentchat", "agentdock-qa", "agentdock_qa", "agent_interaction")  # current name first, then legacy
QAI_TIMEOUT_SECONDS = 1860


# --------------------------------------------------------------------------- defaults
def default_path(kind: str) -> str:
    home = Path.home()
    if kind == "codex":
        return str(Path(os.environ.get("CODEX_HOME") or home / ".codex") / "config.toml")
    if kind == "opencode":
        folder = home / ".config" / "opencode"
        # OpenCode reads opencode.json and opencode.jsonc; use the one that already exists.
        if not (folder / "opencode.json").exists() and (folder / "opencode.jsonc").exists():
            return str(folder / "opencode.jsonc")
        return str(folder / "opencode.json")
    if kind == "claude-code":
        return str(home / ".claude.json")
    # Claude Desktop: the Microsoft Store (MSIX) build redirects AppData\Roaming into its package.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Packages"
        if packages.is_dir():
            for pkg in sorted(packages.glob("Claude_*")):
                candidate = pkg / "LocalCache" / "Roaming" / "Claude"
                if candidate.is_dir():
                    return str(candidate / "claude_desktop_config.json")
    appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
    return str(Path(appdata) / "Claude" / "claude_desktop_config.json")


# --------------------------------------------------------------------------- JSONC
def _scan(text: str, drop_comments: bool) -> tuple[str, bool]:
    out: list[str] = []
    i, n, changed = 0, len(text), False
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif drop_comments and text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            changed = True
        elif drop_comments and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ValueError("設定檔有未結束的註解。")
            i = j + 2
            changed = True
        elif not drop_comments and c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                changed = True
            else:
                out.append(c)
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out), changed


def strip_jsonc(text: str) -> tuple[str, bool]:
    """Remove // and /* */ comments and trailing commas. Returns (json, had_extras)."""
    no_comments, a = _scan(text, True)
    clean, b = _scan(no_comments, False)
    return clean, a or b


def _section(kind: str) -> str:
    return "mcp_servers" if kind == "codex" else "mcp" if kind == "opencode" else "mcpServers"


def _direct_disable(kind: str) -> bool:
    return kind in ("codex", "opencode")


def _parse(kind: str, text: str) -> Any:
    if kind == "codex":
        try:
            return tomlkit.parse(text)
        except Exception as e:
            raise ValueError(f"TOML 語法錯誤，請先修正：{e}") from None
    try:
        stripped, _ = strip_jsonc(text or "{}")
        data = json.loads(stripped or "{}")
    except ValueError as e:
        raise ValueError(f"JSON 語法錯誤，請先修正：{e}") from None
    if not isinstance(data, dict):
        raise ValueError("設定格式必須是物件。")
    return data


def _plain(value: Any) -> Any:
    """tomlkit containers -> plain python for comparisons/serialization."""
    if hasattr(value, "unwrap"):
        return value.unwrap()
    return copy.deepcopy(value)


def _servers(kind: str, data: Any) -> dict[str, Any]:
    section = data.get(_section(kind))
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError("MCP 區塊格式必須是物件。")
    return {k: _plain(v) for k, v in section.items()}


def _dump(kind: str, original: str, data: Any) -> tuple[str, bool]:
    """Serialize. Returns (text, reformats_whole_file)."""
    if kind == "codex":
        return tomlkit.dumps(data), False
    _, had_extras = strip_jsonc(original or "{}")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    return text, had_extras


# --------------------------------------------------------------------------- model
@dataclass
class Profile:
    id: str
    name: str
    kind: str
    path: str

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Profile":
        return Profile(id=d["id"], name=d["name"], kind=d["kind"], path=d["path"])


@dataclass
class PlanFile:
    profile: Profile
    original: str
    next: str
    parked: dict[str, Any]
    parked_original: str


@dataclass
class Plan:
    id: str
    files: list[PlanFile] = field(default_factory=list)
    summary: list[dict[str, Any]] = field(default_factory=list)


# MCP entries that the client app itself writes and regenerates (removing them breaks client features).
SYSTEM_NAMES: dict[str, set[str]] = {"codex": {"node_repl"}}
SYSTEM_PATH_HINTS = ("OpenAI.Codex", "WindowsApps", "codex-resources")


def is_system(kind: str, name: str, config: Any) -> bool:
    if name in SYSTEM_NAMES.get(kind, set()):
        return True
    if isinstance(config, dict):
        command = config.get("command")
        text = " ".join(command) if isinstance(command, list) else str(command or "")
        return any(h.lower() in text.lower() for h in SYSTEM_PATH_HINTS)
    return False


def _is_qai(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    env = entry.get("env") or entry.get("environment") or {}
    return any(k in env for k in ("AGENTDOCK_DATA_DIR", "AIL_DATA_DIR"))


class AgentManager:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.file = directory / "agents.json"
        self.profiles = [Profile.from_dict(d) for d in load_json(self.file, [])]

    def _save(self, profiles: list[Profile]) -> None:
        atomic_json(self.file, [p.__dict__ for p in profiles])
        self.profiles = profiles

    def list(self) -> list[Profile]:
        return [copy.copy(p) for p in self.profiles]

    def get(self, pid: str) -> Profile:
        for p in self.profiles:
            if p.id == pid:
                return p
        raise KeyError("Agent 清單已變更，請重新整理。")

    def add(self, name: str, kind: str, path: str) -> Profile:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("請輸入 1–100 字的名稱。")
        if kind not in KINDS:
            raise ValueError("不支援的客戶端類型。")
        target = Path(path.strip().strip('"'))
        if not target.is_absolute():
            raise ValueError("請選擇設定檔的完整路徑。")
        target = target.resolve()
        canon = lambda p: os.path.normcase(str(Path(p).resolve()))
        if any(canon(p.path) == canon(target) for p in self.profiles):
            raise ValueError("這份設定檔已登錄，請使用既有 Agent 項目。")
        if target.exists():
            _parse(kind, read_text(target))
        profile = Profile(id=str(uuid.uuid4()), name=name, kind=kind, path=str(target))
        self._save(self.profiles + [profile])
        return profile

    def update(self, pid: str, name: str, kind: str, path: str) -> Profile:
        current = self.get(pid)
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("請輸入 1–100 字的名稱。")
        if kind not in KINDS:
            raise ValueError("不支援的客戶端類型。")
        if kind != current.kind and self._parked(current):
            raise ValueError("這個 Agent 還有暫存的停用 MCP，請先處理後再改類型。")
        target = Path(path.strip().strip('"'))
        if not target.is_absolute():
            raise ValueError("請選擇設定檔的完整路徑。")
        target = target.resolve()
        canon = lambda p: os.path.normcase(str(Path(p).resolve()))
        if any(p.id != pid and canon(p.path) == canon(target) for p in self.profiles):
            raise ValueError("這份設定檔已由其他 Agent 登錄。")
        if target.exists():
            _parse(kind, read_text(target))
        updated = Profile(id=pid, name=name, kind=kind, path=str(target))
        self._save([updated if p.id == pid else p for p in self.profiles])
        return updated

    def reorder(self, ids: list[str]) -> None:
        """Save a new display order (ids not listed keep their relative order at the end)."""
        rank = {pid: i for i, pid in enumerate(ids)}
        self._save(sorted(self.profiles, key=lambda p: rank.get(p.id, len(rank) + self.profiles.index(p))))

    def remove(self, pid: str) -> None:
        p = self.get(pid)
        if self._parked(p):
            raise ValueError("請先啟用由 AgentDock 暫存的 MCP，再移除登錄，避免遺失還原入口。")
        self._save([x for x in self.profiles if x.id != pid])

    def _parked_path(self, p: Profile) -> Path:
        return self.directory / f"disabled-{p.id}.json"

    def _parked(self, p: Profile) -> dict[str, Any]:
        return load_json(self._parked_path(p), {})

    def inspect(self) -> list[dict[str, Any]]:
        result = []
        for p in self.profiles:
            path = Path(p.path)
            try:
                servers = _servers(p.kind, _parse(p.kind, read_text(path)))
                parked = self._parked(p)
                rows = []
                for name in list(servers) + [n for n in parked if n not in servers]:
                    entry = servers.get(name, parked.get(name))
                    enabled = name in servers and (servers[name].get("enabled", True) is not False if _direct_disable(p.kind) else True)
                    remote = isinstance(entry, dict) and bool(entry.get("url"))
                    rows.append({"name": name, "enabled": enabled, "transport": "remote" if remote else "local",
                                 "qai": name in QAI_NAMES and _is_qai(entry), "config": entry,
                                 "system": is_system(p.kind, name, entry)})
                result.append({**p.__dict__, "error": "", "exists": path.exists(), "servers": rows})
            except Exception as e:
                result.append({**p.__dict__, "error": str(e), "exists": path.exists(), "servers": []})
        return result

    # ------------------------------------------------------------------ planning
    def prepare(self, ops: list[dict[str, Any]]) -> Plan:
        """ops: [{profile, op, server, entry?}] with op in enable/disable/remove/add/update.
        `entry` is already shaped for that client (see library.render)."""
        known = {p.id for p in self.profiles}
        for o in ops:
            if o["profile"] not in known:
                raise ValueError("Agent 清單已變更，請重新整理。")
            if o["op"] not in ("enable", "disable", "remove", "add", "update"):
                raise ValueError(f"未知的操作：{o['op']}")
            if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", o["server"]) or o["server"] in ("__proto__", "constructor", "prototype"):
                raise ValueError("MCP 名稱僅可使用英文、數字、點、底線及連字號。")
        plan = Plan(id=str(uuid.uuid4()))
        for p in self.profiles:
            mine = [o for o in ops if o["profile"] == p.id]
            if not mine:
                continue
            path = Path(p.path)
            original = read_text(path)
            data = _parse(p.kind, original)
            section = _section(p.kind)
            if section not in data or data[section] is None:
                data[section] = tomlkit.table() if p.kind == "codex" else {}
            servers = data[section]
            parked = self._parked(p)
            parked_original = read_text(self._parked_path(p))
            notes: list[str] = []
            direct = _direct_disable(p.kind)

            def exists(name: str) -> bool:
                return name in servers or name in parked

            for o in mine:
                name, op = o["server"], o["op"]
                if op in ("enable", "disable", "remove", "update") and not exists(name):
                    raise ValueError(f"{p.name} 的 {name} 已不存在，請重新整理。")
                if op == "enable" or op == "disable":
                    on = op == "enable"
                    if direct:
                        if name not in servers:  # parked by an older version: restore first
                            servers[name] = parked.pop(name)
                        servers[name]["enabled"] = on
                    elif not on and name in servers:
                        parked[name] = _plain(servers[name])
                        del servers[name]
                    elif on and name not in servers:
                        servers[name] = parked.pop(name)
                    notes.append(f"{name} → {'啟用' if on else '停用'}{'（移出設定並暫存在 AgentDock）' if not direct and not on else ''}")
                elif op == "remove":
                    servers.pop(name, None)
                    parked.pop(name, None)
                    notes.append(f"{name} → 移除")
                elif op == "add":
                    if exists(name):
                        raise ValueError(f"{p.name} 已有 {name}。")
                    if p.kind == "claude-desktop" and o["entry"].get("url"):
                        raise ValueError("Claude Desktop 的遠端 MCP 請使用 App 的「連接器」介面。")
                    entry = copy.deepcopy(o["entry"])
                    if o.get("enabled", True) is False:
                        if direct:
                            entry["enabled"] = False
                            servers[name] = entry
                        else:
                            parked[name] = entry
                    else:
                        servers[name] = entry
                    notes.append(f"{name} → 加入")
                else:  # update keeps the current enabled/parked state
                    entry = copy.deepcopy(o["entry"])
                    if name in servers:
                        if direct:
                            entry["enabled"] = servers[name].get("enabled", True) is not False
                        servers[name] = entry
                    else:
                        if direct:
                            entry["enabled"] = False
                        parked[name] = entry
                    notes.append(f"{name} → 更新設定")
            text, reformats = _dump(p.kind, original, data)
            _parse(p.kind, text)
            plan.files.append(PlanFile(p, original, text, parked, parked_original))
            plan.summary.append({"agent": p.name, "path": p.path, "changes": notes, "reformats": reformats})
        return plan

    # ------------------------------------------------------------------ apply
    def apply(self, plan: Plan) -> list[dict[str, Any]]:
        for f in plan.files:
            if not any(p.id == f.profile.id and p.path == f.profile.path for p in self.profiles) \
                    or read_text(Path(f.profile.path)) != f.original \
                    or read_text(self._parked_path(f.profile)) != f.parked_original:
                raise ValueError("設定或 Agent 清單在預覽後改變，請重新預覽。")
        committed: list[tuple[Path, str, str, bool]] = []
        results = []
        try:
            for f in plan.files:
                path = Path(f.profile.path)
                backup = None
                if path.exists():
                    backup = backup_path(path)
                    shutil.copy2(path, backup)
                    prune_backups(path)
                # Write parked entries first so a failed config write never loses their values.
                for target, text in ((self._parked_path(f.profile), json.dumps(f.parked, ensure_ascii=False)), (path, f.next)):
                    existed, before = target.exists(), read_text(target)
                    atomic_write(target, text)
                    committed.append((target, before, text, existed))
                results.append({"agent": f.profile.name, "backup": str(backup) if backup else None})
            return results
        except Exception as e:
            failed = []
            for target, before, after, existed in reversed(committed):
                try:
                    if read_text(target) != after:
                        failed.append(str(target))
                    elif existed:
                        atomic_write(target, before)
                    else:
                        target.unlink()
                except Exception:
                    failed.append(str(target))
            tail = ("部分復原失敗，請用備份檢查：" + "、".join(failed)) if failed else "本次已寫入的檔案已復原。"
            raise RuntimeError(f"{e}；{tail}") from None
