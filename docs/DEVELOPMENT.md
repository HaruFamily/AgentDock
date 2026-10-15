# 開發

Codebase Memory 整合：擴充庫已有 `codebase-memory-integration`，其他空白庫可由「＋」加入範本。MCP 庫需先設定本機原生 Codebase Memory 執行檔；可將 MCP 與擴充同時加入待套用清單。既有外掛／hooks 會被辨識並標示需更新，按「全部更新」後預覽、備份、套用。更新後重啟客戶端，Codex 還須在 /hooks 信任。只在使用者確認時接管客戶端檔案；不自動修改技能與 AGENTS.md，不呼叫上游 install/update 來重寫全部客戶端。

驗證包含 `tests/test_codebase_integration.py` 的暫存三客戶端設定、既有接入去重、共用 TOML 合併、MCP 與 hook 執行檔同步、預覽衝突、整批復原，以及 `tests/cbm_plugin_contract.mjs` 的 V1/V2 事件合約、原結果保留與失敗隔離。接入狀態代表設定已存在／與範本一致，不代表客戶端已載入、信任或回傳圖譜結果。

Codex 背景建議過濾：`UserPromptSubmit.prompt` 有明確 app-generated suggested-next-message 前綴時標記 background，後續只靠 session 識別沿用。此前綴來自本機 composer_predictions 診斷證據，並非保證穩定的公開協定；若 Codex 改版更動文字需重新核對。hook 的 prompt 欄位依據 [官方 hooks 文件](https://learn.chatgpt.com/docs/hooks)，不讀 transcript，也不把 prompt 傳入 broker。ended 原生工作階段退出列表；測試需涵蓋背景 Stop／重啟／SessionEnd、不同 owner、正常討論與待授權。

討論分組接入：Inbox 的 ask_user/report_to_user 可傳完整 `conversation_id`，同一討論固定使用此 ID；未知時不要捏造，也不要按每個小任務更換 work_id。OpenCode inbox-activity 外掛的 V1 tool.execute.before／V2 ctx.tool.hook("execute.before") 自動傳送 sessionID 與 work_id 的關聯，依據 [官方外掛 API](https://dev.opencode.ai/v2/docs/build/plugins/)。更新外掛仍需設定頁預覽、確認與備份；本次程式修正不會自行改寫已安裝外掛。Codex／Claude Code 的 PreToolUse/PostToolUse 亦可提供關聯，只識別 inbox／agentchat 的問答與結果工具。舊資料只有在取得明確關聯後才合併。

需求：Windows。不需要另外安裝 Python 或 uv：`AgentDock.exe` 第一次執行時，`scripts\setup.ps1` 會把 uv、
Python（版本見 `.python-version`）、套件與快取都放進 `runtime\`（環境變數設定在 `scripts\env.ps1`）。

- 平常使用：雙擊 `AgentDock.exe`（無主控台）。改完程式：從浮動卡片右鍵「結束」，再開一次。
- 測試：雙擊 `Test AgentDock.cmd`（使用同一個 runtime 環境）。
- 想看到錯誤輸出：`runtime\venv\Scripts\python.exe -m agentdock`。
- 也可以用自己的 uv：`uv sync` 會建立 `.venv`，AgentDock 會優先使用 `runtime\venv`、沒有時才用 `.venv`。
- 啟動器原始碼在 `launcher/launcher.c`，用 `launcher/build.sh`（mingw-w64）重新建置 `AgentDock.exe`。
錯誤記錄在 `data/agentdock.log`。

## 加入外部工具

在 `agentdock/tools/` 新增模組，例如 `mytool.py`：

```python
from PySide6.QtWidgets import QLabel
from agentdock.tools import ToolSpec

def create(ctx):  # ctx.data_dir / ctx.store / ctx.notify(text)
    return QLabel("我的工具")

TOOL = ToolSpec(title="我的工具", create=create, order=50)
```

工具自己的資料請放在 `ctx.data_dir / "<工具名稱>"`。需要的新套件加到 `pyproject.toml` 後執行 `uv sync`。

## 測試邊界

### 提問格式

`ask_user.question`、多題的 `questions[].question`、多題前言與選項 `description` 支援自由文字 Markdown：空行分段、`**粗體**`、`*斜體*`、標題、清單、引用、反引號行內程式碼與三反引號程式碼區塊。選項標題維持純文字。

可選擴充區塊（標記須獨立成行、不可巢狀）：

```text
要用程式重建設定嗎？

:::warning
重建會覆蓋**手動調整**。
:::

:::note
這是較小、較淡的補充說明。
:::
```

警示配色使用主題 WARN_BG／WARN_INK，補充使用 MUTED，內文使用 INK，程式碼底色使用 ACCENT_SOFT。不解析輸入 HTML／CSS、不載入行內圖片或外部資源；圖片沿用 images 參數。未關閉的擴充標記保留原文，程式碼內的標記不解讀。無固定段落結構、無新增必要欄位，原文仍原樣儲存。MCP 工具與參數 schema 會公開相同語法指引，客戶端需重新連線以載入更新。

### Inbox 原生活動接入

設定 → 擴充庫 → ＋ → inbox-activity 範本，再於各 Codex／Claude Code／OpenCode 的 ＋ 加入、預覽並套用。使用資料夾內 Python，不需下載擴充或加入 PATH。重新啟動 AgentDock 及客戶端；Codex 還須在客戶端檢視並信任 hooks。接入不會變更原本的授權政策。

接入只提供近期活動觀測：120 秒無事件或 Inbox 重啟顯示未知，Stop／idle 顯示回合結束，不當成成功。授權通知不移動焦點；Windows 通知被關閉時仍有 Inbox 狀態及紅點。Codex 可能自動審核；Codex／Claude Code 無可靠批准結果配對時提示會保留至結束或失效，請回原客戶端確認。OpenCode 依 permission ID 解除。AgentDock 未運行時丟棄通知，不讀取或重播原生聊天。

原生 session ID 與 MCP work_id 是不同識別，介面另列工作階段，不推測對應任務。Claude Desktop 一般 Chat／Cowork 未提供本次驗證的接入；Desktop 裡的 Claude Code 需支援 permission_prompt 的版本。

協定依據：[Codex hooks](https://learn.chatgpt.com/docs/hooks)、[Claude Code hooks](https://code.claude.com/docs/en/hooks)、[OpenCode plugins](https://opencode.ai/docs/plugins/)。

測試只用暫存目錄；`test_broker_mcp.py` 會啟動真正的 MCP stdio 行程並設定 `AGENTDOCK_NO_LAUNCH=1`，不會開啟 UI。
UI 測試使用 `QT_QPA_PLATFORM=offscreen`。
