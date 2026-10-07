"""Read Claude Desktop's conversation cache without opening a writable database.

Hub, legacy Chat and Cowork cache formats are normalized independently.
Cache eviction is not evidence of deletion. Never fetch missing history.
"""
from pathlib import Path
import tempfile
import shutil


def signature(root):
    if not root.is_dir():
        raise OSError("Desktop IndexedDB unavailable")
    return tuple(sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                        for p in root.rglob("*") if p.is_file() and
                        (".blob" in str(p.parent) or p.suffix in (".log", ".ldb", ".sst")
                         or p.name.startswith("MANIFEST-") or p.name == "CURRENT")))


def read_cache(root):
    from agentdock._vendor.chromium.ccl_chromium_indexeddb import WrappedIndexDB

    before = signature(root)
    # Parse a disposable copy: no locks or recovery writes against the live DB.
    with tempfile.TemporaryDirectory(prefix="agentdock-desktop-") as temp:
        dest = Path(temp)
        for relative, _, _ in before:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        if signature(root) != before:
            raise OSError("Desktop cache changed during snapshot; retrying")
        db = WrappedIndexDB(dest / "https_claude.ai_0.indexeddb.leveldb",
                            dest / "https_claude.ai_0.indexeddb.blob")
        try:
            if "claude-conversation-store" not in db:
                raise ValueError("Desktop conversation store unavailable")
            store = db["claude-conversation-store"]
            meta = {r.key.raw_key: r for r in store["meta"].iterate_records()}
            values = []
            for r in store["trees"].iterate_records():
                if not r.is_live or not isinstance(r.value, dict):
                    continue
                m = meta.get(r.key.raw_key)
                if m and (not m.is_live or (isinstance(m.value, dict) and m.value.get("tombstone"))):
                    continue
                values.append(r.value)
            return values, before
        finally:
            db.close()


def _chat_rows(value):
    tree = value.get("tree") or {}
    if tree.get("kind") == "hub_transcript":
        rows = tree.get("messages")
    elif value.get("product") == "chat":
        rows = tree.get("chat_messages")
        # Legacy trees retain sibling branches. Follow the selected leaf only.
        leaf = tree.get("current_leaf_message_uuid")
        if leaf and isinstance(rows, list):
            by_id = {m.get("uuid"): m for m in rows}
            path, seen = [], set()
            while leaf in by_id and leaf not in seen:
                seen.add(leaf)
                m = by_id[leaf]
                path.append(m)
                leaf = m.get("parent_message_uuid") or tree.get("parentByChildUuid", {}).get(leaf)
            rows = list(reversed(path))
    else:
        raise ValueError("Unsupported Desktop chat format")
    if not isinstance(rows, list):
        raise ValueError("Desktop messages unavailable")
    return rows


def load_assets(root, values):
    from agentdock.desktop_assets import asset_url, cached_images
    urls = set()
    for value in values:
        if value.get("product") not in ("hub", "chat"):
            continue
        try:
            for row in _chat_rows(value):
                for item in row.get("files_v2") or []:
                    if not isinstance(item, dict) or item.get("file_kind") != "image":
                        continue
                    for kind in ("preview_asset", "thumbnail_asset"):
                        asset = item.get(kind)
                        if isinstance(asset, dict):
                            urls.add(asset_url(asset.get("url")))
        except (ValueError, TypeError, AttributeError):
            continue
    return cached_images(root.parent / "Cache" / "Cache_Data", urls)


def references(value):
    from agentdock.chat_sync import question_refs, result_ids
    tree = value.get("tree") or {}
    if tree.get("kind") == "cowork_remote":
        rows = [e.get("payload", {}) for e in tree.get("events", []) if e.get("kind") == "message"]
    else:
        rows = _chat_rows(value)
    refs = []
    for row in rows:
        if row.get("parent_tool_use_id") or row.get("isSynthetic") or row.get("isMeta"):
            continue
        blocks = row.get("message", row).get("content", [])
        for b in blocks if isinstance(blocks, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                refs.extend(question_refs(b.get("name", ""), b.get("input")))
            elif b.get("type") == "tool_result":
                refs.extend(result_ids(b.get("content")))
    return refs


def messages(value, assets=None):
    """Normalize chat and Cowork records; preserve stable native identities."""
    from agentdock.chat_sync import event, iso, text_content, image_content, claude_record
    from agentdock.desktop_assets import attachment_images

    tree = value.get("tree") or {}
    if value.get("product") not in ("hub", "chat", "cowork"):
        return None
    sid = value["conversationUuid"]
    if not isinstance(sid, str) or not sid:
        raise ValueError("Desktop conversation ID missing")
    output = []
    if value.get("product") == "cowork":
        if tree.get("kind") != "cowork_remote" or not isinstance(tree.get("events"), list):
            raise ValueError("Unsupported Cowork cache format")
        state = {}
        # Events are ordered by native sequence; results finalize the last assistant.
        for item in sorted(tree["events"], key=lambda e: e.get("seq", 0)):
            if item.get("kind") != "message":
                continue
            row = dict(item.get("payload") or {})
            row.setdefault("timestamp", row.get("created_at") or item.get("serverCreatedAt"))
            entries, _ = claude_record(row, state)
            output.extend((e, e.get("finished_at", e["created_at"])) for e in entries)
    else:
        for m in _chat_rows(value):
            if m.get("sender") not in ("human", "assistant"):
                continue
            uid, timestamp = m.get("uuid"), m.get("created_at")
            if not isinstance(uid, str) or not isinstance(timestamp, (str, int, float)):
                raise ValueError("Desktop message identity/time missing")
            blocks = m.get("content")
            text = text_content(blocks) or (m.get("text") if isinstance(m.get("text"), str) else "")
            images = (image_content(blocks) + attachment_images(m, assets or {}))[:8]
            if not text and not images:
                continue
            kind = "user_message" if m["sender"] == "human" else (
                "completed" if m.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens") else "progress")
            updated = m.get("updated_at")
            changed = iso(updated) if isinstance(updated, (str, int, float)) else iso(timestamp)
            entry = event(uid, kind, text, timestamp, finished_at=changed, images=images)
            if m.get("stop_reason") == "tool_use":
                entry["phase"] = "commentary"
            output.append((entry, changed))
    title = tree.get("name")
    if not isinstance(title, str) or not title:
        title = next((e["text"].splitlines()[0][:160] for e, _ in output
                      if e["kind"] == "user_message" and e["text"]), "Claude Desktop")
    return sid, title, output
