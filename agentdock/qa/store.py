"""Persistent question store. Same JSON layout as AgentDock 0.5 (questions.json + attachments/)."""
from __future__ import annotations

import base64
import copy
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentdock.files import atomic_json, atomic_write, load_json
from agentdock.qa.schema import Draft, empty_draft

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
                    old = copy.deepcopy(old)
                    old["answer"] = checked[old["id"]]
                    old["draft"] = copy.deepcopy(old["answer"])
                    old["status"] = "answered"
                    old["resolved_at"] = now
                updated.append(old)
            self._persist(updated)
        self._emit("change")

    def cancel_group(self, ids: list[str]) -> None:
        with self._lock:
            now = _now()
            updated = []
            for old in self._questions:
                if old["id"] in ids and old["status"] == "pending":
                    old = {**copy.deepcopy(old), "status": "cancelled", "resolved_at": now}
                updated.append(old)
            self._persist(updated)
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
            self._replace(q)
            return q

    def cancel(self, qid: str) -> dict[str, Any]:
        with self._lock:
            q = self.get(qid)
            if q["status"] != "pending":
                raise ValueError("這題已處理。")
            q["status"] = "cancelled"
            q["resolved_at"] = _now()
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
