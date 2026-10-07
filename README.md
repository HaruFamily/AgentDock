# AgentDock

個人用的 Windows 浮動 Agent 工作台（Python 版）。桌面上只有一個永遠置頂的浮動視窗，兩種模式：

- **浮動視窗**：一次一頁 —「額度」（每家每個視窗 5H／W／M 都列出）、「問答」（直接在這裡回答，Ctrl+Enter 送出）、「設定」（Agent、MCP、擴充）。
  四邊與四角都可以拖曳縮放，大小會記住。新問題會自動切到「問答」，答完回到原本那頁。拖到螢幕邊緣會貼齊。
- **小圖示**：每次啟動只顯示圓形圖示，位於主螢幕右側置中；點圖示切換浮動視窗，展開時圖示仍保留。休息時不更新額度；有問題時顯示題數，簡單選擇題可用對話泡泡回答。
  全螢幕程式在前面時會自動縮成小圖示且不跳泡泡，離開後恢復。
- **外觀**：預設黑色與螢光綠的極簡風格；可從本機 `data/private_theme.py` 載入額外主題，再從右鍵選單切換。
  本機主題不提交 Git、不納入設定匯出。Ctrl+Alt+H 可立即切回極簡，視窗與圖示同步更新。
- 右鍵選單只有：縮小／展開、額度更新頻率、外觀、背景透明度（只有底色變透明，文字與進度條不變）、新問題通知、開機自動啟動、結束。
- 縮成小圖示時有新問題會跳 Windows 通知，點通知直接打開那一題。額度一律顯示剩餘，有資料的每一家都列出；要馬上更新按額度頁的 ⟳。
- **AgentChat**：依 Agent／任務分區，問題、回答、同步指令與結果共用歷史；`ask_user` 提問，`report_to_user` 回報開始／完成，顯示進行中／待回覆／未讀取。保留選項、備註、文字與附件回答，支援移除顯示歷史；本版不主動送出新任務。[接收整合說明](docs/AGENTCHAT.md)
- **Agent 與 MCP**：上半部是「MCP 庫」，每個 MCP 只定義一次（遠端網址、uvx/npx 套件、GitHub Release 下載、自訂指令）；
  下半部是各 Agent（Codex、OpenCode、Claude Code、Claude Desktop/Cowork），用「＋ MCP」加入、「−」移除、勾選啟停。
  變更會累積，最後一次預覽、備份、寫入。QAI 是 MCP 庫裡的內建項目。
  Agent 設定檔裡的 MCP 若被客戶端更新或其他程式移除，會標示「被移除了」，可一鍵補回。
- **擴充庫**：MCP 以外的 hook／plugin（例如 [rtk](https://github.com/rtk-ai/rtk)），同樣在各 Agent 用「＋」加入。
  Claude Code、Codex 寫入 PreToolUse hook，OpenCode 放 plugin；Claude Desktop／Cowork 沒有 hook 機制，不適用。
- **外部工具**：放進 `agentdock/tools/` 的 Python 工具會自動成為面板分頁。

## 使用

下載（或 git clone）整個資料夾，雙擊 **`AgentDock.exe`** 就好。不需要先安裝 Python 或 uv。

- 第一次開啟會出現一個「AgentDock 準備中」視窗，把 uv、Python 與套件下載到資料夾內的 `runtime\`（約 1–2 分鐘），完成後自動開啟並關閉視窗。
  之後每次開啟都不會有任何視窗；`uv.lock` 有變更時會自動再準備一次。
- 所有東西都在 AgentDock 資料夾裡：不寫入「開始」功能表、不改 PATH、不寫登錄檔。唯一例外是右鍵勾選「開機自動啟動」時，在 HKCU 的 Run 登錄 寫一筆指向這個資料夾的 AgentDock.exe（取消勾選即刪除；資料夾搬家後會自動更新路徑）。要移除，刪掉資料夾即可（各 Agent 設定檔裡的項目與開機自動啟動請先在 AgentDock 取消）。
- 想放桌面就自己對 `AgentDock.exe` 建立捷徑。改完程式碼，結束後再開一次就生效，不需要建置或打包。

浮動視窗可拖曳移動，大小、所在頁與外觀會記住；每次啟動時小圖示回到主螢幕右側置中。Ctrl+Alt+Q 或系統匣圖示可以叫回卡片。

## 資料

- `mcp-library.json`（進 Git）：MCP 庫的定義，不含祕密與本機絕對路徑（輸入時會自動移到本機），可在多台電腦共用。
- `data/`（不進 Git）：`agents.json`、`questions.json`、`attachments/`、`secrets.json`（MCP 的 API key 等）、
  `mcp/<名稱>/`（下載的 MCP／擴充執行檔）、`bin/`（加入 PATH 的工具複本）、停用中的 Claude MCP（`disabled-*.json`）、`ui.json`、`agentdock.log` 與本機私人主題。
- `docs/history/local-backups/`（不進 Git）：本機歷史備份。分享整個資料夾時，請排除它與 `data/`。

換一台電腦：pull 後打開 AgentDock，MCP 庫中標示「需下載」或「缺少祕密」的項目照提示補上即可。
敏感設定可從設定頁底部的「設定選單 → 進階／備份與還原」匯出為加密的 `.adsecrets`，再於新電腦匯入（本機已有的值不會被覆寫）。

## 資料夾

| 位置 | 內容 |
| --- | --- |
| `agentdock/` | 程式：`qa/` 問答、`agents.py` 設定管理、`broker.py`、`mcp_server.py`、`ui/` 介面、`tools/` 外部工具 |
| `tests/` | pytest（含真實 MCP stdio 往返與離屏 UI 測試） |
| `docs/` | 現況、架構、開發、驗證與歷史 |
| `AgentDock.exe`、`launcher/` | 免安裝啟動器與它的 C 原始碼（`launcher/build.sh` 重新建置） |
| `scripts/` | 啟動器呼叫的準備腳本（setup.ps1）與測試（test.ps1） |
| `runtime/`（不進 Git） | 資料夾內的 uv、Python、套件與快取 |

開發：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。歷史：[docs/history/CHANGELOG.md](docs/history/CHANGELOG.md)。
