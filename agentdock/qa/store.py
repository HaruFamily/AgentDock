"""Persistent question store. Same JSON layout as AgentDock 0.5 (questions.json + attachments/)."""
from __future__ import annotations

import base64
from bisect import bisect_left
import copy
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentdock.files import atomic_json, atomic_write, load_json
from agentdock.qa.schema import ChatEventInput, Draft, empty_draft

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
CONNECTED_SECONDS = 5.0


def image_mime(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_DATA_URL = re.compile(r"^data:([a-zA-Z0-9.+/-]+);base64,([A-Za-z0-9+/]*={0,2})$")


def decode_data_url(url: str) -> tuple[bytes, str]:
    match = _DATA_URL.match(url)
    if not match:
        raise ValueError("附件格式無效，請重新加入檔案。")
    data = base64.b64decode(match.group(2))
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("每個附件上限為 5 MB。")
    declared = match.group(1)
    mime = image_mime(data) or ("application/octet-stream" if declared.startswith("image/") else declared)
    return data, mime


def guess_mime(name: str, data: bytes) -> str:
    found = image_mime(data)
    if found:
        return found
    import mimetypes

    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_key(*parts: str) -> str:
    return json.dumps(parts, ensure_ascii=False)


class QuestionStore:
    """Thread-safe: the broker thread writes, the UI thread reads."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.assets = directory / "attachments"
        self.assets.mkdir(parents=True, exist_ok=True)
        self.file = directory / "questions.json"
        self._lock = threading.RLock()
        self._questions: list[dict[str, Any]] = load_json(self.file, [])
        if not isinstance(self._questions, list):
            raise RuntimeError("questions.json 格式錯誤，請先保留並修復該檔案。")
        self.last_seen: dict[str, float] = {}
        self._listeners: list[Callable[[str, dict | None], None]] = []
        self.chat_file = directory / "chat.json"
        self._chat = load_json(self.chat_file, {"events": [], "hidden_questions": []})
        if not isinstance(self._chat, dict) or not isinstance(self._chat.get("events"), list) or not isinstance(self._chat.get("hidden_questions"), list):
            raise RuntimeError("chat.json 格式錯誤，請先保留並修復該檔案。")
        # Running is an event-driven, process-local hint, never inferred after a restart.
        self._running: set[tuple[str, str]] = set()
        self.session_started_at = None
        self._active_chats = frozenset()
        self._view_sources = None
        self._view_index = {}
        self._view_summaries = []
        self._read_lock = threading.Lock()
        self._read_requests = {}
        self._read_thread = None

    def _activate(self, owner, work_id):
        self._active_chats = self._active_chats | {(owner, work_id)}

    def _chat_key(self, owner, work_id):
        # Legacy native-session aliases are deliberately ignored in Inbox.
        return owner, work_id

    def _save_chat(self, data: dict) -> None:
        atomic_json(self.chat_file, data)
        self._chat = data

    def receive(self, owner: str, source: str, raw: dict) -> dict:
        payload = ChatEventInput.model_validate(raw).model_dump()
        with self._lock:
            for old in self._chat["events"]:
                if (old["owner"], old["work_id"], old["request_key"]) == (owner, payload["work_id"], payload["request_key"]):
                    if any(old[k] != v for k, v in payload.items()):
                        raise ValueError("這個 request_key 已用於不同回報，請使用新的 request_key。")
                    return copy.deepcopy(old)
            key = (owner, payload["work_id"])
            if payload["kind"] in ("completed", "failed", "cancelled") and any(
                q["owner"] == owner and q["work_id"] == payload["work_id"] and q["status"] == "pending"
                and q["id"] not in self._chat["hidden_questions"]
                for q in self._questions
            ):
                raise ValueError("任務仍有待回答問題，不能回報結束；請先取得回答或取消結果。")
            event = {**payload, "id": str(uuid.uuid4()), "owner": owner, "source": source,
                     "created_at": _now(), "read": False}
            data = {**self._chat, "events": [*self._chat["events"], event]}
            self._save_chat(data)
            if payload["kind"] in ("started", "user_message"):
                self._running.add(key)
            elif payload["kind"] in ("completed", "failed", "cancelled"):
                self._running.discard(key)
            self._activate(*key)
        if self._chat_key(owner, payload["work_id"]) == (owner, payload["work_id"]):
            self._emit("new-message", copy.deepcopy(event))
        self._emit("change")
        return copy.deepcopy(event)

    def _conversation_view(self):
        """Immutable committed snapshots: readers never wait for the sync writer's disk I/O.

        Writers replace _chat/_questions rather than mutating published records. Cache the
        grouping once per snapshot; list reads copy metadata, detail reads copy one page.
        """
        chat, questions, running = self._chat, self._questions, frozenset(self._running)
        previous = self._view_sources
        active = self._active_chats
        if previous and previous[0] is chat and previous[1] is questions and previous[2] == running and previous[3] == active:
            return self._view_index, self._view_summaries
        def canonical(entry):
            return entry["owner"], entry["work_id"]
        grouped = {}
        hidden = set(chat["hidden_questions"])
        for q in questions:
            if q["id"] not in hidden:
                grouped.setdefault(canonical(q), []).append({**q, "kind": "question"})
        for e in chat["events"]:
            if e.get("external") or e["kind"] not in ("completed", "failed", "cancelled"):
                continue
            key = canonical(e)
            if key != (e["owner"], e["work_id"]) and not e.get("external"):
                continue
            grouped.setdefault(key, []).append(e)
        index, summaries = {}, []
        for key, entries in grouped.items():
            entries = sorted(entries, key=lambda e: e["created_at"])
            last = entries[-1]
            pending = any(e["kind"] == "question" and e["status"] == "pending" for e in entries)
            terminal = [e for e in entries if e["kind"] in ("completed", "failed", "cancelled")]
            activity = max((e.get("resolved_at", e["created_at"]) for e in entries
                            if e["kind"] in ("question", "started", "user_message")), default="")
            latest_terminal = max(terminal, key=lambda e: e.get("finished_at", e["created_at"]), default=None)
            unread = (latest_terminal is not None and not latest_terminal["read"]
                      and latest_terminal.get("finished_at", latest_terminal["created_at"]) >= activity)
            state = "waiting" if pending else "running" if key in running else "unread" if unread else ""
            meta = {}
            summary = {"owner": key[0], "work_id": key[1], "work_title": meta.get("title", last["work_title"]),
                       "source": meta.get("source", last["source"]), "state": state,
                       "updated_at": max(e.get("resolved_at", e.get("finished_at", e["created_at"])) for e in entries)}
            visible = sorted((e for e in entries if not (e["kind"] == "progress" and e.get("phase") == "commentary")
                              and (e["kind"] == "question" or e.get("text") or e.get("images")
                              or e["kind"] in ("completed", "failed", "cancelled"))),
                             key=lambda e: (e.get("resolved_at", e["created_at"]), e["id"]))
            index[key] = {"summary": summary, "entries": entries, "visible": visible,
                          "keys": [(e.get("resolved_at", e["created_at"]), e["id"]) for e in visible]}
            summaries.append(summary)
        summaries.sort(key=lambda c: c["updated_at"], reverse=True)
        self._view_index, self._view_summaries = index, summaries
        self._view_sources = (chat, questions, running, active)
        return index, summaries

    def conversation_summaries(self) -> list[dict]:
        return [dict(s) for s in self._conversation_view()[1]]

    def inbox_tasks(self) -> list[dict]:
        """Only the latest item and every pending question, never a history page."""
        index, summaries = self._conversation_view()
        tasks = []
        for summary in summaries:
            item = index[(summary["owner"], summary["work_id"])]
            latest = item["visible"][-1]
            pending = [e for e in item["entries"]
                       if e["kind"] == "question" and e["status"] == "pending"]
            # Answered history is not evidence of a running agent after restart.
            # Only the current process's answer handoff keeps a task in progress.
            if not pending and latest["kind"] == "question" and summary["state"] != "running":
                continue
            tasks.append({**summary, "latest": copy.deepcopy(latest),
                          "pending": copy.deepcopy(pending)})
        return tasks

    def conversation_page(self, owner: str, work_id: str, limit: int = 30, before=None) -> dict | None:
        item = self._conversation_view()[0].get((owner, work_id))
        if item is None:
            return None
        entries, keys = item["visible"], item["keys"]
        end = bisect_left(keys, before) if before is not None else len(entries)
        start = max(0, end - max(1, limit))
        return {**item["summary"], "entries": copy.deepcopy(entries[start:end]), "has_older": start > 0,
                "first_key": keys[start] if start < end else None, "total": len(entries)}

    def conversations(self) -> list[dict]:
        """Full export for integrations; the UI uses summaries and bounded pages instead."""
        index, summaries = self._conversation_view()
        return [{**s, "entries": copy.deepcopy(index[(s["owner"], s["work_id"])]["entries"])} for s in summaries]

    def mark_read(self, owner: str, work_id: str, event_ids: list[str]) -> bool:
        ids = set(event_ids)
        def unread(e):
            return e["id"] in ids and e["owner"] == owner and e["work_id"] == work_id and not e["read"]
        if not ids or not any(unread(e) for e in self._chat["events"]):
            return False
        with self._lock:
            if not any(unread(e) for e in self._chat["events"]):
                return False
            data = {**self._chat, "events": [{**e, "read": True} if unread(e) else e for e in self._chat["events"]]}
            self._save_chat(data)
        self._emit("change")
        return True

    def queue_mark_read(self, owner: str, work_id: str, event_ids: list[str], done=None) -> None:
        """Scrolling must not wait for a sync commit or serialize chat.json on the UI thread."""
        if not event_ids:
            return
        with self._read_lock:
            ids, callbacks = self._read_requests.setdefault((owner, work_id), (set(), set()))
            ids.update(event_ids)
            if done:
                callbacks.add(done)
            if self._read_thread is None:
                self._read_thread = threading.Thread(target=self._write_reads, name="Inbox read receipts", daemon=True)
                self._read_thread.start()

    def _write_reads(self):
        while True:
            with self._read_lock:
                if not self._read_requests:
                    self._read_thread = None
                    return
                key, (ids, callbacks) = self._read_requests.popitem()
            try:
                self.mark_read(*key, list(ids))
            except Exception:
                logging.exception("Inbox could not save read receipt")
            for callback in callbacks:
                try:
                    callback()
                except RuntimeError:  # the view may already have been destroyed
                    pass

    def flush_reads(self):
        with self._read_lock:
            worker = self._read_thread
        if worker:
            worker.join()

    def remove_conversation(self, owner: str, work_id: str) -> None:
        """Remove visible history, retaining question protocol records for in-flight callers/retries."""
        with self._lock:
            data = copy.deepcopy(self._chat)
            removed = [e for e in data["events"] if self._chat_key(e["owner"], e["work_id"]) == (owner, work_id)]
            data["sync_removed"] = sorted(set(data.get("sync_removed", [])) | {e["id"] for e in removed if e.get("external")})
            data["sync_removed_pending"] = sorted(set(data.get("sync_removed_pending", [])) |
                                                  {e["id"] for e in removed if e.get("external") and e["kind"] == "progress"})
            data["events"] = [e for e in data["events"] if e not in removed]
            hidden = set(data["hidden_questions"])
            hidden.update(q["id"] for q in self._questions if self._chat_key(q["owner"], q["work_id"]) == (owner, work_id))
            data["hidden_questions"] = sorted(hidden)
            self._save_chat(data)
            self._running.discard((owner, work_id))
        self._emit("change")

    def visible_questions(self) -> list[dict]:
        hidden = set(self._chat["hidden_questions"])
        return copy.deepcopy([q for q in self._questions if q["id"] not in hidden])

    # -- events -------------------------------------------------------------
    def subscribe(self, listener: Callable[[str, dict | None], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, question: dict | None = None) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, question)
            except Exception:  # a broken listener must never break the store
                pass

    # -- reads --------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._questions)

    def get(self, qid: str, owner: str | None = None) -> dict[str, Any]:
        with self._lock:
            for q in self._questions:
                if q["id"] == qid and (owner is None or q["owner"] == owner):
                    return copy.deepcopy(q)
        raise KeyError("找不到這個工作的問題。")

    def connected(self, qid: str) -> bool:
        seen = self.last_seen.get(qid)
        return seen is not None and time.monotonic() - seen < CONNECTED_SECONDS

    def touch(self, qid: str) -> None:
        was = self.connected(qid)
        self.last_seen[qid] = time.monotonic()
        if not was:
            self._emit("change")

    def detach(self, qid: str) -> None:
        self.last_seen.pop(qid, None)
        self._emit("change")

    # -- writes -------------------------------------------------------------
    def _persist(self, questions: list[dict[str, Any]]) -> None:
        atomic_json(self.file, questions)
        self._questions = questions

    def _replace(self, q: dict[str, Any]) -> None:
        self._activate(q["owner"], q["work_id"])
        self._persist([q if old["id"] == q["id"] else old for old in self._questions])
        self._emit("change")

    def _save_asset(self, name: str, data: bytes, mime: str, caption: str | None = None) -> dict[str, Any]:
        aid = str(uuid.uuid4())
        atomic_write(self.assets / aid, data)
        meta = {"id": aid, "name": Path(name).name[:200] or "attachment", "mime": mime, "size": len(data)}
        if caption:
            meta["caption"] = caption
        return meta

    def create(self, owner: str, source: str, ask: dict[str, Any]) -> dict[str, Any]:
        """ask: validated AskInput dict whose images are {name, data_url, caption}."""
        with self._lock:
            for existing in self._questions:
                if existing["owner"] == owner and existing["work_id"] == ask["work_id"] and existing["request_key"] == ask["request_key"]:
                    labels = lambda opts: [(o["id"], o["label"], o.get("description", "")) for o in opts]
                    if existing["question"] != ask["question"] or existing["mode"] != ask["mode"] or labels(existing["options"]) != labels(ask["options"]):
                        raise ValueError("這個 request_key 已用於不同內容。新問題請使用新的 request_key。")
                    if existing["id"] in self._chat["hidden_questions"]:
                        data = copy.deepcopy(self._chat)
                        members = {q["id"] for q in self.group_members(existing["id"], owner)}
                        data["hidden_questions"] = [qid for qid in data["hidden_questions"] if qid not in members]
                        self._save_chat(data)
                        if existing["status"] == "pending":
                            self._emit("new-question", copy.deepcopy(existing))
                        self._emit("change")
                    return copy.deepcopy(existing)
            every = list(ask["images"]) + [i for o in ask["options"] for i in o["images"]]
            decoded = []
            total = 0
            for image in every:
                data, mime = decode_data_url(image["data_url"])
                if not mime.startswith("image/"):
                    raise ValueError("問題圖片僅接受 PNG、JPEG、GIF 或 WebP。")
                total += len(data)
                decoded.append((image, data, mime))
            if total > MAX_TOTAL_BYTES:
                raise ValueError("一題的圖片總量上限為 20 MB。")
            saved = {id(image): self._save_asset(image.get("name") or "image", data, mime, image.get("caption")) for image, data, mime in decoded}
            q = {
                "id": str(uuid.uuid4()), "owner": owner, "source": source,
                "request_key": ask["request_key"], "work_id": ask["work_id"], "work_title": ask["work_title"],
                "question": ask["question"], "mode": ask["mode"],
                "images": [saved[id(i)] for i in ask["images"]],
                "options": [{"id": o["id"], "label": o["label"], "description": o.get("description", ""),
                             "images": [saved[id(i)] for i in o["images"]]} for o in ask["options"]],
                "created_at": _now(), "status": "pending", "uploads": [], "draft": empty_draft(),
            }
            if ask.get("group"):
                q["group"] = ask["group"]
            self._persist(self._questions + [q])
            self._activate(owner, ask["work_id"])
        self._emit("new-question", copy.deepcopy(q))
        self._emit("change")
        return copy.deepcopy(q)

    # -- groups: several questions asked together, answered and cancelled together -------------
    def create_group(self, owner: str, source: str, base: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """base: request_key/work_id/work_title (+ question as intro); items: question/mode/options/images each."""
        with self._lock:
            first = next((q for q in self._questions if q["owner"] == owner and q["work_id"] == base["work_id"]
                          and q["request_key"] == f"{base['request_key']}#1"), None)
            gid = first["group"]["id"] if first and first.get("group") else str(uuid.uuid4())
            created = []
            for i, item in enumerate(items):
                ask = {**base, **item, "request_key": f"{base['request_key']}#{i + 1}",
                       "group": {"id": gid, "index": i, "size": len(items), "intro": base.get("question", "")}}
                created.append(self.create(owner, source, ask))
            return created

    def group_members(self, qid: str, owner: str | None = None) -> list[dict[str, Any]]:
        q = self.get(qid, owner)
        group = q.get("group")
        if not group:
            return [q]
        with self._lock:
            members = [copy.deepcopy(x) for x in self._questions
                       if x.get("group", {}).get("id") == group["id"] and x["owner"] == q["owner"]]
        return sorted(members, key=lambda x: x["group"]["index"])

    def answer_group(self, answers: dict[str, Any]) -> None:
        """Validate every answer first, then commit all of them; one bad answer commits nothing."""
        with self._lock:
            checked = {}
            for n, (qid, raw) in enumerate(answers.items(), 1):
                q = self.get(qid)
                if q["status"] != "pending":
                    raise ValueError("這組問題已處理，不能重複提交。")
                try:
                    checked[qid] = self._validated(q, raw, True)
                except Exception as e:
                    raise ValueError(f"第 {n} 題：{e}") from None
            now = _now()
            updated = []
            for old in self._questions:
                if old["id"] in checked:
                    self._activate(old["owner"], old["work_id"])
                    old = copy.deepcopy(old)
                    old["answer"] = checked[old["id"]]
                    old["draft"] = copy.deepcopy(old["answer"])
                    old["status"] = "answered"
                    old["resolved_at"] = now
                updated.append(old)
            self._persist(updated)
            self._running.update(self._chat_key(q["owner"], q["work_id"]) for q in updated if q["id"] in checked)
        self._emit("change")

    def cancel_group(self, ids: list[str]) -> None:
        with self._lock:
            now = _now()
            updated = []
            for old in self._questions:
                if old["id"] in ids and old["status"] == "pending":
                    self._activate(old["owner"], old["work_id"])
                    old = {**copy.deepcopy(old), "status": "cancelled", "resolved_at": now}
                updated.append(old)
            self._persist(updated)
            self._running.difference_update(self._chat_key(q["owner"], q["work_id"]) for q in updated if q["id"] in ids)
        self._emit("change")

    def _validated(self, q: dict[str, Any], raw: Any, submit: bool) -> dict[str, Any]:
        answer = Draft.model_validate(raw).model_dump()
        ids = {o["id"] for o in q["options"]}
        sel = answer["selected"]
        if len(set(sel)) != len(sel) or any(s not in ids for s in sel):
            raise ValueError("選項無效。")
        if q["mode"] == "single" and len(sel) > 1:
            raise ValueError("這題只能選一項。")
        if q["mode"] == "text" and sel:
            raise ValueError("文字題不能選擇選項。")
        if any(k not in ids for k in answer["notes"]):
            raise ValueError("備註的選項不存在。")
        uploads = {a["id"] for a in q["uploads"]}
        att = answer["attachment_ids"]
        if len(set(att)) != len(att) or any(a not in uploads for a in att):
            raise ValueError("附件不屬於這個問題。")
        if submit and not sel and not answer["text"].strip() and not att:
            raise ValueError("請選擇選項、輸入回答或加入附件。")
        return answer

    def save_draft(self, qid: str, raw: Any) -> None:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                return
            q["draft"] = self._validated(q, raw, False)
            self._persist([q if old["id"] == qid else old for old in self._questions])
        # Drafts do not emit "change": the UI that saved it is already current.

    def answer(self, qid: str, raw: Any) -> dict[str, Any]:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                raise ValueError("這題已處理，不能重複提交。")
            q["answer"] = self._validated(q, raw, True)
            q["draft"] = copy.deepcopy(q["answer"])
            q["status"] = "answered"
            q["resolved_at"] = _now()
            self._running.add(self._chat_key(q["owner"], q["work_id"]))
            self._replace(q)
            return q

    def cancel(self, qid: str) -> dict[str, Any]:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                raise ValueError("這題已處理。")
            q["status"] = "cancelled"
            q["resolved_at"] = _now()
            self._running.discard(self._chat_key(q["owner"], q["work_id"]))
            self._replace(q)
            return q

    def add_upload(self, qid: str, name: str, data: bytes) -> dict[str, Any]:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                raise ValueError("這題已處理。")
            if len(q["uploads"]) >= 8:
                raise ValueError("每題最多 8 個附件，請先移除不需要的附件。")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("每個附件上限為 5 MB。")
            if sum(a["size"] for a in q["uploads"]) + len(data) > MAX_TOTAL_BYTES:
                raise ValueError("回答附件總量上限為 20 MB。")
            meta = self._save_asset(name, data, guess_mime(name, data))
            q["uploads"].append(meta)
            q["draft"]["attachment_ids"].append(meta["id"])
            self._persist([q if old["id"] == qid else old for old in self._questions])
            return meta

    def remove_upload(self, qid: str, aid: str) -> None:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                raise ValueError("這題已處理。")
            if not any(a["id"] == aid for a in q["uploads"]):
                return
            q["uploads"] = [a for a in q["uploads"] if a["id"] != aid]
            q["draft"]["attachment_ids"] = [i for i in q["draft"]["attachment_ids"] if i != aid]
            self._persist([q if old["id"] == qid else old for old in self._questions])
            (self.assets / aid).unlink(missing_ok=True)

    def named_path(self, aid: str, name: str) -> Path:
        """A copy of an attachment under its original file name (data/attachments/named/<id>/<name>),
        so agents can open it directly with their own file tools."""
        safe = Path(name).name.replace(":", "_") or "attachment"
        target = self.assets / "named" / aid / safe
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copyfile(self.assets / aid, target)
        return target

    def asset(self, qid: str, aid: str, owner: str | None = None, answers_only: bool = False) -> tuple[dict[str, Any], bytes]:
        q = self.get(qid, owner)
        if answers_only:
            chosen = set((q.get("answer") or {}).get("attachment_ids", []))
            pool = [a for a in q["uploads"] if a["id"] in chosen]
        else:
            pool = q["images"] + [i for o in q["options"] for i in o["images"]] + q["uploads"]
        for meta in pool:
            if meta["id"] == aid:
                return meta, (self.assets / aid).read_bytes()
        raise KeyError("找不到附件。")
