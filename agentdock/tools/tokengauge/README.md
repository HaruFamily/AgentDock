# 額度（原 TokenGauge，已整合進 AgentDock）

浮動視窗的「額度」頁顯示 Claude Code、Codex（可同時追多個 ChatGPT workspace）、Grok 的剩餘額度與重置倒數。

- 一律顯示剩餘；有資料的每一家、每個視窗（5H＝5 小時、W＝每週、M＝每月；倒數的 D 是天）都以進度條列出。
- 更新頻率在右鍵選單「額度更新頻率」；要馬上更新按額度頁的 ⟳。縮成小圖示時休息，不向伺服器取數字。
- 取數字的程式是 `gauge.py`，在 AgentDock 的行程裡執行；另外開著的舊 TokenGauge 會被自動關閉，避免兩邊同時換發登入憑證。
- 設定與保存的 Codex workspace 在 `AgentDock\data\tokengauge`（第一次啟動時從舊的 `%APPDATA%\TokenGauge` 複製，並取消舊版的開機自動啟動）。
- 每家名稱旁的小標籤是訂閱方案：Claude 讀自登入檔、Codex 讀自用量回應或 token，Grok 則是帳務回應裡找得到才顯示。

## Codex：同時追蹤個人版與企業版

1. 先用 `codex login` 登入個人版 workspace，等 AgentDock 更新一次（或按 ⟳），這組登入會另外保存一份。
2. 再執行 `codex login`，這次選企業版 workspace。
3. 之後兩個 workspace 會一起顯示，Codex CLI 則使用你最後登入的那個。

- 切換 workspace 時**直接執行 `codex login`，不要先 `codex logout`**；登出可能撤銷舊 session，保存的登入就會失效。
- 改名或隱藏：編輯 `data\tokengauge\config.json` 的 `"codex_labels": {"<account_id>": "公司"}`、`"codex_hidden": ["<account_id>"]`。
  account_id 就是 `codex_accounts` 資料夾裡的檔名；刪掉其中的檔案等於移除那個 workspace。

## 各家資料來源

| 家 | 讀取的登入 |
|---|---|
| Claude Code | `~/.claude/.credentials.json`，或 opencode 的 anthropic 登入 |
| Codex | `~/.codex/auth.json`、保存的 workspace 副本，或 opencode 的 openai 登入 |
| Grok | opencode 的 xAI 登入（`opencode.db` 或 `auth.json`） |

token 過期時會用各家 CLI 的公開流程自動換發，並寫回原本的憑證檔，所以不會把 CLI 登出。

## 除錯

- `runtime\venv\Scripts\python.exe -m agentdock.tools.tokengauge.gauge --once`：取一次數字，以 JSON 印出（`--selftest` 跑解析器自我測試）。
- 錯誤記錄在 `data\tokengauge\error.log`。

所有用量端點都是非公開介面，對方改版就可能失效；失效時卡片會顯示錯誤訊息，不會顯示假的數字。
