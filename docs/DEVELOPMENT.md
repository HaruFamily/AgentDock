# 開發

需求：Windows、[uv](https://docs.astral.sh/uv/)。Python 版本與套件由 `pyproject.toml` 決定，uv 會自動安裝。

```powershell
uv sync                     # 建立 .venv（改了相依套件後也執行一次）
uv run python -m agentdock  # 以主控台執行，看得到錯誤
uv run pytest -q            # 全部測試（也可雙擊 Test AgentDock.cmd）
```

平常使用雙擊 `Start AgentDock.cmd`（pythonw，無主控台）。改完程式：從小球右鍵「結束」，再開一次。
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
