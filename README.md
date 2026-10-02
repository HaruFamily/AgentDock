# AgentDock

個人用的 Windows 浮動 Agent 工作台（Python 版）。桌面上一顆永遠置頂的小球，點開是面板：

- **問答（QAI）**：Agent 呼叫 `ask_user` 時小球變橘色並跳出問題；可選項、寫備註、輸入文字、附檔或貼圖回答。
- **Agent 與 MCP**：上半部是「MCP 庫」，每個 MCP 只定義一次（遠端網址、uvx/npx 套件、GitHub Release 下載、自訂指令）；
  下半部是各 Agent（Codex、OpenCode、Claude Code、Claude Desktop/Cowork），用「＋ MCP」加入、「−」移除、勾選啟停。
  變更會累積，最後一次預覽、備份、寫入。QAI 是 MCP 庫裡的內建項目。
- **外部工具**：放進 `agentdock/tools/` 的 Python 工具會自動成為面板分頁。

## 使用

1. 需要 [uv](https://docs.astral.sh/uv/)（已用來跑其他 MCP 就有）。
2. 雙擊 `Start AgentDock.cmd`。第一次會自動安裝 Python 與套件，之後直接開啟。
3. 改完程式碼，結束後再開一次就生效，不需要建置或打包。

小球：點一下展開／收合，拖曳移動，右鍵選單可結束。系統匣圖示也可以展開或結束。
面板右上「置頂」可切換面板是否永遠在最上層；小球永遠置頂。

## 資料

- `mcp-library.json`（進 Git）：MCP 庫的定義，不含祕密與絕對路徑，可在多台電腦共用。
- `data/`（不進 Git）：`agents.json`、`questions.json`、`attachments/`、`secrets.json`（MCP 的 API key 等）、
  `mcp/<名稱>/`（下載的 MCP 執行檔）、停用中的 Claude MCP（`disabled-*.json`）、`ui.json`、`agentdock.log`。

換一台電腦：pull 後打開 AgentDock，MCP 庫中標示「需下載」或「缺少祕密」的項目照提示補上即可。

## 資料夾

| 位置 | 內容 |
| --- | --- |
| `agentdock/` | 程式：`qa/` 問答、`agents.py` 設定管理、`broker.py`、`mcp_server.py`、`ui/` 介面、`tools/` 外部工具 |
| `tests/` | pytest（含真實 MCP stdio 往返與離屏 UI 測試） |
| `docs/` | 現況、架構、開發、驗證與歷史 |

開發：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。歷史：[docs/history/CHANGELOG.md](docs/history/CHANGELOG.md)。
