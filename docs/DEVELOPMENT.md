# 開發

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

### Inbox 原生活動接入

設定 → 擴充庫 → ＋ → inbox-activity 範本，再於各 Codex／Claude Code／OpenCode 的 ＋ 加入、預覽並套用。使用資料夾內 Python，不需下載擴充或加入 PATH。重新啟動 AgentDock 及客戶端；Codex 還須在客戶端檢視並信任 hooks。接入不會變更原本的授權政策。

接入只提供近期活動觀測：120 秒無事件或 Inbox 重啟顯示未知，Stop／idle 顯示回合結束，不當成成功。授權通知不移動焦點；Windows 通知被關閉時仍有 Inbox 狀態及紅點。Codex 可能自動審核；Codex／Claude Code 無可靠批准結果配對時提示會保留至結束或失效，請回原客戶端確認。OpenCode 依 permission ID 解除。AgentDock 未運行時丟棄通知，不讀取或重播原生聊天。

原生 session ID 與 MCP work_id 是不同識別，介面另列工作階段，不推測對應任務。Claude Desktop 一般 Chat／Cowork 未提供本次驗證的接入；Desktop 裡的 Claude Code 需支援 permission_prompt 的版本。

協定依據：[Codex hooks](https://learn.chatgpt.com/docs/hooks)、[Claude Code hooks](https://code.claude.com/docs/en/hooks)、[OpenCode plugins](https://opencode.ai/docs/plugins/)。

測試只用暫存目錄；`test_broker_mcp.py` 會啟動真正的 MCP stdio 行程並設定 `AGENTDOCK_NO_LAUNCH=1`，不會開啟 UI。
UI 測試使用 `QT_QPA_PLATFORM=offscreen`。
