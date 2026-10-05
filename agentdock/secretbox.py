"""Carry data/secrets.json to another computer as one password-protected file (.adsecrets).

scrypt (n=2^15, r=8, p=1) turns the password into a 256-bit key; AES-256-GCM encrypts and authenticates the JSON,
so a wrong password or an edited file is refused instead of importing garbage. No PySide6 here.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

FORMAT = "agentdock-secrets"
N, R, P = 2 ** 15, 8, 1


def _key(password: str, salt: bytes, n: int = N, r: int = R, p: int = P) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def seal(values: dict[str, str], password: str) -> str:
    if not password:
        raise ValueError("請設定密碼。")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    data = AESGCM(_key(password, salt)).encrypt(nonce, json.dumps(values, ensure_ascii=False).encode("utf-8"),
                                               FORMAT.encode())
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return json.dumps({"format": FORMAT, "version": 1, "kdf": {"name": "scrypt", "n": N, "r": R, "p": P, "salt": b64(salt)},
                       "cipher": "aes-256-gcm", "nonce": b64(nonce), "data": b64(data)}, indent=1)


def open_sealed(text: str, password: str) -> dict[str, str]:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        doc: dict[str, Any] = json.loads(text)
        assert doc.get("format") == FORMAT and doc.get("cipher") == "aes-256-gcm"
        kdf = doc["kdf"]
        key = _key(password, base64.b64decode(kdf["salt"]), int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
        nonce, data = base64.b64decode(doc["nonce"]), base64.b64decode(doc["data"])
    except Exception:
        raise ValueError("這不是 AgentDock 匯出的祕密檔。") from None
    try:
        values = json.loads(AESGCM(key).decrypt(nonce, data, FORMAT.encode()).decode("utf-8"))
    except InvalidTag:
        raise ValueError("密碼不對，或檔案已被修改。") from None
    if not isinstance(values, dict):
        raise ValueError("檔案內容不正確。")
    return {str(k): str(v) for k, v in values.items()}


def merge(current: dict[str, str], incoming: dict[str, str]) -> tuple[dict[str, str], list[str], list[str]]:
    """Only fill what this computer lacks: values here (e.g. a local path) are never overwritten.
    Returns (values to add, added names, names kept as they are here)."""
    add = {k: v for k, v in incoming.items() if v and not current.get(k)}
    kept = sorted(k for k, v in incoming.items() if current.get(k) and current.get(k) != v)
    return add, sorted(add), kept
