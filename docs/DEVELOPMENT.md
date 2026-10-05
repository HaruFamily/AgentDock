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

測試只用暫存目錄；`test_broker_mcp.py` 會啟動真正的 MCP stdio 行程並設定 `AGENTDOCK_NO_LAUNCH=1`，不會開啟 UI。
UI 測試使用 `QT_QPA_PLATFORM=offscreen`。
