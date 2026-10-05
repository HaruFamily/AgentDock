"""MCP library: define each MCP once, then add it to any Agent.

Three layers, deliberately separate:
- definitions  -> <repo>/mcp-library.json   (version controlled, no secrets, no absolute paths)
- downloads    -> data/mcp/<key>/           (local, re-downloadable)
- secrets      -> data/secrets.json         (local, never committed): API keys and also absolute local paths
                                             (e.g. a service-account file), which differ per computer

Placeholders usable in command / args / env: {AGENTDOCK} {DATA} {HOME} {BIN} (= data/mcp/<key>).
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from agentdock.files import atomic_json, load_json
from agentdock.paths import console_python, local_uvx

QAI_KEY = "agentdock-qa"
TYPES = {
    "builtin": "內建",
    "remote": "遠端網址",
    "uvx": "uvx 套件",
    "npx": "npx 套件",
    "github": "GitHub Release",
    "download": "下載網址",
    "command": "自訂指令",
}
SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.I)
LOCAL_PATH = re.compile(r"^([A-Za-z]:[\\/]|\\\\|/)")   # absolute path on this computer (not a {PLACEHOLDER})


def split_local(env: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """env -> (values that may go into Git, values that must stay on this computer)."""
    local = {k: str(v) for k, v in env.items() if isinstance(v, str) and LOCAL_PATH.match(v)}
    return {k: v for k, v in env.items() if k not in local}, local
DEFAULT_ASSET = r"windows.*(amd64|x86_64|x64)"
EXT_TYPES = ("github", "download", "command")
EXT_HOOK_KINDS = ("claude-code", "codex", "opencode")


def _https(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or not parts.hostname:
        raise ValueError("網址必須是 https:// 開頭，且不含帳號密碼。")


class Library:
    def __init__(self, root: Path, data: Path):
        self.root = root
        self.data = data
        self.file = root / "mcp-library.json"
        self.secrets_file = data / "secrets.json"
        self.bin_root = data / "mcp"

    # ------------------------------------------------------------------ storage
    def _doc(self) -> dict[str, Any]:
        return load_json(self.file, {"schema": 1, "servers": []})

    def _stored(self) -> list[dict[str, Any]]:
        return self._doc().get("servers", [])

    def _write(self, servers: list[dict[str, Any]] | None = None, extensions: list[dict[str, Any]] | None = None) -> None:
        doc = self._doc()
        out: dict[str, Any] = {"schema": 1, "servers": doc.get("servers", []) if servers is None else servers}
        ext = doc.get("extensions", []) if extensions is None else extensions
        if ext:
            out["extensions"] = ext
        atomic_json(self.file, out)

    @staticmethod
    def builtin() -> dict[str, Any]:
        return {"key": QAI_KEY, "type": "builtin",
                "description": "AgentDock 問答（QAI）：讓 Agent 用 ask_user 在小球面板問你問題。", "timeout_sec": 1860}

    def entries(self) -> list[dict[str, Any]]:
        return [self.builtin()] + copy.deepcopy(self._stored())

    def get(self, key: str) -> dict[str, Any] | None:
        return next((e for e in self.entries() if e["key"] == key), None)

    def save(self, entry: dict[str, Any], secrets: dict[str, str] | None = None, replace: str | None = None) -> dict[str, Any]:
        """Add (replace=None) or edit (replace=old key) a definition. Secret values go to data/secrets.json."""
        entry = self.validate(entry)
        if entry.get("env"):   # an absolute path typed into 環境變數 is per computer: keep it out of Git
            entry["env"], local = split_local(entry["env"])
            if not entry["env"]:
                entry.pop("env")
            if local:
                entry["secrets"] = sorted(set(entry.get("secrets", [])) | set(local))
                secrets = {**(secrets or {}), **local}
        servers = self._stored()
        if replace is None and (self.get(entry["key"]) or self.get_extension(entry["key"])):
            raise ValueError(f"MCP 庫或擴充庫已有 {entry['key']}。")
        if replace is not None:
            if replace == QAI_KEY:
                raise ValueError("內建項目不能修改。")
            if entry["key"] != replace and (self.get(entry["key"]) or self.get_extension(entry["key"])):
                raise ValueError(f"MCP 庫或擴充庫已有 {entry['key']}。")
            servers = [entry if s["key"] == replace else s for s in servers]
        else:
            servers.append(entry)
        if secrets:
            self.set_secrets({k: v for k, v in secrets.items() if v})
        self._write(servers)
        return entry

    def upgrade_commands(self) -> int:
        """Re-detect stored 自訂指令 entries that are really uvx/npx (e.g. adopted before options were understood)."""
        servers, changed = self._stored(), 0
        for i, e in enumerate(servers):
            if e.get("type") != "command":
                continue
            config = {"command": e["command"], "args": e.get("args", []), "env": e.get("env", {})}
            try:
                better, _ = self.from_config(e["key"], config)
            except ValueError:
                continue
            if better["type"] != "command":
                for keep in ("secrets", "timeout_sec", "description", "name"):
                    if keep in e:
                        better[keep] = e[keep]
                servers[i] = better
                changed += 1
        if changed:
            self._write(servers)
        return changed

    def localize_paths(self) -> int:
        """Older libraries kept absolute local paths in env (and so in Git): move them to data/secrets.json."""
        servers, changed, moved = self._stored(), 0, {}
        for e in servers:
            plain, local = split_local(e.get("env", {}))
            if not local:
                continue
            if plain:
                e["env"] = plain
            else:
                e.pop("env", None)
            e["secrets"] = sorted(set(e.get("secrets", [])) | set(local))
            moved.update({k: v for k, v in local.items() if not self.secrets().get(k)})
            changed += 1
        if changed:
            if moved:
                self.set_secrets(moved)
            self._write(servers)
        return changed

    def recover_secrets(self, entry: dict[str, Any], config: Any) -> list[str]:
        """Fill secrets this computer lacks from an Agent config that already has them (e.g. right after a pull
        moved a path out of mcp-library.json, or on a second computer). Never overwrites a stored value."""
        if not isinstance(config, dict):
            return []
        env = config.get("env") or config.get("environment") or {}
        have = self.secrets()
        found = {s: str(env[s]) for s in entry.get("secrets", []) if not have.get(s) and env.get(s)}
        if found:
            self.set_secrets(found)
        return sorted(found)

    def remove(self, key: str) -> None:
        if key == QAI_KEY:
            raise ValueError("內建項目不能刪除。")
        self._write([s for s in self._stored() if s["key"] != key])

    def validate(self, raw: dict[str, Any]) -> dict[str, Any]:
        e = {k: v for k, v in raw.items() if v not in (None, "", [], {})}
        key = str(e.get("key", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", key):
            raise ValueError("名稱（設定檔中的鍵）僅可使用英文、數字、點、底線及連字號。")
        if key == QAI_KEY:
            raise ValueError(f"{QAI_KEY} 是內建名稱。")
        e["key"] = key
        t = e.get("type")
        if t not in TYPES or t == "builtin":
            raise ValueError("請選擇 MCP 類型。")
        need = {"remote": ["url"], "uvx": ["package"], "npx": ["package"], "github": ["repo"], "download": ["download_url"],
                "command": ["command"]}[t]
        for field in need:
            if not str(e.get(field, "")).strip():
                raise ValueError(f"缺少欄位：{field}")
        if t == "remote":
            _https(e["url"])
        if t == "download":
            _https(e["download_url"])
        if t == "github" and not re.fullmatch(r"[\w.-]+/[\w.-]+", e["repo"]):
            raise ValueError("GitHub 專案請填 owner/repo，例如 DeusData/codebase-memory-mcp。")
        for field in ("args", "secrets", "local", "options"):
            if field in e and not (isinstance(e[field], list) and all(isinstance(x, str) for x in e[field])):
                raise ValueError(f"{field} 必須是文字清單。")
        if "env" in e:
            if not isinstance(e["env"], dict):
                raise ValueError("env 必須是物件。")
            leaked = [k for k in e["env"] if SECRET_HINT.search(k)]
            if leaked:
                raise ValueError(f"{'、'.join(leaked)} 看起來是祕密，請放在「祕密」欄位，避免寫進 Git。")
        if "timeout_sec" in e:
            e["timeout_sec"] = int(e["timeout_sec"])
        return e

    # ------------------------------------------------------------------ extensions (hooks / plugins)
    def extensions(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._doc().get("extensions", []))

    def get_extension(self, key: str) -> dict[str, Any] | None:
        return next((e for e in self.extensions() if e["key"] == key), None)

    def save_extension(self, entry: dict[str, Any], replace: str | None = None) -> dict[str, Any]:
        entry = self.validate_extension(entry)
        items = self.extensions()
        taken = lambda k: self.get(k) or self.get_extension(k)
        if replace is None:
            if taken(entry["key"]):
                raise ValueError(f"MCP 庫或擴充庫已有 {entry['key']}。")
            items.append(entry)
        else:
            if entry["key"] != replace and taken(entry["key"]):
                raise ValueError(f"MCP 庫或擴充庫已有 {entry['key']}。")
            items = [entry if e["key"] == replace else e for e in items]
        self._write(extensions=items)
        return entry

    def remove_extension(self, key: str) -> None:
        self._write(extensions=[e for e in self.extensions() if e["key"] != key])

    def validate_extension(self, raw: dict[str, Any]) -> dict[str, Any]:
        e = {k: v for k, v in raw.items() if v not in (None, "", [], {})}
        key = str(e.get("key", "")).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", key) or key == QAI_KEY:
            raise ValueError("名稱僅可使用英文、數字、點、底線及連字號。")
        e["key"] = key
        t = e.get("type")
        if t not in EXT_TYPES:
            raise ValueError("擴充的類型必須是 " + "、".join(EXT_TYPES) + "。")
        need = {"github": "repo", "download": "download_url", "command": "command"}[t]
        if not str(e.get(need, "")).strip():
            raise ValueError(f"缺少欄位：{need}")
        if t == "download":
            _https(e["download_url"])
        if t == "github" and not re.fullmatch(r"[\w.-]+/[\w.-]+", e["repo"]):
            raise ValueError("GitHub 專案請填 owner/repo，例如 rtk-ai/rtk。")
        hooks = e.get("hooks")
        if not isinstance(hooks, dict) or not hooks:
            raise ValueError("hooks 必須是物件，至少列出一種 Agent（claude-code、codex、opencode）。")
        for kind, spec in hooks.items():
            if kind not in EXT_HOOK_KINDS:
                raise ValueError(f"擴充不支援 {kind}；可用：{'、'.join(EXT_HOOK_KINDS)}。")
            if not isinstance(spec, dict):
                raise ValueError(f"hooks.{kind} 必須是物件。")
            if kind == "opencode":
                if not (str(spec.get("plugin", "")).strip() and str(spec.get("source", "")).strip()):
                    raise ValueError("hooks.opencode 需要 plugin（檔名）與 source（範本路徑）。")
                if not re.fullmatch(r"[\w.-]+\.(ts|js)", spec["plugin"]):
                    raise ValueError("OpenCode plugin 檔名須為 .ts 或 .js，且不含路徑。")
                if spec.get("source_url"):
                    _https(spec["source_url"].replace("{TAG}", "x"))
            elif not all(str(spec.get(f, "")).strip() for f in ("event", "matcher", "command")):
                raise ValueError(f"hooks.{kind} 需要 event、matcher、command。")
        if "path" in e:
            e["path"] = bool(e["path"])
        return e

    def ext_command(self, entry: dict[str, Any]) -> str:
        """The name hooks call the tool by (rtk.exe -> rtk)."""
        if entry["type"] == "command":
            return Path(self._expand(entry["command"], entry["key"])).stem if entry["command"].lower().endswith(".exe") \
                else entry["command"]
        return Path(entry.get("exe") or entry["key"]).stem

    def ext_found(self, entry: dict[str, Any]) -> str | None:
        """Where agents would find the tool on PATH right now (None when they would not)."""
        from agentdock import userpath
        return userpath.which(self.ext_command(entry))

    def ext_problems(self, entry: dict[str, Any]) -> list[str]:
        if not entry.get("path", True) or self.ext_found(entry):
            return []
        if entry["type"] == "command":
            return [f"找不到 {entry['command']}"]
        if not self.installed_exe(entry):
            return ["需下載"]
        return ["未加入 PATH"]

    def publish(self, entry: dict[str, Any], add_path: bool = True) -> Path:
        """Copy the installed exe into data/bin and (optionally) put data/bin on the user PATH."""
        from agentdock import userpath
        exe = self.installed_exe(entry)
        if not exe:
            raise ValueError(f"{entry['key']} 尚未下載。")
        folder = userpath.bin_dir(self.data)
        linked = userpath.link(exe, folder)
        if add_path:
            userpath.add_to_user_path(folder)
        return linked

    def refresh_link(self, entry: dict[str, Any]) -> bool:
        """After an update: refresh data/bin's copy if this tool already lives there."""
        from agentdock import userpath
        exe, folder = self.installed_exe(entry), userpath.bin_dir(self.data)
        if not exe or not (folder / exe.name).exists():
            return False
        userpath.link(exe, folder)
        return True

    def deploy_target(self, entry: dict[str, Any]) -> Path | None:
        """The file agents run that an update must replace: a copy installed some other way (e.g. ~/.local/bin/rtk.exe).
        None when agents already use AgentDock's own copy (data/bin, refreshed by refresh_link) or find nothing."""
        found = self.ext_found(entry) if "hooks" in entry else None
        if not found:
            return None
        mine = self.installed_exe(entry)
        if (mine and _same_file(found, mine)) or Path(found).parent == self.data / "bin":
            return None
        return Path(found)

    def deploy(self, entry: dict[str, Any]) -> Path | None:
        """Put the downloaded version where agents find it. Returns the replaced/refreshed file, or None."""
        from agentdock import userpath
        exe = self.installed_exe(entry)
        if not exe:
            raise ValueError(f"{entry['key']} 尚未下載。")
        target = self.deploy_target(entry)
        if target is None:
            return userpath.bin_dir(self.data) / exe.name if self.refresh_link(entry) else None
        _replace_running(exe, target)
        state = self.install_state(entry)
        atomic_json(self.bin_dir(entry["key"]) / ".installed.json", {**state, "deployed": str(target)})
        return target

    def version_dir(self, entry: dict[str, Any]) -> Path | None:
        state = self.install_state(entry)
        if not state.get("exe") or state.get("path"):
            return None
        return self.bin_dir(entry["key"]) / Path(state["exe"]).parts[0]

    # ------------------------------------------------------------------ secrets
    def secrets(self) -> dict[str, str]:
        return load_json(self.secrets_file, {})

    def set_secrets(self, values: dict[str, str]) -> None:
        current = self.secrets()
        current.update(values)
        atomic_json(self.secrets_file, current)

    # ------------------------------------------------------------------ install state
    def bin_dir(self, key: str) -> Path:
        return self.bin_root / key

    def install_state(self, entry: dict[str, Any]) -> dict[str, Any]:
        return load_json(self.bin_dir(entry["key"]) / ".installed.json", {})

    def installed_exe(self, entry: dict[str, Any]) -> Path | None:
        state = self.install_state(entry)
        if state.get("path"):  # pointing at an existing install elsewhere on this computer
            path = Path(state["path"])
        elif state.get("exe"):
            path = self.bin_dir(entry["key"]) / state["exe"]
        else:
            return None
        return path if path.is_file() else None

    def local_candidate(self, entry: dict[str, Any]) -> Path | None:
        """An already-installed copy at one of the entry's `local` paths (e.g. from the vendor installer)."""
        for raw in entry.get("local", []):
            path = Path(self._expand(raw, entry["key"]))
            if path.is_file():
                return path
        return None

    def use_local(self, entry: dict[str, Any], path: Path) -> Path:
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError(f"找不到檔案：{path}")
        target = self.bin_dir(entry["key"])
        target.mkdir(parents=True, exist_ok=True)
        atomic_json(target / ".installed.json", {"path": str(path), "tag": "本機"})
        return path

    def problems(self, entry: dict[str, Any]) -> list[str]:
        out = []
        if entry["type"] in ("github", "download") and not self.installed_exe(entry):
            out.append("需下載")
        missing = [s for s in entry.get("secrets", []) if not self.secrets().get(s)]
        if missing:
            out.append("缺少祕密：" + "、".join(missing))
        return out

    def _release(self, entry: dict[str, Any]) -> dict[str, Any]:
        api = f"https://api.github.com/repos/{entry['repo']}/releases/" + (
            "latest" if entry.get("tag", "latest") == "latest" else f"tags/{entry['tag']}")
        return json.loads(_get(api))

    def check_update(self, entry: dict[str, Any], force: bool = False, max_age: float = 86400) -> str | None:
        """Latest GitHub tag when newer than the installed one; cached once a day per MCP."""
        import time
        if entry["type"] != "github" or entry.get("tag", "latest") != "latest":
            return None
        current = self.current_version(entry)
        if not current:
            return None  # not installed yet, or its version cannot be read
        cache_file = self.data / "update-check.json"
        cache = load_json(cache_file, {})
        hit = cache.get(entry["key"])
        if force or not hit or time.time() - hit.get("at", 0) > max_age:
            latest = self._release(entry).get("tag_name", "")
            hit = {"at": time.time(), "latest": latest}
            cache[entry["key"]] = hit
            atomic_json(cache_file, cache)
        latest = hit.get("latest", "")
        return latest if latest and _newer(latest, current) else None

    def cached_update(self, entry: dict[str, Any]) -> str | None:
        hit = load_json(self.data / "update-check.json", {}).get(entry["key"], {})
        latest, current = hit.get("latest", ""), self.current_version(entry)
        return latest if latest and current and _newer(latest, current) else None

    def current_version(self, entry: dict[str, Any]) -> str | None:
        """Version actually in use: for an extension the copy agents find on PATH, otherwise AgentDock's download."""
        if "hooks" in entry:
            found = self.ext_found(entry)
            if found:
                return self.tool_version(found) or (self.install_state(entry).get("tag") if self.managed_by_agentdock(entry) else None)
        state = self.install_state(entry)
        if state.get("path") or not self.installed_exe(entry):
            return None
        return state.get("tag") or None

    def managed_by_agentdock(self, entry: dict[str, Any]) -> bool:
        """False when agents use a copy installed some other way (e.g. rtk's own installer in ~/.local/bin)."""
        found = self.ext_found(entry) if "hooks" in entry else None
        mine = self.installed_exe(entry)
        if not found:
            return bool(mine)
        deployed = self.install_state(entry).get("deployed")
        return bool(mine and (_same_file(found, mine) or Path(found).parent == self.data / "bin"
                              or (deployed and _same_file(found, deployed))))

    def tool_version(self, path: str | Path) -> str | None:
        """`<tool> --version` -> "0.51.0". Cached per file (path + size + mtime), so it runs once per update."""
        import subprocess
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        sig = f"{path}|{st.st_size}|{st.st_mtime_ns}"
        cache_file = self.data / "version-cache.json"
        cache = load_json(cache_file, {})
        if sig in cache:
            return cache[sig] or None
        version = ""
        try:
            out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            m = re.search(r"\d+\.\d+(?:\.\d+)?", out.stdout + " " + out.stderr)
            version = m.group(0) if m else ""
        except (OSError, subprocess.SubprocessError):
            pass
        cache = {k: v for k, v in cache.items() if not k.startswith(f"{path}|")}
        cache[sig] = version
        atomic_json(cache_file, cache)
        return version or None

    def cleanup(self, entry: dict[str, Any]) -> None:
        """Delete old versions that are no longer current. Files still running (locked) are skipped."""
        target = self.bin_dir(entry["key"])
        state = self.install_state(entry)
        if not target.is_dir() or not state.get("exe"):
            return
        keep = Path(state["exe"]).parts[0] if len(Path(state["exe"]).parts) > 1 else None
        for child in target.iterdir():
            if child.name == ".installed.json" or (keep and child.name == keep) or (not keep):
                continue
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            except OSError:
                pass

    def install(self, entry: dict[str, Any], progress: Callable[[str], None] = lambda _m: None) -> Path:
        """Download (and unpack) a github/download entry into data/mcp/<key>/<version>/.

        Each version gets its own folder, so a version that Codex/Claude are still running
        (locked on Windows) is never overwritten; agents switch over on the next apply."""
        if entry["type"] == "github":
            progress("查詢最新版本…")
            release = self._release(entry)
            pattern = re.compile(entry.get("asset") or DEFAULT_ASSET, re.I)
            # Only real payloads; among several matches prefer the plainest (shortest) name,
            # e.g. "x-windows-amd64.zip" over "x-ui-windows-amd64.zip".
            assets = sorted((a for a in release.get("assets", []) if pattern.search(a["name"])
                             and a["name"].lower().endswith((".zip", ".tar.gz", ".tgz", ".exe"))),
                            key=lambda a: (len(a["name"]), a["name"]))
            if not assets:
                raise ValueError(f"{release.get('tag_name')} 找不到符合 {pattern.pattern} 的檔案。")
            url, name, tag = assets[0]["browser_download_url"], assets[0]["name"], release.get("tag_name", "")
        else:
            url = entry["download_url"]
            name, tag = url.rsplit("/", 1)[-1] or "download", ""
        progress(f"下載 {name}…")
        data = _download(url, progress)
        import time
        target = self.bin_dir(entry["key"])
        target.mkdir(parents=True, exist_ok=True)
        version = re.sub(r"[^\w.-]", "_", tag) or time.strftime("%Y%m%d-%H%M%S")
        temp = target / f".{version}.partial"
        shutil.rmtree(temp, ignore_errors=True)
        temp.mkdir(parents=True)
        progress("解壓縮…")
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                _safe_extract_zip(z, temp)
        elif name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
                t.extractall(temp, filter="data")
        else:
            (temp / name).write_bytes(data)
        for spec in (entry.get("hooks") or {}).values():  # e.g. rtk's OpenCode plugin, matching this release
            if spec.get("source_url") and spec.get("plugin") and tag:
                try:
                    (temp / spec["plugin"]).write_bytes(_get(spec["source_url"].replace("{TAG}", tag)))
                except Exception:
                    pass  # the bundled template is used instead
        wanted = entry.get("exe")
        candidates = [p for p in temp.rglob("*") if p.is_file() and (p.name == wanted if wanted else p.suffix.lower() == ".exe")]
        if not candidates:
            shutil.rmtree(temp, ignore_errors=True)
            raise ValueError(f"下載內容中找不到執行檔 {wanted or '*.exe'}。")
        exe = candidates[0]
        if sys.platform != "win32":
            exe.chmod(exe.stat().st_mode | 0o111)
        final = target / version
        if final.exists():
            try:
                shutil.rmtree(final)
            except OSError:  # this version is running: keep it and use a fresh folder
                final = target / f"{version}-{time.strftime('%H%M%S')}"
        temp.rename(final)
        rel = exe.relative_to(temp)
        atomic_json(target / ".installed.json", {"exe": str(Path(final.name) / rel), "tag": tag, "asset": name, "url": url})
        progress(f"完成 {tag}".strip())
        return final / rel

    # ------------------------------------------------------------------ rendering
    def _expand(self, text: str, key: str) -> str:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return (text.replace("{AGENTDOCK}", str(self.root)).replace("{LOCALAPPDATA}", local).replace("{DATA}", str(self.data))
                .replace("{HOME}", str(Path.home())).replace("{BIN}", str(self.bin_dir(key))))

    def spec(self, entry: dict[str, Any], profile: Any) -> dict[str, Any]:
        """Client-neutral launch spec: {url} or {command, args, env}."""
        key, t = entry["key"], entry["type"]
        if t == "builtin":
            return {"command": console_python(), "args": ["-m", "agentdock.mcp_server"],
                    "env": {"AGENTDOCK_SOURCE": profile.name, "AGENTDOCK_CLIENT_ID": f"agentdock-{profile.id}",
                            "AGENTDOCK_DATA_DIR": str(self.data), "PYTHONPATH": str(self.root), "PYTHONIOENCODING": "utf-8"}}
        if t == "remote":
            return {"url": entry["url"]}
        args = [self._expand(a, key) for a in entry.get("args", [])]
        options = [self._expand(o, key) for o in entry.get("options", [])]
        if t == "uvx":
            command, args = local_uvx() or "uvx", [*options, entry["package"], *args]
        elif t == "npx":
            npx = ["npx", "-y", *options, entry["package"], *args]
            command, args = ("cmd", ["/c", *npx]) if os.name == "nt" else (npx[0], npx[1:])
        elif t in ("github", "download"):
            exe = self.installed_exe(entry)
            if not exe:
                raise ValueError(f"{key} 尚未下載，請先在 MCP 庫按「下載」。")
            command = str(exe)
        else:
            command = self._expand(entry["command"], key)
        env = {k: self._expand(v, key) for k, v in entry.get("env", {}).items()}
        secrets = self.secrets()
        missing = [s for s in entry.get("secrets", []) if not secrets.get(s)]
        if missing:
            raise ValueError(f"{key} 缺少祕密：{'、'.join(missing)}。請在 MCP 庫編輯並填入。")
        env.update({s: secrets[s] for s in entry.get("secrets", [])})
        return {"command": command, "args": args, "env": env}

    # Hosts that cut a single tool call short. QAI then waits in slices and asks the agent to keep polling.
    HOST_CALL_LIMIT = {"claude-desktop": 50}

    def render(self, entry: dict[str, Any], kind: str, profile: Any) -> dict[str, Any]:
        s = self.spec(entry, profile)
        if entry["type"] == "builtin" and kind in self.HOST_CALL_LIMIT:
            s["env"] = {**s["env"], "AGENTDOCK_MAX_WAIT": str(self.HOST_CALL_LIMIT[kind])}
        timeout = entry.get("timeout_sec")
        if "url" in s:
            if kind == "codex":
                return {"url": s["url"], "enabled": True}
            if kind == "opencode":
                return {"type": "remote", "url": s["url"], "enabled": True}
            if kind == "claude-code":
                return {"type": "http", "url": s["url"]}
            raise ValueError("Claude Desktop 的遠端 MCP 請使用 App 的「連接器」介面。")
        if kind == "opencode":
            out: dict[str, Any] = {"type": "local", "command": [s["command"], *s["args"]], "enabled": True}
            if s["env"]:
                out["environment"] = s["env"]
            return out
        out = {"command": s["command"], "args": s["args"]}
        if s["env"]:
            out["env"] = s["env"]
        if kind == "codex":
            out["enabled"] = True
            if timeout:
                out["tool_timeout_sec"] = timeout
        elif kind == "claude-code" and timeout:
            out["timeout"] = timeout * 1000
        return out

    def in_sync(self, entry: dict[str, Any], kind: str, profile: Any, current: Any) -> bool | None:
        """True/False when comparable; None when it cannot be rendered yet (not downloaded, missing secret)."""
        try:
            wanted = self.render(entry, kind, profile)
        except ValueError:
            return None
        def strip(d: Any) -> Any:  # ignore on/off state and empty optional fields
            if not isinstance(d, dict):
                return d
            return {k: v for k, v in d.items()
                    if k != "enabled" and v not in ([], {}, None) and not (k == "type" and v == "stdio")}
        return strip(wanted) == strip(current)

    # ------------------------------------------------------------------ import
    def from_config(self, key: str, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Turn an existing client entry into a library definition + secret values."""
        root, home = str(self.root), str(Path.home())

        def portable(text: str) -> str:
            return text.replace(root, "{AGENTDOCK}").replace(home, "{HOME}")

        if config.get("url"):
            return {"key": key, "type": "remote", "url": config["url"]}, {}
        command = config.get("command")
        args = list(config.get("args", []))
        if isinstance(command, list):  # opencode
            command, args = command[0], list(command[1:])
        env = dict(config.get("env") or config.get("environment") or {})
        secrets = {k: v for k, v in env.items() if SECRET_HINT.search(k)}
        plain = {k: portable(str(v)) for k, v in env.items() if k not in secrets}
        plain, local = split_local(plain)   # paths outside AgentDock and the home folder stay on this computer
        secrets.update({k: env[k] for k in local})
        entry: dict[str, Any] = {"key": key}
        base = Path(str(command)).name.lower()
        runner, rest = None, args
        if base in ("uvx", "uvx.exe"):
            runner = "uvx"
        elif base in ("npx", "npx.cmd"):
            runner = "npx"
        elif base in ("cmd", "cmd.exe") and [a.lower() for a in args[:2]] in (["/c", "npx"], ["/c", "npx.cmd"]):
            runner, rest = "npx", args[2:]
        split = _split_runner(runner, rest) if runner else None
        if split:
            options, package, tail = split
            entry.update(type=runner, package=package, args=[portable(a) for a in tail])
            if options:
                entry["options"] = options
        else:
            entry.update(type="command", command=portable(str(command)), args=[portable(a) for a in args])
        if plain:
            entry["env"] = plain
        if secrets:
            entry["secrets"] = sorted(secrets)
        timeout = config.get("tool_timeout_sec") or (config.get("timeout", 0) // 1000 if isinstance(config.get("timeout"), int) else 0)
        if timeout:
            entry["timeout_sec"] = int(timeout)
        return self.validate(entry), {k: str(v) for k, v in secrets.items()}


# Options that take a value, so the word after them is not the package name.
_VALUE_FLAGS = {
    "uvx": {"--with", "--with-editable", "--with-requirements", "--from", "--python", "-p", "--index", "--index-url",
            "--extra-index-url", "--default-index", "--find-links", "-f", "--env-file", "--constraints", "-c",
            "--overrides", "--directory", "--project", "--cache-dir", "--config-file", "--python-preference"},
    "npx": {"-p", "--package", "-c", "--call", "--registry", "--cache", "--userconfig", "--prefix", "-w", "--workspace"},
}


def _vtuple(v: str) -> tuple[int, ...] | None:
    m = re.search(r"\d+(?:\.\d+)*", v or "")
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def _newer(latest: str, current: str) -> bool:
    a, b = _vtuple(latest), _vtuple(current)
    if a is None or b is None:
        return latest.lstrip("vV") != current.lstrip("vV")
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _replace_running(source: Path, target: Path) -> None:
    """Replace target with source even while target is running: Windows lets a running .exe be renamed, not overwritten."""
    for old in target.parent.glob(target.name + ".agentdock-old*"):
        try:
            old.unlink()
        except OSError:
            pass
    temp = target.with_name(target.name + ".agentdock-new")
    shutil.copy2(source, temp)
    aside = target.with_name(f"{target.name}.agentdock-old{os.getpid()}")
    try:
        if target.exists():
            os.replace(target, aside)
        os.replace(temp, target)
    except OSError as e:
        if aside.exists() and not target.exists():
            os.replace(aside, target)
        temp.unlink(missing_ok=True)
        raise ValueError(f"無法取代 {target}：{e}") from None
    try:
        aside.unlink()
    except OSError:
        pass  # still running: removed next time


def _same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _split_runner(runner: str, args: list[str]) -> tuple[list[str], str, list[str]] | None:
    """uvx/npx argv -> (runner options, package, server args). None when no package is found."""
    options: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if runner == "npx" and a in ("-y", "--yes"):
            i += 1
            continue
        if a.startswith("-"):
            options.append(a)
            if "=" not in a and a in _VALUE_FLAGS[runner] and i + 1 < len(args):
                options.append(args[i + 1])
                i += 1
            i += 1
            continue
        return options, a, args[i + 1:]
    return None


def _get(url: str, progress: Callable[[str], None] | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AgentDock", "Accept": "application/octet-stream, application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        chunks, got, shown = [], 0, 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if got > 500 * 1024 * 1024:
                raise ValueError("下載檔案超過 500 MB。")
            if progress and got - shown >= 1 << 20:
                shown = got
                progress(f"下載中 {got / 1e6:.1f}" + (f" / {total / 1e6:.1f} MB" if total else " MB"))
        return b"".join(chunks)


def _download(url: str, progress: Callable[[str], None]) -> bytes:
    """Large files: prefer the system curl (much faster than urllib through some networks), else urllib."""
    curl = shutil.which("curl")
    if curl:
        import subprocess
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "download"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen([curl, "-fsSL", "--retry", "2", "-A", "AgentDock", "-o", str(out), url],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
            start = time.monotonic()
            while proc.poll() is None:
                time.sleep(0.5)
                if out.exists():
                    mb = out.stat().st_size / 1e6
                    progress(f"下載中 {mb:.1f} MB（{mb / max(time.monotonic() - start, 0.1):.1f} MB/s）")
            if proc.returncode == 0 and out.exists():
                return out.read_bytes()
            err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            progress(f"curl 失敗（{err or proc.returncode}），改用內建下載…")
    return _get(url, progress)


def _safe_extract_zip(z: zipfile.ZipFile, target: Path) -> None:
    base = target.resolve()
    for info in z.infolist():
        dest = (target / info.filename).resolve()
        if base != dest and base not in dest.parents:
            raise ValueError("壓縮檔含有不安全的路徑。")
    z.extractall(target)
