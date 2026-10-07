"""Bounded, read-only image lookup in Chromium's local block/simple cache.

Layout: Chromium net/disk_cache/simple/simple_entry_format.h.
Only explicitly referenced Claude attachment URLs are considered; no network I/O.
"""
import base64
import hashlib
import re
import struct
import zlib

LIMIT = 5 * 1024 * 1024
HEADER = 0xfcfb6d1ba7725c30
EOF = 0xf4fa6f45970d41d8


def cache_stamp(folder):
    if not folder.is_dir():
        return ()
    return tuple(sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                        for p in folder.iterdir() if p.is_file()))


def asset_url(value):
    if not isinstance(value, str):
        return ""
    if value.startswith("/") and not value.startswith("//"):
        return "https://claude.ai" + value
    return value if value.startswith("https://claude.ai/") else ""


def cached_images(folder, urls):
    from agentdock.qa.store import image_mime
    urls = set(filter(None, urls))
    found = {}
    if not urls or not folder.is_dir():
        return found
    if (folder / "data_0").is_file():
        from agentdock._vendor.chromium.ccl_chromium_cache import ChromiumBlockFileCache
        try:
            with ChromiumBlockFileCache(folder) as cache:
                for key in cache.cache_keys():
                    if key.url not in urls:
                        continue
                    entry = cache[key.raw_key]
                    size = entry.data_sizes[1]
                    if not 0 < size <= LIMIT:
                        continue
                    with cache.get_stream_for_addr(entry.data_addrs[1]) as stream:
                        data = stream.read(size)
                    mime = image_mime(data)
                    if mime and len(data) == size:
                        found[key.url] = f"data:{mime};base64," + base64.b64encode(data).decode()
        except (OSError, ValueError, struct.error, UnicodeError, IndexError):
            pass
        return found
    for path in folder.glob("*_0"):
        if not re.fullmatch(r"[0-9a-f]{16}_0", path.name):
            continue
        try:
            with path.open("rb") as stream:
                header = stream.read(24)
                magic, version, length, _ = struct.unpack("<QIII4x", header)
                if magic != HEADER or version != 9 or not 0 < length <= 16384:
                    continue
                key = stream.read(length)
                # Partitioned HTTP keys end with the resource URL.
                raw = key.decode("latin1")
                url = next((u for u in urls if raw == u or raw.endswith(" " + u)
                            or re.fullmatch(r"(?:[01]/)?\d+/" + re.escape(u), raw)), None)
                if not url:
                    continue
                rest = stream.read(LIMIT + 65537)
            if len(rest) > LIMIT + 65536 or len(rest) < 48:
                continue
            magic, flags, crc, size = struct.unpack("<QIII4x", rest[-24:])
            if magic != EOF or size > 65536:
                continue
            digest_size = 32 if flags & 2 else 0
            end = len(rest) - 48 - size - digest_size
            if not 0 < end <= LIMIT:
                continue
            if digest_size and rest[-56:-24] != hashlib.sha256(key).digest():
                continue
            magic1, flags1, crc1, _ = struct.unpack("<QIII4x", rest[end:end + 24])
            data = rest[:end]
            if magic1 != EOF or (flags1 & 1 and zlib.crc32(data) != crc1):
                continue
            metadata = rest[end + 24:end + 24 + size]
            if flags & 1 and zlib.crc32(metadata) != crc:
                continue
            mime = image_mime(data)
            if mime:
                found[url] = f"data:{mime};base64," + base64.b64encode(data).decode()
        except (OSError, ValueError, struct.error):
            continue
    return found


def attachment_images(message, assets):
    result = []
    for item in message.get("files_v2") or []:
        if not isinstance(item, dict) or item.get("file_kind") != "image":
            continue
        urls = [asset_url((item.get(k) or {}).get("url"))
                for k in ("preview_asset", "thumbnail_asset") if isinstance(item.get(k), dict)]
        result.append({"name": str(item.get("file_name") or "圖片")[:200],
                       "data_url": next((assets[u] for u in urls if u in assets), "")})
        if len(result) == 8:
            break
    return result
