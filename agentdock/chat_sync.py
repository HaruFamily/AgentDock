"""Read-only local chat adapters. No credentials, inference calls, or client configuration writes.

SQLite and JSONL are private client formats: unknown schemas fail per source, not the app.
Only completed JSONL lines are checkpointed; native message IDs make replay idempotent.
"""
from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentdock.files import atomic_json, load_json


def iso(value) -> str:
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value,
                                      timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def text_content(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b["text"] for b in content if isinstance(b, dict)
                     and b.get("type", "").lower() in ("text", "input_text", "output_text")
                     and isinstance(b.get("text"), str))


def image_content(content) -> list[dict]:
    """Only explicit image blocks; never fetch URLs or open transcript-supplied paths."""
    result = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type", "").lower()
        if kind not in ("image", "input_image", "image_url", "file", "local_image"):
            continue
        if kind == "local_image":
            # Native structured attachment only, never a path scraped from prose.
            url = ""
            path = Path(block.get("path") or "")
            try:
                if path.is_absolute() and not str(path).startswith(("\\\\", "//")):
                    with path.open("rb") as attachment:
                        data = attachment.read(5 * 1024 * 1024 + 1)
                    from agentdock.qa.store import image_mime
                    mime = image_mime(data)
                    if mime and len(data) <= 5 * 1024 * 1024:
                        url = f"data:{mime};base64," + base64.b64encode(data).decode()
            except OSError:
                pass
            result.append({"name": path.name or "圖片", "data_url": url})
            if len(result) == 8:
                break
            continue
        if kind == "file" and not str(block.get("mime", "")).startswith("image/"):
            continue
        source = block.get("source") or {}
        source = source if isinstance(source, dict) else {}
        url = block.get("image_url") or block.get("url") or source.get("url") or ""
        if isinstance(url, dict):
            url = url.get("url", "")
        if source.get("type") == "base64":
            url = f"data:{source.get('media_type', '')};base64,{source.get('data', '')}"
        result.append({"name": str(block.get("filename") or "圖片")[:200],
                       "data_url": url if isinstance(url, str) and url.startswith("data:") else ""})
        if len(result) == 8:
            break
    return result


def result_ids(value, depth=0) -> list[str]:
    """MCP response IDs link the question's original protocol identity to the real session."""
    if depth > 10:
        return []
    if isinstance(value, str):
        try:
            return result_ids(json.loads(value), depth + 1)
        except (ValueError, TypeError):
            return []
    if isinstance(value, list):
        return [i for item in value for i in result_ids(item, depth + 1)]
    if isinstance(value, dict):
        ids = [value[k] for k in ("id", "request_id") if isinstance(value.get(k), str)]
        return ids + [i for k, item in value.items() if k not in ("id", "request_id")
                      for i in result_ids(item, depth + 1)]
    return []


def question_refs(name, arguments) -> list[dict]:
    """Extract literal ask_user identities, without executing code or guessing variable values.

    Codex's exec wrapper contains JavaScript; tokenize strings/comments first so examples
    inside patches, prompts and documentation cannot be mistaken for real tool calls.
    """
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except ValueError:
            decoded = None
    else:
        decoded = arguments
    if "ask_user" in name and isinstance(decoded, dict):
        if all(isinstance(decoded.get(k), str) for k in ("work_id", "request_key")):
            return [{k: decoded[k] for k in ("work_id", "request_key")}]
        return []
    if name not in ("exec", "functions.exec") or not isinstance(arguments, str):
        return []
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_$][\w$]*|[^\s]', arguments)
    refs = []
    for index, token in enumerate(tokens):
        if not re.fullmatch(r"mcp__(?:agentchat|agentdock_qa)__ask_user", token):
            continue
        if tokens[max(0, index - 2):index] != ["tools", "."] or tokens[index + 1:index + 3] != ["(", "{"]:
            continue
        depth, fields = 1, {}
        for pos in range(index + 3, len(tokens)):
            t = tokens[pos]
            if t == "{":
                depth += 1
            elif t == "}":
                depth -= 1
                if depth == 0:
                    break
            if depth == 1 and t.strip("\"'") in ("work_id", "request_key") and tokens[pos + 1:pos + 2] == [":"]:
                try:
                    value = ast.literal_eval(tokens[pos + 2])
                except (ValueError, SyntaxError, IndexError):
                    continue
                if isinstance(value, str) and tokens[pos + 3:pos + 4] in ([","], ["}"]):
                    fields[t.strip("\"'")] = value
        if len(fields) == 2:
            refs.append(fields)
    return refs


def event(key, kind, text, timestamp, **extra) -> dict:
    return dict(key=str(key), kind=kind, text=text, created_at=iso(timestamp), **extra)


def codex_record(row: dict, state: dict) -> tuple[list[dict], list[str | dict]]:
    p = row.get("payload") or {}
    typ, timestamp = p.get("type"), row.get("timestamp")
    output, links = [], []
    if row.get("type") == "session_meta":
        state["session"] = p.get("id") or p.get("session_id")
        # Exclude review/subagent sessions, which otherwise appear as user conversations.
        state["subagent"] = bool(p.get("parent_thread_id")) or isinstance(p.get("source"), dict)
        return [], []
    if not timestamp:
        return [], []
    if row.get("type") == "event_msg":
        if typ == "task_started":
            state["turn"] = p.get("turn_id") or str(row.get("ordinal", timestamp))
            state.pop("last", None)
            output.append(event("start:" + state["turn"], "started", "", timestamp))
        elif typ == "item_completed":
            item = p.get("item") or {}
            kind = item.get("type")
            if kind in ("UserMessage", "AgentMessage"):
                content = text_content(item.get("content"))
                images = image_content(item.get("content"))
                if kind == "UserMessage" and images and content.lstrip().startswith("# Files mentioned by the user:"):
                    marker = "## My request:"
                    if marker in content and "Image attachment: true" in content.split(marker, 1)[0]:
                        content = content.split(marker, 1)[1].lstrip()
                if content or images:
                    e = event(item["id"], "user_message" if kind == "UserMessage" else "progress", content, timestamp, images=images)
                    if kind == "AgentMessage":
                        e["phase"] = item.get("phase") or ""
                    output.append(e)
                    if kind == "AgentMessage":
                        state["last"] = e
            else:
                links = result_ids(item.get("result"))
        elif typ in ("task_complete", "turn_aborted"):
            kind = "cancelled" if typ == "turn_aborted" else "completed"
            last = state.get("last")
            if last:
                output.append({**last, "kind": kind, "finished_at": iso(timestamp)})
            else:
                output.append(event("end:" + str(p.get("turn_id") or state.get("turn", timestamp)), kind,
                                    p.get("last_agent_message") or "", timestamp))
        elif state.get("mode") != "paginated" and typ in ("user_message", "agent_message"):
            message = p.get("message", "")
            if message:
                e = event("legacy:" + str(row.get("ordinal", timestamp)),
                          "user_message" if typ == "user_message" else "progress", message, timestamp)
                output.append(e)
                if typ == "agent_message":
                    state["last"] = e
    # Tool return values only; never display reasoning, injected prompts or compaction summaries.
    if row.get("type") == "response_item" and typ in ("function_call_output", "custom_tool_call_output"):
        links.extend(result_ids(p.get("output")))
    if row.get("type") == "response_item" and typ in ("function_call", "custom_tool_call"):
        links.extend(question_refs(p.get("name", ""), p.get("arguments", p.get("input"))))
    return output, links


def claude_record(row: dict, state: dict) -> tuple[list[dict], list[str | dict]]:
    if row.get("isSidechain") or row.get("isMeta"):
        return [], []
    if row.get("sessionId"):
        state["session"] = row["sessionId"]
    if row.get("type") == "custom-title":
        state["title"] = row.get("customTitle") or state.get("title")
        state["custom_title"] = True
    if row.get("type") == "ai-title" and not state.get("custom_title"):
        state["title"] = row.get("aiTitle") or state.get("title")
    timestamp, uid = row.get("timestamp"), row.get("uuid")
    msg = row.get("message") or {}
    blocks = msg.get("content", [])
    links = [i for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"
             for i in result_ids(b.get("content"))] if isinstance(blocks, list) else []
    if isinstance(blocks, list):
        links.extend(ref for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                     for ref in question_refs(b.get("name", ""), b.get("input")))
    if not timestamp or not uid or row.get("type") not in ("user", "assistant"):
        return [], links
    text = text_content(blocks)
    images = image_content(blocks)
    # User tool results have role=user but are not user-authored chat messages.
    if row["type"] == "user":
        if not text and not images:
            return [], links
        if text:
            state.setdefault("title", text.splitlines()[0][:160])
        return [event(uid, "user_message", text, timestamp, images=images)], links
    reason = msg.get("stop_reason")
    kind = "completed" if reason in ("end_turn", "stop_sequence") else "progress"
    if text or images:
        return [event(uid, kind, text, timestamp, images=images)], links
    return [], links


@contextmanager
def readonly_db(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class Source:
    kind: str
    root: Path
    name: str
    owner: str
    mcp_owner: str | None = None


def sources(profiles=(), home: Path | None = None) -> list[Source]:
    home = home or Path.home()
    candidates = []
    for profile in profiles:
        if profile.kind in ("codex", "claude-code"):
            config = Path(profile.path)
            root = config.parent
            # The global MCP config is ~/.claude.json; transcripts live in
            # ~/.claude/projects (or CLAUDE_CONFIG_DIR), not ~/projects.
            if profile.kind == "claude-code" and config.name == ".claude.json":
                root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or root / ".claude")
            candidates.append((profile.kind, root, profile.name,
                               hashlib.sha256(f"agentdock-{profile.id}".encode()).hexdigest()))
    defaults = [("codex", Path(os.environ.get("CODEX_HOME") or home / ".codex"), "Codex"),
                ("claude-code", Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), "Claude Code"),
                ("opencode", Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "opencode", "OpenCode")]
    for kind, root, name in defaults:
        matching = [p for p in profiles if p.kind == kind]
        caller = hashlib.sha256(f"agentdock-{matching[0].id}".encode()).hexdigest() if len(matching) == 1 else None
        candidates.append((kind, root, matching[0].name if len(matching) == 1 else name, caller))
    seen, result = set(), []
    for kind, root, name, caller in candidates:
        key = (kind, str(root.resolve()).casefold())
        if key not in seen and root.is_dir():
            seen.add(key)
            owner = "local:" + kind + ":" + hashlib.sha256(key[1].encode()).hexdigest()
            result.append(Source(kind, root, name, owner, caller))
    return result


class ChatSync:
    """Background incremental reader. Only file metadata is polled; UI changes on new events."""

    def __init__(self, store, directory: Path, get_sources=sources, on_status=None):
        self.store = store
        self.file = directory / "chat-sync.json"
        try:
            self.state = load_json(self.file, {"files": {}, "databases": {}})
            if not isinstance(self.state, dict) or any(not isinstance(self.state.get(k), dict) for k in ("files", "databases")):
                raise ValueError("invalid checkpoint")
        except (ValueError, OSError):
            logging.warning("AgentChat checkpoint unavailable; rebuilding from native IDs")
            self.state = {"files": {}, "databases": {}}
        self.get_sources = get_sources
        self.session_since = iso(store.session_started_at) if store.session_started_at else None
        self.live_since = self.session_since or self.state.get("last_scan") or iso(time.time())
        self.notify_since = iso(time.time())
        self.on_status = on_status
        self._last_status = None
        self.stop_event = threading.Event()
        self.thread = None
        self.errors: dict[str, str] = {}
        self._signatures = {}
        # Repair only already-saved Codex attachment wrappers, never import other
        # old messages or activate old conversations while replaying their source.
        self._image_repairs = {}
        for entry in store._chat["events"]:
            if entry.get("external") and entry.get("kind") == "progress" and "phase" not in entry:
                self._image_repairs.setdefault((entry["owner"], entry["work_id"]), set()).add(entry["request_key"])
            if (entry.get("external") and entry.get("kind") == "user_message"
                    and entry.get("text", "").lstrip().startswith("# Files mentioned by the user:")
                    and "Image attachment: true" in entry["text"]):
                self._image_repairs.setdefault((entry["owner"], entry["work_id"]), set()).add(entry["request_key"])

    def start(self):
        self.thread = threading.Thread(target=self._run, name="AgentChat receiver", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.scan()
            except Exception:
                logging.exception("AgentChat receiver failed; retrying")
                if self.on_status:
                    self.on_status("接收失敗，正在重試；可查看 AgentDock 記錄")
            self.stop_event.wait(2)

    def scan(self):
        before = json.dumps(self.state, sort_keys=True)
        active_sources = self.get_sources()
        for source in active_sources:
            if self.stop_event.is_set():
                break
            try:
                if source.kind == "opencode":
                    self._opencode(source)
                else:
                    self._json_files(source)
                self.errors.pop(str(source.root), None)
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if self.errors.get(str(source.root)) != detail:
                    logging.warning("AgentChat %s sync unavailable: %s", source.name, detail)
                self.errors[str(source.root)] = detail
        status = "；".join(f"{s.name}：{'讀取失敗' if str(s.root) in self.errors else '接收中'}" for s in active_sources)
        status = (status or "尚未找到本機對話紀錄") + "；Claude Desktop 一般聊天：僅支援 MCP 問答與回報"
        if status != self._last_status and self.on_status:
            self.on_status(status)
        self._last_status = status
        if before != json.dumps(self.state, sort_keys=True):
            self.state["last_scan"] = iso(time.time())
            atomic_json(self.file, self.state)

    def _json_files(self, source):
        metadata = {}
        indexed = False
        if source.kind == "codex":
            indexes = sorted(source.root.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
            if indexes:
                with readonly_db(indexes[0]) as db:
                    columns = {r[1] for r in db.execute("PRAGMA table_info(threads)")}
                    name = "coalesce(nullif(name,''),title)" if "name" in columns else "title"
                    mode = "history_mode" if "history_mode" in columns else "'legacy'"
                    for r in db.execute(f"SELECT id,rollout_path,{name} as title,{mode} as mode FROM threads WHERE archived=0"):
                        metadata[str(Path(r["rollout_path"]))] = dict(r)
                indexed = True
                self.state.setdefault("indexed_sources", {})[source.owner] = True
            elif self.state.get("indexed_sources", {}).get(source.owner):
                raise OSError("Codex session index unavailable; keeping existing conversations")
            # An empty authoritative index is empty, not permission to resurrect old files.
            paths = [Path(p) for p in metadata] if indexed else list((source.root / "sessions").rglob("*.jsonl"))
        else:
            # One level excludes subagents/<id>.jsonl without guessing agent names.
            if not (source.root / "projects").is_dir():
                raise OSError("Claude Code transcript directory unavailable")
            # Unlike glob, explicit enumeration reports permission/I/O failures instead
            # of returning a misleading empty inventory that would hide saved chats.
            paths = [path for folder in (source.root / "projects").iterdir() if folder.is_dir()
                     for path in folder.iterdir() if path.suffix == ".jsonl" and path.is_file()]
        paths = sorted((p for p in paths if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
        failures = 0
        for path in paths:
            if self.stop_event.is_set():
                return
            try:
                self._read_jsonl(source, path, metadata.get(str(path), {}))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                failures += 1
        if failures:
            raise RuntimeError(f"{failures} transcript(s) could not be read; will retry")
        if indexed:
            # A temporarily inaccessible rollout is not a deletion when the index still lists it.
            sessions = {m["id"] for m in metadata.values()}
        else:
            sessions = {self.state["files"][str(p)]["session"] for p in paths if str(p) in self.state["files"]}
        self.store.set_source_sessions(source.owner, sessions)

    def _read_jsonl(self, source, path, meta):
        key = str(path)
        info = path.stat()
        cursor = copy.deepcopy(self.state["files"].get(key, {"offset": 0, "session": meta.get("id") or path.stem}))
        if meta:
            cursor.update(session=meta["id"], title=meta["title"], mode=meta["mode"])
        identity = [info.st_dev, info.st_ino]
        signature = (info.st_mtime_ns, info.st_size, info.st_ino, cursor.get("title"))
        if self._signatures.get(key) == signature and cursor["offset"] == info.st_size:
            return
        if info.st_size < cursor["offset"] or (cursor.get("file_id") and cursor["file_id"] != identity):
            cursor["offset"] = 0
            cursor.pop("last", None)
        cursor["file_id"] = identity
        output, links = [], []
        parse = codex_record if source.kind == "codex" else claude_record
        repairs = self._image_repairs.get((source.owner, cursor["session"]), set()) if source.kind == "codex" else set()
        if repairs and key not in self._signatures:
            cursor["offset"] = 0
        # Old, unchanged files need no transcript replay on application launch.
        # Keep the last partial line so an in-flight append can finish it later.
        if not repairs and self.session_since and key not in self._signatures and iso(info.st_mtime) < self.session_since:
            with path.open("rb") as stream:
                if source.kind == "codex":
                    try:
                        parse(json.loads(stream.readline()), cursor)
                    except (ValueError, TypeError, KeyError):
                        pass
                stream.seek(max(0, info.st_size - 8192))
                tail = stream.read()
                while b"\n" not in tail and len(tail) < info.st_size:
                    stream.seek(max(0, info.st_size - len(tail) - 8192))
                    tail = stream.read()
                boundary = tail.rfind(b"\n")
                cursor["offset"] = info.st_size - len(tail) + boundary + 1 if boundary >= 0 else 0
        # Bound work per file per pass, leaving the UI and other sources responsive.
        with path.open("rb") as stream:
            stream.seek(cursor["offset"])
            consumed = 0
            while consumed < 4 * 1024 * 1024 and not self.stop_event.is_set():
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                consumed += len(line)
                try:
                    row = json.loads(line)
                    events, found = parse(row, cursor)
                    recent = not self.session_since or (row.get("timestamp") and iso(row["timestamp"]) >= self.session_since)
                    if recent:
                        output.extend(events)
                    else:
                        output.extend({**e, "history_repair": True} for e in events if e["key"] in repairs)
                    links.extend(found)
                except (ValueError, TypeError, KeyError, AttributeError):
                    logging.warning("AgentChat ignored malformed record in %s at %s", path.name, stream.tell() - len(line))
                cursor["offset"] = stream.tell()
        if not cursor.get("subagent") and (not self.session_since or output or links):
            title = cursor.get("title") or cursor["session"]
            links = [{**ref, "owner": source.mcp_owner or source.owner} if isinstance(ref, dict) else ref for ref in links]
            self.store.import_chat(source.owner, source.name, cursor["session"], title, output, links,
                                   self.live_since, self.notify_since)
        self.state["files"][key] = cursor
        self._signatures[key] = signature

    def _opencode(self, source):
        path = source.root / "opencode.db"
        if not path.is_file():
            return
        wal = Path(str(path) + "-wal")
        signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (path, wal) if p.exists())
        if self._signatures.get(str(path)) == signature:
            return
        saved = self.state["databases"].setdefault(str(path), {})
        sessions = set()
        with readonly_db(path) as db:
            # A single read transaction gives messages and parts the same WAL snapshot.
            db.execute("BEGIN")
            for session in db.execute("SELECT id,title,time_updated FROM session WHERE parent_id IS NULL AND time_archived IS NULL"):
                sid = session["id"]
                sessions.add(sid)
                fingerprint = (session["time_updated"], session["title"])
                # Parts may be updated without session.time_updated changing during streaming.
                revision = db.execute("SELECT max(time_updated),count(*) FROM part WHERE session_id=?", (sid,)).fetchone()
                message_revision = db.execute("SELECT max(time_updated),count(*) FROM message WHERE session_id=?", (sid,)).fetchone()
                stamp = [*fingerprint, *revision, *message_revision]
                if saved.get(sid) == stamp and str(path) in self._signatures:
                    continue
                output, links = [], []
                for row in db.execute("SELECT id,data,time_created,time_updated FROM message WHERE session_id=? ORDER BY time_created,id", (sid,)):
                    if self.session_since:
                        part_updated = db.execute("SELECT max(time_updated) FROM part WHERE message_id=?", (row["id"],)).fetchone()[0]
                        updated = max(row["time_updated"] or 0, part_updated or 0, row["time_created"])
                        if iso(updated) < self.session_since:
                            continue
                    msg = json.loads(row["data"])
                    blocks = [json.loads(p[0]) for p in db.execute(
                        "SELECT data FROM part WHERE message_id=? ORDER BY time_created,id", (row["id"],))]
                    text = text_content([b for b in blocks if not b.get("synthetic") and not b.get("ignored")])
                    images = image_content([b for b in blocks if not b.get("synthetic") and not b.get("ignored")])
                    links.extend(i for b in blocks if b.get("type") == "tool"
                                 for i in result_ids(b.get("state", {}).get("output")))
                    links.extend(ref for b in blocks if b.get("type") == "tool"
                                 for ref in question_refs(b.get("tool", ""), b.get("state", {}).get("input")))
                    if msg.get("role") == "user":
                        if text or images:
                            output.append(event(row["id"], "user_message", text, row["time_created"], images=images))
                    elif msg.get("role") == "assistant":
                        finished = msg.get("time", {}).get("completed")
                        terminal = bool(finished) and msg.get("finish") in ("stop", "end_turn", "length", "content-filter")
                        kind = "failed" if msg.get("error") else "completed" if terminal else "progress"
                        if text or images or kind != "progress":
                            output.append(event(row["id"], kind, text, row["time_created"],
                                                finished_at=iso(finished or row["time_created"]), images=images))
                links = [{**ref, "owner": source.mcp_owner or source.owner} if isinstance(ref, dict) else ref for ref in links]
                if not self.session_since or output or links:
                    self.store.import_chat(source.owner, source.name, sid, session["title"], output, links,
                                           self.live_since, self.notify_since)
                saved[sid] = stamp
        self.store.set_source_sessions(source.owner, sessions)
        self._signatures[str(path)] = signature
