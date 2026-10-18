"""Extensions: things an Agent loads that are not MCP servers — hooks and plugins (e.g. rtk).

A library extension lists, per client kind, how it plugs in:

- claude-code / codex: a hook entry {event, matcher, command} in a JSON hooks file
  (Claude Code: <claude dir>/settings.json, Codex: <codex dir>/hooks.json — both use
  {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}]}}).
- opencode: a plugin file {plugin: "rtk.ts", source: "<template path>"} copied into <opencode dir>/plugins/.

The Claude Desktop / Cowork MCP profile has no verified extension adapter here.

Like MCP changes, everything is planned first (preview), re-checked, backed up and then written.
An extension counts as installed for an Agent when its hook command / plugin file is present —
also when someone else (e.g. `rtk init`) put it there.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
import base64
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from agentdock.agents import Profile, strip_jsonc, _parse, _dump, _servers, _section
from agentdock import codebase_integration as cbm
from agentdock.files import atomic_write, backup_path, prune_backups, read_text
from agentdock.library import Library, bundled_rtk_plugin
from agentdock.paths import console_python

# One-click library entries ("＋ → rtk（範本）"). Same shape as mcp-library.json "extensions".
PRESETS: list[dict[str, Any]] = [{
    "key": "rtk", "type": "github",
    "description": "RTK（rtk-ai/rtk）：改寫 Agent 執行的 shell 指令，壓縮輸出以節省 token。需要在 PATH 上。",
    "repo": "rtk-ai/rtk", "asset": r"^rtk-x86_64-pc-windows-msvc\.zip$", "exe": "rtk.exe", "path": True,
    "hooks": {
        "claude-code": {"event": "PreToolUse", "matcher": "Bash", "command": "rtk hook claude"},
        "codex": {"event": "PreToolUse", "matcher": "Bash", "command": "rtk hook codex"},
        "opencode": {"plugin": "rtk.ts", "source": "{AGENTDOCK}/agentdock/assets/opencode/rtk.ts"},
    },
}]

PRESETS.append({
    'key': 'inbox-activity', 'type': 'command', 'command': '{PYTHON}', 'path': False,
    'description': 'Inbox 授權提醒與近期活動。僅通知，仍在原客戶端批准；兩分鐘無事件顯示未知。Claude Desktop 一般聊天尚無接入。',
    'hooks': {
        'codex': {'event': 'UserPromptSubmit', 'matcher': '*', 'command': '{INBOX_HOOK}',
                  'events': ['PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop', 'Interrupt', 'SessionEnd']},
        'claude-code': {'event': 'UserPromptSubmit', 'matcher': '*', 'command': '{INBOX_HOOK}',
                        'events': ['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'Notification', 'Stop', 'StopFailure', 'SessionEnd']},
        'grok-cli': {'event': 'UserPromptSubmit', 'matcher': '.*', 'command': '{INBOX_HOOK}',
                     'events': ['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'Notification', 'Stop', 'StopFailure', 'StopCancelled', 'SessionEnd']},
        'opencode': {'plugin': 'inbox-activity.js', 'source': '{AGENTDOCK}/agentdock/assets/opencode/inbox-activity.js'},
    },
})

UNSUPPORTED = {"claude-desktop": "Claude Desktop 一般聊天／Cowork 尚無已驗證的擴充接入"}
PRESETS.append(cbm.PRESET)


def agent_dir(profile: Profile) -> Path | None:
    """Folder the client reads hooks/plugins from, derived from the registered config file."""
    cfg = Path(profile.path)
    if profile.kind == "claude-code":
        env = os.environ.get("CLAUDE_CONFIG_DIR")
        if env:
            return Path(env)
        # ~/.claude.json -> ~/.claude ; a config already inside a .claude folder -> that folder
        return cfg.parent if cfg.parent.name == ".claude" else cfg.parent / ".claude"
    if profile.kind in ("codex", "opencode", "grok-cli"):
        return cfg.parent
    return None


def hook_file(profile: Profile) -> Path | None:
    base = agent_dir(profile)
    if base is None:
        return None
    if profile.kind == 'grok-cli':
        return base / 'hooks' / 'agentdock.json'
    return base / ("settings.json" if profile.kind == "claude-code" else "hooks.json")


@dataclass
class Target:
    """Where one extension lands for one Agent."""
    kind: str               # "hook" or "plugin"
    path: Path
    spec: dict[str, Any]


def target(library: Library, entry: dict[str, Any], profile: Profile) -> Target | None:
    spec = entry.get("hooks", {}).get(profile.kind)
    if not spec or profile.kind in UNSUPPORTED:
        return None
    spec = copy.deepcopy(spec)
    if spec.get('command') == '{INBOX_HOOK}':
        args = [
            console_python(), str(library.root / 'agentdock' / 'activity_hook.py'),
            '--client', profile.kind, '--identity', 'agentdock-' + profile.id,
            '--data', str(library.data)]
        script = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args)
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        spec['command'] = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded
        spec['timeout'] = 3
    spec['_identity'] = 'agentdock-' + profile.id
    if profile.kind == "opencode":
        base = agent_dir(profile)
        return Target("plugin", base / "plugins" / spec["plugin"], spec) if base else None
    path = hook_file(profile)
    return Target("hook", path, spec) if path else None


# --------------------------------------------------------------------------- hook JSON
def _load_hooks(text: str) -> dict[str, Any]:
    clean, _ = strip_jsonc(text or "{}")
    data = json.loads(clean or "{}")
    if not isinstance(data, dict):
        raise ValueError("hooks 設定格式必須是物件。")
    return data


def _commands(group: Any) -> list[str]:
    if not isinstance(group, dict):
        return []
    return [str(h.get("command", "")) for h in group.get("hooks", []) if isinstance(h, dict)]


def _matches(command: str, wanted: str) -> bool:
    """`rtk hook claude` also matches a hook that calls rtk by full path (C:\\...\\rtk.exe hook claude)."""
    command, wanted = command.strip().strip('"'), wanted.strip()
    if command == wanted:
        return True
    head, _, tail = wanted.partition(" ")
    if not tail:
        return False
    exe, _, rest = command.replace('"', "").rpartition(" " + tail)
    return rest == "" and bool(exe) and PureWindowsPath(exe.strip()).stem.lower() == head.lower()


def hook_present(data: dict[str, Any], spec: dict[str, Any]) -> bool:
    if spec.get('events'):
        return all(hook_present(data, s) for s in hook_specs(spec))
    groups = (data.get("hooks") or {}).get(spec["event"]) or []
    return any(_matches(c, spec["command"]) for g in groups for c in _commands(g))


def hook_specs(spec):
    return [{**{k: v for k, v in spec.items() if k != 'events'}, 'event': event}
            for event in [spec['event'], *spec.get('events', [])]]


def add_hook(data: dict[str, Any], spec: dict[str, Any]) -> None:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks 區塊格式必須是物件。")
    groups = hooks.setdefault(spec["event"], [])
    if not isinstance(groups, list):
        raise ValueError(f"hooks.{spec['event']} 必須是陣列。")
    hook = {"type": "command", "command": spec["command"]}
    if spec.get("timeout"):
        hook["timeout"] = spec["timeout"]
    groups.append({"matcher": spec["matcher"], "hooks": [hook]})


def remove_hook(data: dict[str, Any], spec: dict[str, Any]) -> int:
    hooks = data.get("hooks") or {}
    groups = hooks.get(spec["event"]) or []
    removed, kept = 0, []
    for g in groups:
        if not isinstance(g, dict):
            kept.append(g)
            continue
        inner = [h for h in g.get("hooks", []) if not (isinstance(h, dict) and _matches(str(h.get("command", "")), spec["command"]))]
        removed += len(g.get("hooks", [])) - len(inner)
        if inner:
            kept.append({**g, "hooks": inner})
    if removed:
        if kept:
            hooks[spec["event"]] = kept
        else:
            hooks.pop(spec["event"], None)
        if not hooks:
            data.pop("hooks", None)
    return removed


# --------------------------------------------------------------------------- plan
@dataclass
class FileChange:
    path: Path
    original: str
    existed: bool
    next: str | None        # None = delete


@dataclass
class ExtPlan:
    id: str
    files: list[FileChange] = field(default_factory=list)
    summary: list[dict[str, Any]] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    guards: list[FileChange] = field(default_factory=list)
    dependencies: dict[str, dict[str, Any]] = field(default_factory=dict)


class ExtensionManager:
    def __init__(self, library: Library):
        self.library = library

    def status(self, entry: dict[str, Any], profile: Profile) -> bool | None:
        """True installed, False not installed, None not applicable to this Agent."""
        t = target(self.library, entry, profile)
        if t is None:
            return None
        if t.kind == "plugin":
            return t.path.is_file()
        if cbm.managed(entry):
            try:
                return any(cbm.owned_groups(self._cbm_doc(path, profile)) for path in self._cbm_paths(profile))
            except ValueError:
                return False
        try:
            return hook_present(_load_hooks(read_text(t.path)), t.spec)
        except ValueError:
            return False

    def in_sync(self, entry: dict[str, Any], profile: Profile) -> bool:
        """Installed and identical to what AgentDock would write now (plugin text; hooks are matched by command)."""
        t = target(self.library, entry, profile)
        if t is None or not self.status(entry, profile):
            return False
        if cbm.managed(entry):
            try:
                if not cbm.mcp_matches(self.library, entry, profile, read_text(Path(profile.path))):
                    return False
                if t.kind == 'plugin':
                    return read_text(t.path) == self._plugin_text(entry, t.spec)
                paths = self._cbm_paths(profile)
                return (cbm.owned_groups(self._cbm_doc(paths[0], profile)) == cbm.desired_groups(self.library, entry, profile.kind)
                        and all(not cbm.owned_groups(self._cbm_doc(p, profile)) for p in paths[1:]))
            except ValueError:
                return False
        if t.kind == "plugin":
            try:
                return read_text(t.path) == self._plugin_text(entry, t.spec)
            except ValueError:
                return True  # no template to compare with
        return True

    def refresh_ops(self, entry: dict[str, Any], profiles: list[Profile]) -> list[dict[str, Any]]:
        """Re-install where the Agent has it but it is out of date (e.g. after an update)."""
        return [{"profile": p.id, "ext": entry["key"], "op": "install"} for p in profiles
                if self.status(entry, p) and not self.in_sync(entry, p)]

    def installed(self, profile: Profile) -> list[dict[str, Any]]:
        return [e for e in self.library.extensions() if self.status(e, profile)]

    def _plugin_text(self, entry: dict[str, Any], spec: dict[str, Any]) -> str:
        """Use the maintained RTK adapter; other plugins prefer their downloaded release."""
        if cbm.managed(entry):
            source = self.library.root / 'agentdock/assets/opencode/cbm-augment.ts'
            text = read_text(source)
            if not text:
                raise ValueError(f'找不到 Codebase Memory 外掛範本：{source}')
            return text.replace('__CBM_LAUNCH__', json.dumps(cbm.launch(self.library, entry), ensure_ascii=False))
        version = self.library.version_dir(entry)
        if (not bundled_rtk_plugin(entry, spec) and version and
                (version / spec["plugin"]).is_file() and self.library.managed_by_agentdock(entry)):
            text = read_text(version / spec["plugin"])
            if text:
                return text
        source = Path(self.library._expand(spec["source"], entry["key"]))
        text = read_text(source)
        if not text:
            raise ValueError(f"找不到 {entry['key']} 的 OpenCode plugin 範本：{source}")
        if entry['key'] == 'inbox-activity':
            text = text.replace('__INBOX_ENDPOINT__', json.dumps(str(self.library.data / 'endpoint.json')))
            text = text.replace('__INBOX_IDENTITY__', json.dumps(spec['_identity']))
        return text

    @staticmethod
    def _cbm_paths(profile):
        if profile.kind == 'codex':
            return [Path(profile.path), hook_file(profile)]
        return [hook_file(profile)]

    @staticmethod
    def _cbm_doc(path, profile):
        return _parse('codex', read_text(path)) if profile.kind == 'codex' and path == Path(profile.path) else _load_hooks(read_text(path))

    def prepare(self, ops: list[dict[str, Any]], profiles: list[Profile], *, base=None) -> ExtPlan:
        """ops: [{profile, ext, op}] with op in install / uninstall."""
        by_id = {p.id: p for p in profiles}
        plan = ExtPlan(id=str(uuid.uuid4()))
        docs: dict[Path, dict[str, Any]] = {}          # hook files edited by several ops
        originals: dict[Path, str] = {}
        plugin_next: dict[Path, str | None] = {}
        formats: dict[Path, str] = {}
        notes: dict[str, list[str]] = {}
        if base:
            plan = copy.deepcopy(base)
            originals.update({f.path: f.original for f in base.files})
            plugin_next.update({f.path: f.next for f in base.files})
            plan.files = []

        def document(path, kind):
            originals.setdefault(path, read_text(path))
            if path not in docs:
                text = plugin_next.pop(path, originals[path])
                docs[path] = _parse(kind, text or '')
                formats[path] = kind
            return docs[path]

        for o in ops:
            p = by_id.get(o["profile"])
            if p is None:
                raise ValueError("Agent 清單已變更，請重新整理。")
            entry = self.library.get_extension(o["ext"])
            if entry is None:
                raise ValueError(f"擴充庫已沒有 {o['ext']}，請重新整理。")
            if o["op"] not in ("install", "uninstall"):
                raise ValueError(f"未知的操作：{o['op']}")
            t = target(self.library, entry, p)
            if t is None:
                raise ValueError(f"{p.name} 不支援 {entry['key']}。")
            install = o["op"] == "install"
            originals.setdefault(t.path, read_text(t.path))
            if cbm.managed(entry):
                paths = []
                if install:
                    plan.dependencies[entry['key']] = cbm.launch(self.library, entry)
                    config_path = Path(p.path)
                    config = document(config_path, p.kind)
                    current = _servers(p.kind, config).get(entry['mcp'])
                    if not current:
                        raise ValueError(f'{p.name} 尚未加入 {entry["mcp"]}，請先將 MCP 加入待套用清單。')
                    source = self.library.get(entry['mcp'])
                    if self.library.in_sync(source, p.kind, p, current) is not True:
                        rendered = self.library.render(source, p.kind, p)
                        if 'enabled' in current:
                            rendered['enabled'] = current['enabled']
                        config[_section(p.kind)][entry['mcp']] = rendered
                        notes.setdefault(p.id, []).append(f'{entry["mcp"]} → 同步 MCP 與擴充使用的執行檔及環境')
                    paths.append(config_path)
                if t.kind == 'plugin':
                    plugin_next[t.path] = self._plugin_text(entry, t.spec) if install else None
                    paths.append(t.path)
                else:
                    for index, path in enumerate(self._cbm_paths(p)):
                        kind = 'codex' if p.kind == 'codex' and path == Path(p.path) else 'claude-code'
                        data = document(path, kind)
                        desired = cbm.desired_groups(self.library, entry, p.kind) if install and index == 0 else {}
                        if cbm.owned_groups(data) != desired:
                            cbm.reconcile(data, desired)
                        paths.append(path)
                notes.setdefault(p.id, []).append(f'{entry["key"]} → {"由 AgentDock 接管／更新" if install else "移除"}接入（保留其他 hooks、技能與 AGENTS.md）')
                if install and p.kind == 'codex':
                    notes[p.id].append('Codex 重啟後須在 /hooks 檢視並信任更新的 hooks。')
                notes.setdefault(f'path:{p.id}', []).extend(str(path) for path in paths)
                if p.id not in plan.profiles:
                    plan.profiles.append(p.id)
                continue
            if t.kind == "plugin":
                plugin_next[t.path] = self._plugin_text(entry, t.spec) if install else None
                change = f"{entry['key']} → {'加入' if install else '移除'} plugin {t.path.name}"
            else:
                if t.path not in docs:
                    try:
                        document(t.path, 'claude-code')
                    except ValueError as e:
                        raise ValueError(f"{t.path} 語法錯誤，請先修正：{e}") from None
                data = docs[t.path]
                for spec in hook_specs(t.spec):
                    if install and not hook_present(data, spec):
                        add_hook(data, spec)
                    elif not install:
                        remove_hook(data, spec)
                change = f"{entry['key']} → {'加入' if install else '移除'} {', '.join(s['event'] for s in hook_specs(t.spec))} hook（{t.spec['command']}）"
            notes.setdefault(p.id, []).append(change)
            if p.id not in plan.profiles:
                plan.profiles.append(p.id)
            notes.setdefault(f"path:{p.id}", []).append(str(t.path))
        for path, data in docs.items():
            kind = formats[path]
            if data != _parse(kind, originals[path]):
                text, _ = _dump(kind, originals[path], data)
                plan.files.append(FileChange(path, originals[path], path.exists(), text))
            else:
                plan.guards.append(FileChange(path, originals[path], path.exists(), originals[path]))
        for path, text in plugin_next.items():
            if text != (originals[path] if path.exists() else None):
                plan.files.append(FileChange(path, originals[path], path.exists(), text))
        for pid in plan.profiles:
            if pid not in notes:
                continue
            paths = sorted(set(notes[f"path:{pid}"]))
            reformats = any(Path(x) in docs and formats[Path(x)] != 'codex' and strip_jsonc(originals[Path(x)] or "{}")[1] for x in paths)
            plan.summary.append({"agent": by_id[pid].name, "path": "、".join(paths), "changes": notes[pid], "reformats": reformats})
        return plan

    def prepare_all(self, manager, mcp_ops, ext_ops):
        """One transaction for MCP and extensions, including shared Codex config.toml."""
        mcp_plan = manager.prepare(mcp_ops)
        base = ExtPlan(id=mcp_plan.id, summary=mcp_plan.summary)
        base.guards.append(FileChange(manager.file, read_text(manager.file), manager.file.exists(), None))
        for f in mcp_plan.files:
            path = Path(f.profile.path)
            base.files.append(FileChange(path, f.original, path.exists(), f.next))
            parked = manager._parked_path(f.profile)
            value = json.dumps(f.parked, ensure_ascii=False)
            if value != f.parked_original:
                base.files.append(FileChange(parked, f.parked_original, parked.exists(), value))
            else:
                base.guards.append(FileChange(parked, f.parked_original, parked.exists(), value))
            base.profiles.append(f.profile.id)
        plan = self.prepare(ext_ops, manager.list(), base=base)
        plan.summary = merge_summary(plan.summary)
        return plan

    def apply(self, plan: ExtPlan) -> list[dict[str, Any]]:
        for key, expected in plan.dependencies.items():
            entry = self.library.get_extension(key)
            if not entry or cbm.launch(self.library, entry) != expected:
                raise ValueError('MCP 執行檔在預覽後改變，請重新預覽。')
        for f in plan.files + plan.guards:
            if read_text(f.path) != f.original or f.path.exists() != f.existed:
                raise ValueError("設定在預覽後改變，請重新預覽。")
        done: list[FileChange] = []
        results = []
        try:
            for f in plan.files:
                backup = None
                if f.existed:
                    backup = backup_path(f.path)
                    shutil.copy2(f.path, backup)
                    prune_backups(f.path)
                if f.next is None:
                    f.path.unlink(missing_ok=True)
                else:
                    atomic_write(f.path, f.next)
                done.append(f)
                results.append({"path": str(f.path), "backup": str(backup) if backup else None})
            return results
        except Exception as e:
            failed = []
            for f in reversed(done):
                try:
                    if read_text(f.path) != (f.next or '') or f.path.exists() != (f.next is not None):
                        failed.append(str(f.path))
                    elif f.existed:
                        atomic_write(f.path, f.original)
                    else:
                        f.path.unlink(missing_ok=True)
                except Exception:
                    failed.append(str(f.path))
            tail = ("部分復原失敗，請用備份檢查：" + "、".join(failed)) if failed else "本次已寫入的檔案已復原。"
            raise RuntimeError(f"{e}；{tail}") from None


def merge_summary(*summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One preview card per Agent, combining MCP and extension changes."""
    out: list[dict[str, Any]] = []
    for summary in summaries:
        for item in summary:
            same = next((o for o in out if o["agent"] == item["agent"]), None)
            if same is None:
                out.append(copy.deepcopy(item))
            else:
                if item["path"] not in same["path"]:
                    same["path"] += "、" + item["path"]
                same["changes"] += item["changes"]
                same["reformats"] = same["reformats"] or item["reformats"]
    return out
