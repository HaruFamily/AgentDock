"""額度取數字（原 TokenGauge 的資料部分；介面在 agentdock/ui/card.py，由 QuotaModel 在主行程呼叫）。

支援：Claude Code、Codex（多個 ChatGPT workspace）、Grok（經 opencode 登入）。
只讀本機各 CLI 已存在的登入狀態，對各家非公開用量端點發請求；不需要 API key 或密碼。
這些端點隨時可能改版，失效時卡片會顯示錯誤而不是假數字。

除錯：python -m agentdock.tools.tokengauge.gauge --once（取一次數字印出 JSON）／--selftest（解析器自我測試）。
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ────────────────────────────── 路徑與設定 ──────────────────────────────

APP = "TokenGauge"
HOME = Path(os.environ.get("USERPROFILE") or Path.home())
# AgentDock 以 configure() 指到 data\tokengauge；以下是單獨除錯時的預設位置。
APPDATA = Path(os.environ.get("APPDATA") or (HOME / ".config")) / APP
CONFIG_FILE = APPDATA / "config.json"
CODEX_STORE = APPDATA / "codex_accounts"   # 每個 ChatGPT workspace 一份登入副本
LOG_FILE = APPDATA / "error.log"
LATEST_FILE = APPDATA / "latest.json"       # 最近一次的結果，下次開啟時先顯示


def configure(folder: Path) -> None:
    """AgentDock 在同一個行程裡使用這個模組時，改用它的資料夾。"""
    global APPDATA, CONFIG_FILE, CODEX_STORE, LOG_FILE, LATEST_FILE
    APPDATA = Path(folder)
    CONFIG_FILE = APPDATA / "config.json"
    CODEX_STORE = APPDATA / "codex_accounts"
    LOG_FILE = APPDATA / "error.log"
    LATEST_FILE = APPDATA / "latest.json"


def save_latest(results: list, at: float) -> None:
    try:
        APPDATA.mkdir(parents=True, exist_ok=True)
        write_atomic(LATEST_FILE, json.dumps({"at": at, "results": [asdict(r) for r in results]}, ensure_ascii=False))
    except Exception:
        log_error("save_latest")


def load_latest() -> tuple[float | None, list]:
    try:
        doc = json.loads(LATEST_FILE.read_text("utf-8"))
    except Exception:
        return None, []
    out = []
    for r in doc.get("results", []):
        accounts = [Account(a.get("label"), [Window(**w) for w in a.get("windows", [])], a.get("note"),
                            a.get("identity"), a.get("plan")) for a in r.get("accounts", [])]
        out.append(ProviderResult(r["key"], r["name"], accounts, r.get("error"), r.get("stale", False), r.get("missing", False)))
    return doc.get("at"), out

CLAUDE_CREDS = HOME / ".claude" / ".credentials.json"
CODEX_AUTH = Path(os.environ.get("CODEX_HOME") or (HOME / ".codex")) / "auth.json"


def _opencode_data() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else HOME / ".local" / "share") / "opencode"


OPENCODE_AUTH = _opencode_data() / "auth.json"
OPENCODE_DB = Path(os.environ.get("OPENCODE_DB") or (_opencode_data() / "opencode.db"))

REFRESH_SKEW = 300          # token 到期前 5 分鐘就先換發
UA = "claude-code/2.0.32"   # Anthropic 端點會擋掉 python-urllib 的預設 UA

DEFAULT_CONFIG = {
    "show": "remaining",          # 卡片固定顯示剩餘（舊 TokenGauge 的設定會被重設）
    "poll_minutes": 5,            # 幾分鐘向伺服器取一次數字（右鍵選單「額度更新頻率」）
    "codex_labels": {},           # {account_id: "自訂名稱"}
    "codex_hidden": [],           # 不想看的 workspace account_id
    "providers": {"claude": True, "codex": True, "grok": True},
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(CONFIG_FILE.read_text("utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APPDATA.mkdir(parents=True, exist_ok=True)
        write_atomic(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception:
        log_error("save_config")


def log_error(where: str) -> None:
    try:
        APPDATA.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n{traceback.format_exc()}")
    except Exception:
        pass

# ────────────────────────────── 資料模型 ──────────────────────────────

ROLLING, WEEKLY, MONTHLY = "5h", "week", "month"


@dataclass
class Window:
    kind: str                    # 5h / week / month
    used: float                  # 已用百分比 0–100
    resets_at: float | None      # Unix 秒


@dataclass
class Account:
    label: str | None
    windows: list[Window] = field(default_factory=list)
    note: str | None = None      # 沒有數字時的說明
    identity: str | None = None  # 用來判斷兩條途徑是否同一個帳號
    plan: str | None = None      # 訂閱方案，例如 Max 20x、Plus、Enterprise


@dataclass
class ProviderResult:
    key: str
    name: str
    accounts: list[Account] = field(default_factory=list)
    error: str | None = None
    stale: bool = False          # 這次失敗，顯示的是上次的數字
    missing: bool = False        # 本機沒裝這家，介面直接隱藏


class NeedLogin(Exception):
    """需要使用者重新登入。"""


class Temporary(Exception):
    """暫時性錯誤（網路、伺服器 5xx），下次輪詢會再試。"""


class NotInstalled(Exception):
    """本機找不到這家的任何登入。"""

# ────────────────────────────── 共用工具 ──────────────────────────────


def http(method: str, url: str, headers: dict | None = None, json_body=None, form=None,
         timeout: int = 20) -> tuple[int, str]:
    data = None
    h = {"User-Agent": UA, "Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except (urllib.error.URLError, OSError) as e:
        raise Temporary(f"連線失敗（{getattr(e, 'reason', e)}）")


def check_status(status: int, body: str, who: str) -> None:
    if 200 <= status < 300:
        return
    if status >= 500 or status == 429:
        raise Temporary(f"{who} 伺服器回 {status}，稍後重試")
    raise RuntimeError(f"{who} 回 {status}：{body[:120]}")


def read_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, "utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:      # Windows：對方剛好開著檔案
            time.sleep(0.2)
    tmp.unlink(missing_ok=True)


def update_json(path: Path, mutate) -> None:
    """重新讀取 → 修改 → 原子寫回，盡量不蓋掉 CLI 同時寫入的其他欄位。"""
    root = read_json(path)
    if not isinstance(root, dict):
        return
    mutate(root)
    write_atomic(path, json.dumps(root, ensure_ascii=False, indent=2))


def jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def parse_time(v) -> float | None:
    """ISO8601 字串、Unix 秒或毫秒 → Unix 秒。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    try:
        s = str(v).replace("Z", "+00:00")
        # Python 3.10 以前不吃超過 6 位的小數秒
        if "." in s:
            head, _, tail = s.partition(".")
            n = 0
            while n < len(tail) and tail[n].isdigit():
                n += 1
            s = f"{head}.{tail[:n][:6].ljust(6, '0')}{tail[n:]}"
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))

# ────────────────────────────── OAuth token 來源 ──────────────────────────────
# 每個來源只需要會 load() 與 save()，換發與重試邏輯全家共用。


@dataclass
class Tokens:
    access: str
    refresh: str
    expires: float | None   # Unix 秒；None 表示不知道


class TokenSource:
    def load(self) -> Tokens | None: ...
    def save(self, t: Tokens, extra: dict) -> None: ...


class ClaudeFile(TokenSource):
    def __init__(self, path: Path):
        self.path = path

    def load(self):
        o = (read_json(self.path) or {}).get("claudeAiOauth")
        if not isinstance(o, dict) or not o.get("accessToken"):
            return None
        return Tokens(o["accessToken"], o.get("refreshToken") or "", parse_time(o.get("expiresAt")))

    def save(self, t, extra):
        def m(root):
            node = root.setdefault("claudeAiOauth", {})
            node.update(accessToken=t.access, refreshToken=t.refresh,
                        expiresAt=int((t.expires or time.time() + 3600) * 1000))
        update_json(self.path, m)


class CodexFile(TokenSource):
    """~/.codex/auth.json 或本工具保存的 workspace 副本（格式相同）。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self):
        t = (read_json(self.path) or {}).get("tokens")
        if not isinstance(t, dict) or not t.get("access_token"):
            return None
        exp = jwt_claims(t["access_token"]).get("exp")
        return Tokens(t["access_token"], t.get("refresh_token") or "", float(exp) if exp else None)

    def account_id(self) -> str:
        root = read_json(self.path) or {}
        t = root.get("tokens") or {}
        return t.get("account_id") or codex_account_id(t.get("access_token", ""))

    def save(self, t, extra):
        def m(root):
            node = root.setdefault("tokens", {})
            node["access_token"] = t.access
            node["refresh_token"] = t.refresh
            if extra.get("id_token"):
                node["id_token"] = extra["id_token"]
            root["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        update_json(self.path, m)


class OpencodeFileNode(TokenSource):
    """opencode 舊版 auth.json 的某一節點：{type, access, refresh, expires(ms)}。"""

    def __init__(self, node: str):
        self.node = node

    def raw(self) -> dict:
        v = (read_json(OPENCODE_AUTH) or {}).get(self.node)
        return v if isinstance(v, dict) else {}

    def load(self):
        v = self.raw()
        if not v.get("access") and not v.get("refresh"):
            return None
        return Tokens(v.get("access") or "", v.get("refresh") or "", parse_time(v.get("expires")))

    def save(self, t, extra):
        def m(root):
            node = root.setdefault(self.node, {})
            node.update(access=t.access, refresh=t.refresh,
                        expires=int((t.expires or time.time() + 3600) * 1000))
        update_json(OPENCODE_AUTH, m)


class OpencodeDbRow(TokenSource):
    """新版 opencode 把登入存在 opencode.db 的 credential 表。"""

    def __init__(self, row_id):
        self.row_id = row_id

    def raw(self) -> dict:
        for r in opencode_rows(None):
            if r["id"] == self.row_id:
                return r["value"]
        return {}

    def load(self):
        v = self.raw()
        if not v.get("access") and not v.get("refresh"):
            return None
        return Tokens(v.get("access") or "", v.get("refresh") or "", parse_time(v.get("expires")))

    def save(self, t, extra):
        opencode_update(self.row_id, {"access": t.access, "refresh": t.refresh,
                                      "expires": int((t.expires or time.time() + 3600) * 1000)})


def fresh_token(src: TokenSource, refresher, failed_access: str | None = None) -> str:
    """回傳可用的 access token；快到期（或剛被拒絕）就換發並寫回原處。

    refresher(refresh_token) -> (Tokens, extra_dict)
    """
    cur = src.load()
    if cur is None:
        raise NeedLogin("找不到登入資料")
    if failed_access is not None and cur.access != failed_access:
        return cur.access                 # CLI 自己已經換發過了
    fresh_enough = cur.access and cur.expires and cur.expires > time.time() + REFRESH_SKEW
    if failed_access is None and fresh_enough:
        return cur.access
    if not cur.refresh:
        if failed_access is None:
            return cur.access             # 沒得換，讓端點判斷
        raise NeedLogin("登入已失效")
    try:
        new, extra = refresher(cur.refresh)
    except NeedLogin:
        latest = src.load()               # 可能輸給了 CLI 的並發換發
        if latest and latest.access != cur.access:
            return latest.access
        raise
    if not new.refresh:
        new.refresh = cur.refresh
    try:
        src.save(new, extra)
    except Exception:
        log_error("save token")           # 寫不回去也沒關係，這輪先用記憶體裡的
    return new.access


def call_with_token(src: TokenSource, refresher, call):
    """帶 token 呼叫；遇到 401/403 先換發再試一次。"""
    tok = fresh_token(src, refresher)
    status, body = call(tok)
    if status in (401, 403):
        tok = fresh_token(src, refresher, failed_access=tok)
        status, body = call(tok)
    return status, body


def _oauth_result(status, body, who) -> tuple[Tokens, dict]:
    if status in (400, 401):
        raise NeedLogin(f"{who} 登入已失效，請重新登入")
    check_status(status, body, f"{who} 換發")
    j = json.loads(body)
    if not j.get("access_token"):
        raise Temporary(f"{who} 換發回應缺少 access_token")
    exp = j.get("expires_in")
    if not exp:
        exp_claim = jwt_claims(j["access_token"]).get("exp")
        expires = float(exp_claim) if exp_claim else time.time() + 3600
    else:
        expires = time.time() + float(exp)
    return Tokens(j["access_token"], j.get("refresh_token") or "", expires), j

# ────────────────────────────── opencode 共用 ──────────────────────────────


def opencode_rows(integration: str | None) -> list[dict]:
    if not OPENCODE_DB.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{OPENCODE_DB.as_posix()}?mode=ro", uri=True, timeout=3)
        try:
            if integration is None:
                cur = con.execute("SELECT id, label, value, active, integration_id FROM credential")
            else:
                cur = con.execute("SELECT id, label, value, active, integration_id FROM credential "
                                  "WHERE integration_id = ? ORDER BY time_created", (integration,))
            rows = []
            for rid, label, value, active, integ in cur.fetchall():
                try:
                    v = json.loads(value)
                except Exception:
                    continue
                if isinstance(v, dict):
                    rows.append({"id": rid, "label": label, "value": v, "active": active == 1,
                                 "integration": integ})
            rows.sort(key=lambda r: not r["active"])
            return rows
        finally:
            con.close()
    except Exception:
        return []


def opencode_update(row_id, patch: dict) -> None:
    if not OPENCODE_DB.exists():
        return
    con = sqlite3.connect(OPENCODE_DB.as_posix(), timeout=3, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT value FROM credential WHERE id = ?", (row_id,)).fetchone()
        if not row:
            con.execute("ROLLBACK")
            return
        merged = {**json.loads(row[0]), **patch}
        con.execute("UPDATE credential SET value = ?, time_updated = ? WHERE id = ?",
                    (json.dumps(merged), int(time.time() * 1000), row_id))
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        log_error("opencode_update")
    finally:
        con.close()


def opencode_logins(integration: str) -> list[tuple[str | None, TokenSource, dict]]:
    """opencode 代存的 OAuth 登入：[(label, source, raw_value)]。"""
    found = []
    seen_refresh = set()
    for r in opencode_rows(integration):
        v = r["value"]
        if v.get("type") not in (None, "oauth") or not (v.get("access") or v.get("refresh")):
            continue
        label = r["label"] if r["label"] and r["label"] != "Default" else None
        found.append((label, OpencodeDbRow(r["id"]), v))
        seen_refresh.add(v.get("refresh"))
    node = OpencodeFileNode(integration)
    v = node.raw()
    if (v.get("access") or v.get("refresh")) and v.get("type") in (None, "oauth") \
            and v.get("refresh") not in seen_refresh:
        found.append((None, node, v))
    return found

# ────────────────────────────── 多途徑合併 ──────────────────────────────


def collect_routes(key, name, routes, missing_msg) -> ProviderResult:
    """routes: [(label, identity, fn)]。全部嘗試；任一成功就忽略其他失敗；同帳號合併。"""
    if not routes:
        raise NotInstalled(missing_msg)
    got: list[Account] = []
    errors: list[str] = []
    for label, identity, fn in routes:
        try:
            acc: Account = fn()
            acc.label = acc.label or label
            acc.identity = acc.identity or identity
            got.append(acc)
        except (NeedLogin, Temporary, RuntimeError) as e:
            errors.append(f"{label or name}：{e}" if len(routes) > 1 else str(e))
        except Exception as e:
            log_error(f"{name} route {label}")
            errors.append(f"{label or name}：{type(e).__name__}: {e}")
    if not got:
        return ProviderResult(key, name, error=errors[0] if errors else "取不到資料")

    def fingerprint(a: Account):
        return tuple((w.kind, round(w.used, 1), int((w.resets_at or 0) // 60)) for w in a.windows) or None

    distinct: list[Account] = []
    for a in got:
        twin = None
        for d in distinct:
            if a.identity and d.identity:
                if a.identity == d.identity:
                    twin = d
            elif fingerprint(a) and fingerprint(a) == fingerprint(d):
                twin = d
            if twin:
                break
        if twin is None:
            distinct.append(a)
    return ProviderResult(key, name, accounts=distinct)

# ────────────────────────────── Claude Code ──────────────────────────────

CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code 公開 OAuth client
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def claude_refresh(refresh: str):
    st, body = http("POST", CLAUDE_TOKEN_URL, form={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": CLAUDE_CLIENT_ID})
    return _oauth_result(st, body, "Claude")


def claude_parse(body: str) -> Account:
    root = json.loads(body)
    wins = []
    for k, kind in (("five_hour", ROLLING), ("seven_day", WEEKLY)):
        w = root.get(k)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            wins.append(Window(kind, clamp(w["utilization"]), parse_time(w.get("resets_at"))))
    if not wins:
        return Account(None, note="回應裡沒有額度窗口")
    return Account(None, wins)


CLAUDE_PLANS = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise", "free": "Free"}


def claude_plan(oauth: dict) -> str | None:
    """Claude Code 登入檔裡的 subscriptionType / rateLimitTier → 方案名稱。"""
    tier = str(oauth.get("rateLimitTier") or "").lower()
    for k, name in (("max_20x", "Max 20x"), ("max_5x", "Max 5x")):
        if k in tier:
            return name
    sub = oauth.get("subscriptionType")
    if isinstance(sub, str) and sub:
        return CLAUDE_PLANS.get(sub.lower(), sub.title())
    return None


def claude_collect() -> ProviderResult:
    def via(src: TokenSource):
        def run():
            st, body = call_with_token(src, claude_refresh, lambda tok: http(
                "GET", CLAUDE_USAGE_URL,
                headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"}))
            if st in (401, 403):
                raise NeedLogin("登入已失效，請在 Claude Code 執行 /login")
            check_status(st, body, "Claude")
            acc = claude_parse(body)
            if isinstance(src, ClaudeFile):
                acc.plan = claude_plan((read_json(src.path) or {}).get("claudeAiOauth") or {})
            return acc
        return run

    routes = []
    if CLAUDE_CREDS.exists():
        routes.append(("Claude Code", None, via(ClaudeFile(CLAUDE_CREDS))))
    for label, src, _ in opencode_logins("anthropic"):
        routes.append((label or "opencode", None, via(src)))
    return collect_routes("claude", "Claude Code", routes, "找不到 Claude Code 登入")

# ────────────────────────────── Codex（多 workspace） ──────────────────────────────
#
# ChatGPT 的個人版與企業版是不同 workspace，各有 account_id，而 ~/.codex/auth.json 一次只存一個。
# 做法：每次輪詢都把目前 CLI 登入的 workspace 複製一份到 %APPDATA%/TokenGauge/codex_accounts/<id>.json。
# 之後你用 `codex login` 換到另一個 workspace，舊的那份副本仍保有自己的 refresh token，繼續獨立追蹤。
# 注意：換 workspace 時用 `codex login` 直接登入即可，不要先 `codex logout`（可能會撤銷舊 session）。

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"   # Codex CLI 公開 OAuth client
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

PLAN_NAMES = {"plus": "Plus", "pro": "Pro", "team": "Business", "business": "Business",
              "enterprise": "Enterprise", "edu": "Edu", "free": "Free", "go": "Go"}


def codex_account_id(token: str) -> str:
    return (jwt_claims(token).get("https://api.openai.com/auth") or {}).get("chatgpt_account_id") or ""


def codex_plan(token: str) -> str | None:
    p = (jwt_claims(token).get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type")
    return PLAN_NAMES.get(p, p.title()) if isinstance(p, str) and p else None


def codex_refresh(refresh: str):
    st, body = http("POST", CODEX_TOKEN_URL, json_body={
        "client_id": CODEX_CLIENT_ID, "grant_type": "refresh_token", "refresh_token": refresh})
    return _oauth_result(st, body, "Codex")


def codex_kind(seconds: int) -> str:
    if seconds <= 86400:
        return ROLLING
    if seconds <= 864000:
        return WEEKLY
    return MONTHLY


def codex_parse(body: str) -> Account:
    root = json.loads(body)
    rl = root.get("rate_limit") or {}
    wins = []
    for k in ("primary_window", "secondary_window"):
        w = rl.get(k)
        if isinstance(w, dict) and isinstance(w.get("limit_window_seconds"), int) \
                and isinstance(w.get("used_percent"), (int, float)):
            wins.append(Window(codex_kind(w["limit_window_seconds"]), clamp(w["used_percent"]),
                               parse_time(w.get("reset_at"))))
    wins.sort(key=lambda w: [ROLLING, WEEKLY, MONTHLY].index(w.kind))
    acc = Account(None, wins, note=None if wins else "此帳號沒有訂閱額度")
    plan = root.get("plan_type")
    if isinstance(plan, str) and plan:
        acc.plan = acc.label = PLAN_NAMES.get(plan, plan.title())
    return acc


def codex_sync_snapshot() -> str | None:
    """把 CLI 目前登入的 workspace 存（或更新）成副本；回傳其 account_id。"""
    root = read_json(CODEX_AUTH)
    if not isinstance(root, dict) or not isinstance(root.get("tokens"), dict):
        return None
    t = root["tokens"]
    if not t.get("access_token"):
        return None
    aid = t.get("account_id") or codex_account_id(t["access_token"])
    if not aid:
        return None
    try:
        CODEX_STORE.mkdir(parents=True, exist_ok=True)
        snap = CODEX_STORE / f"{aid}.json"
        old = read_json(snap) or {}
        if (old.get("tokens") or {}).get("refresh_token") != t.get("refresh_token") \
                or (old.get("tokens") or {}).get("access_token") != t.get("access_token"):
            write_atomic(snap, json.dumps({"tokens": t, "last_refresh": root.get("last_refresh")},
                                          ensure_ascii=False, indent=2))
    except Exception:
        log_error("codex snapshot")
    return aid


def codex_collect(cfg: dict) -> ProviderResult:
    labels = cfg.get("codex_labels") or {}
    hidden = set(cfg.get("codex_hidden") or [])

    def via(src: TokenSource, account_id: str | None, who: str):
        def run():
            aid = account_id
            if not aid:
                cur = src.load()
                aid = codex_account_id(cur.access) if cur else ""
            if not aid:
                raise NeedLogin(f"{who} 缺少帳號資訊，請重新登入")
            st, body = call_with_token(src, codex_refresh, lambda tok: http(
                "GET", CODEX_USAGE_URL,
                headers={"Authorization": f"Bearer {tok}", "chatgpt-account-id": aid}))
            if st in (401, 403):
                raise NeedLogin(f"{who} 登入已失效，請重新 codex login 到此 workspace")
            check_status(st, body, "Codex")
            acc = codex_parse(body)
            if not acc.plan:
                cur = src.load()
                acc.plan = codex_plan(cur.access) if cur else None
            acc.label = acc.label or acc.plan
            if aid in labels:
                acc.label = labels[aid]
            acc.identity = aid
            return acc
        return run

    live_id = codex_sync_snapshot()
    routes = []
    if live_id and live_id not in hidden:
        routes.append(("Codex CLI", live_id, via(CodexFile(CODEX_AUTH), live_id, "Codex CLI")))
    if CODEX_STORE.exists():
        for snap in sorted(CODEX_STORE.glob("*.json")):
            aid = snap.stem
            if aid == live_id or aid in hidden:
                continue          # 目前正在用的 workspace 以 CLI 的檔案為準，避免兩邊搶著換發
            routes.append((labels.get(aid) or "已保存的 workspace", aid,
                           via(CodexFile(snap), aid, "已保存的 workspace")))
    for label, src, v in opencode_logins("openai"):
        aid = v.get("accountId") or codex_account_id(v.get("access") or "")
        if aid in hidden:
            continue
        routes.append((label or "opencode", aid or None, via(src, aid or None, "opencode 的 ChatGPT 登入")))

    res = collect_routes("codex", "Codex", routes, "找不到 Codex 登入")
    codex_sync_snapshot()     # CLI 的 token 若剛被換發，同步進副本
    # 同方案的兩個 workspace 名稱相同時，加上 id 尾碼區分
    names = [a.label for a in res.accounts]
    for a in res.accounts:
        if names.count(a.label) > 1 and a.identity and a.identity not in labels:
            a.label = f"{a.label} ·{a.identity[-4:]}"
    return res

# ────────────────────────────── Grok（經 opencode 的 xAI 登入） ──────────────────────────────

XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
GROK_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"


def xai_refresh(refresh: str):
    st, body = http("POST", XAI_TOKEN_URL, form={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": XAI_CLIENT_ID})
    return _oauth_result(st, body, "xAI")


def _val(config: dict, key: str):
    v = config.get(key)
    v = v.get("val") if isinstance(v, dict) else v
    return v if isinstance(v, (int, float)) and v >= 0 else None


def grok_plan(config: dict) -> str | None:
    """帳務回應沒有固定的方案欄位，找得到像方案名稱的欄位才顯示。"""
    for k in ("subscriptionTier", "subscriptionType", "tier", "planName", "plan", "productTier"):
        v = config.get(k)
        if isinstance(v, str) and v.strip():
            v = v.strip().replace("SUBSCRIPTION_TIER_", "").replace("_", " ")
            return v.title() if v.isupper() or v.islower() else v
    if config.get("isUnifiedBillingUser") is True:
        return "SuperGrok"
    return None


def grok_parse(body: str) -> Account:
    acc = _grok_parse(body)
    acc.plan = grok_plan(json.loads(body).get("config") or {})
    return acc


def _grok_parse(body: str) -> Account:
    config = json.loads(body).get("config")
    if not isinstance(config, dict):
        raise RuntimeError("Grok 回應缺少 config")
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    reset = parse_time(period.get("end")) or parse_time(config.get("billingPeriodEnd"))
    wins = []

    weekly = config.get("creditUsagePercent")
    if not isinstance(weekly, (int, float)):
        vals = [p["usagePercent"] for p in config.get("productUsage") or []
                if isinstance(p, dict) and isinstance(p.get("usagePercent"), (int, float))]
        weekly = max(vals) if vals else None
    if weekly is not None:
        wins.append(Window(WEEKLY, clamp(weekly), reset))

    limit = _val(config, "monthlyLimit") or 0
    if limit > 0:
        used = _val(config, "used") or 0
        wins.append(Window(MONTHLY, clamp(used / limit * 100),
                           parse_time(config.get("billingPeriodEnd"))))
    if wins:
        return Account(None, wins)

    unified_weekly = config.get("isUnifiedBillingUser") is True and "WEEKLY" in str(period.get("type", ""))
    amounts = [_val(config, k) for k in ("monthlyLimit", "used", "onDemandCap", "onDemandUsed", "prepaidBalance")]
    if unified_weekly and all(not a for a in amounts):
        return Account(None, [Window(WEEKLY, 0, reset)], note="本週期尚無用量")
    if unified_weekly:
        return Account(None, note="SuperGrok 統一帳單，未回傳用量百分比")
    spent = _val(config, "used") or 0
    return Account(None, note=f"本月已用 ${spent:.2f}，未設上限" if spent else "沒有 Grok 訂閱額度")


def grok_collect() -> ProviderResult:
    def via(src):
        def run():
            st, body = call_with_token(src, xai_refresh, lambda tok: http(
                "GET", GROK_BILLING_URL,
                headers={"Authorization": f"Bearer {tok}", "x-xai-token-auth": "xai-grok-cli"}))
            if st in (401, 403):
                raise NeedLogin("登入已失效，請在 opencode 重新登入 xAI")
            check_status(st, body, "Grok")
            return grok_parse(body)
        return run

    routes = [(label or "xAI", None, via(src)) for label, src, _ in opencode_logins("xai")]
    return collect_routes("grok", "Grok", routes, "找不到 opencode 的 xAI 登入")

# ────────────────────────────── 輪詢 ──────────────────────────────

PROVIDERS = [
    ("claude", "Claude Code", lambda cfg: claude_collect()),
    ("codex", "Codex", codex_collect),
    ("grok", "Grok", lambda cfg: grok_collect()),
]


def collect_one(key, name, fn, cfg) -> ProviderResult:
    try:
        return fn(cfg)
    except NotInstalled as e:
        return ProviderResult(key, name, error=str(e), missing=True)
    except (NeedLogin, Temporary, RuntimeError) as e:
        return ProviderResult(key, name, error=str(e))
    except Exception as e:
        log_error(f"collect {name}")
        return ProviderResult(key, name, error=f"{type(e).__name__}: {e}")


def collect_all(cfg: dict) -> list[ProviderResult]:
    enabled = [(k, n, f) for k, n, f in PROVIDERS if (cfg.get("providers") or {}).get(k, True)]
    with ThreadPoolExecutor(max_workers=len(enabled) or 1) as ex:
        futs = [ex.submit(collect_one, k, n, f, cfg) for k, n, f in enabled]
        return [f.result() for f in futs]

# ────────────────────────────── 顯示格式 ──────────────────────────────

KIND_LABEL = {ROLLING: "5H", WEEKLY: "W", MONTHLY: "M"}


def fmt_countdown(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return "—"
    s = int(ts - (now or time.time()))
    if s <= 0:
        return "已重置"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}D{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{max(m, 1)}m"


# ────────────────────────────── 假資料 / 自我測試 ──────────────────────────────


def demo_results() -> list[ProviderResult]:
    now = time.time()
    return [
        ProviderResult("claude", "Claude Code", [Account(None, [
            Window(ROLLING, 42, now + 2 * 3600 + 13 * 60), Window(WEEKLY, 76, now + 3 * 86400 + 5 * 3600)],
            plan="Max 5x")]),
        ProviderResult("codex", "Codex", [
            Account("Plus", [Window(ROLLING, 12, now + 4 * 3600), Window(WEEKLY, 35, now + 5 * 86400)], plan="Plus"),
            Account("公司", [Window(ROLLING, 93, now + 38 * 60), Window(WEEKLY, 61, now + 2 * 86400)],
                    plan="Enterprise")]),
        ProviderResult("grok", "Grok", [Account(None, [Window(WEEKLY, 8, now + 6 * 86400)], plan="SuperGrok")]),
    ]


def selftest() -> None:
    now = time.time()
    a = claude_parse(json.dumps({"five_hour": {"utilization": 42.0, "resets_at": "2026-10-01T03:00:00.123456+00:00"},
                                 "seven_day": {"utilization": 7, "resets_at": None}, "seven_day_opus": None}))
    assert [(w.kind, w.used) for w in a.windows] == [(ROLLING, 42.0), (WEEKLY, 7.0)], a
    assert a.windows[0].resets_at and a.windows[1].resets_at is None

    c = codex_parse(json.dumps({"plan_type": "enterprise", "rate_limit": {
        "primary_window": {"used_percent": 98, "limit_window_seconds": 604800, "reset_at": 1788756101},
        "secondary_window": {"used_percent": 3, "limit_window_seconds": 18000, "reset_at": 1788617477}}}))
    assert [(w.kind, w.used) for w in c.windows] == [(ROLLING, 3), (WEEKLY, 98)], c
    assert c.label == "Enterprise" and c.windows[0].resets_at == 1788617477
    assert codex_parse(json.dumps({"rate_limit": {}})).note

    g = grok_parse(json.dumps({"config": {"creditUsagePercent": 15,
                                          "currentPeriod": {"end": "2026-10-05T00:00:00Z"}}}))
    assert g.windows[0].kind == WEEKLY and g.windows[0].used == 15
    g2 = grok_parse(json.dumps({"config": {"productUsage": [{"product": "GrokChat"},
                                                            {"product": "GrokBuild", "usagePercent": 22}]}}))
    assert g2.windows[0].used == 22
    g3 = grok_parse(json.dumps({"config": {"monthlyLimit": {"val": 200}, "used": {"val": 50},
                                           "billingPeriodEnd": "2026-10-10T00:00:00Z"}}))
    assert g3.windows[0].kind == MONTHLY and g3.windows[0].used == 25
    g4 = grok_parse(json.dumps({"config": {"isUnifiedBillingUser": True,
                                           "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY"}}}))
    assert g4.windows[0].used == 0 and g4.note


    assert fmt_countdown(now + 3 * 86400 + 5 * 3600 + 30, now) == "3D5h"
    assert fmt_countdown(now + 2 * 3600 + 13 * 60 + 5, now) == "2h13m"
    assert fmt_countdown(now + 20, now) == "1m"
    assert fmt_countdown(now - 5, now) == "已重置"
    assert codex_kind(18000) == ROLLING and codex_kind(604800) == WEEKLY and codex_kind(2592000) == MONTHLY

    # 多途徑合併：同 identity 合併、不同 identity 分開、全失敗回第一個錯誤
    def ok(aid, used):
        return lambda: Account(None, [Window(WEEKLY, used, 1.0)], identity=aid)

    def bad():
        raise NeedLogin("x 失效")
    r = collect_routes("codex", "Codex", [("A", None, ok("id1", 10)), ("B", None, ok("id1", 10)),
                                          ("C", None, ok("id2", 50)), ("D", None, bad)], "none")
    assert len(r.accounts) == 2 and r.error is None
    r2 = collect_routes("codex", "Codex", [("D", None, bad)], "none")
    assert r2.error == "x 失效" and not r2.accounts
    assert claude_plan({"subscriptionType": "max", "rateLimitTier": "default_claude_max_20x"}) == "Max 20x"
    assert claude_plan({"subscriptionType": "pro"}) == "Pro"
    assert grok_plan({"isUnifiedBillingUser": True}) == "SuperGrok"
    assert grok_plan({"subscriptionTier": "SUBSCRIPTION_TIER_SUPER_GROK_HEAVY"}) == "Super Grok Heavy"
    assert c.plan == "Enterprise"
    print("selftest OK")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="取一次數字並印出 JSON")
    parser.add_argument("--selftest", action="store_true", help="解析器自我測試")
    args = parser.parse_args()
    if args.selftest:
        selftest()
    else:
        print(json.dumps([asdict(r) for r in collect_all(load_config())], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
