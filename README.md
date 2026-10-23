# AgentDock

個人用的 Windows Agent 工作台（Python 版）。像一般桌面 App 常駐在右下角系統匣，用一個對話視窗對各家 Agent CLI 收發。

- **對話視窗**：點系統匣圖示或按 Ctrl+Alt+Q 打開，關掉只是收回系統匣。左側清單依「專案」（名稱＋根目錄＋預設 Agent）分組，可拖曳換專案、搜尋；
  右邊是完整對話與即時回覆，工具動作收成一行，授權與問題直接在對話裡按，Enter 送出、Shift+Enter 換行。新對話頁與輸入框的操作對齊 Claude／Codex 桌面版：模型與權限在輸入框底列、專案或資料夾在輸入框下方、一顆圓鈕送出／停止、`/` 指令與 `@` 檔案、Ctrl+K 搜尋面板、Ctrl+B 收起清單、釘選對話。可對 OpenCode、Claude Code（對話視窗裡顯示為 Claude）、Codex、Grok 收發；可分叉、匯入在別處開的對話，標題列看上下文用量與 Git 狀態並開 Git 視窗提交。[說明](docs/DISPATCH.md)
- **最左窄邊欄**：只有圖示；聊天、工具在上，設定在最下面。聊天頁再點「聊天」收起或展開對話清單。
- **系統匣**：圖示角標顯示待授權＋待回答數，有新回覆時亮點；待授權、待回答會跳 Windows 通知（可在系統匣選單關閉），點通知打開那段對話。
  選單：對話視窗、新對話、額度浮動視窗、通知開關、外觀、開機自動啟動（開機時只在系統匣啟動）、結束。
- **工具**：額度（每家每個視窗剩餘多少）與額度浮動視窗開關；額度浮動視窗是唯一的浮動小窗，可拖到任何位置。
  鬧鐘：時間、名稱、重複的星期、聲音（Windows 內建的鬧鐘音，可試聽）；開關啟停、點一下編輯。AgentDock 開著就會響（視窗關掉、只剩系統匣也會），跳出置頂的視窗可「貪睡 5 分鐘」或「關掉」，聲音最多響 5 分鐘。電腦睡著或 AgentDock 沒開而錯過超過 10 分鐘的就不補響。只響一次的鬧鐘關掉後自動停用。鬧鐘存在 `data/alarms.json`。`agentdock/tools/` 的 Python 工具也列在這頁。
- **文字大小**：設定 → 外觀可選標準／大／特大（預設大），整個 App 一起變。
- **外觀**：預設黑色與螢光綠的極簡風格；可從本機 `data/private_theme.py` 載入額外主題（可替對話視窗的背景、清單、輸入框、標題、訊息、送出鈕與所有對話框畫蕾絲與緞帶等裝飾；Windows 11 的標題列也會換成主題色），從「設定 → 外觀」或系統匣「外觀」切換。本機主題不提交 Git、不納入設定匯出。Ctrl+Alt+H 立即切回極簡。
- **Inbox 已停用**：對話都在視窗裡原生處理，不再透過 `ask_user`／`report_to_user` MCP。舊設定檔裡的 Inbox MCP 與 inbox-activity 擴充會在「設定 → 全部更新」預覽後移除；移除前若有 Agent 呼叫 ask_user，會收到「已停用、請直接詢問使用者」的錯誤。
- 舊的浮動卡片暫時保留一版：`data/ui.json` 設 `"shell": "card"` 可退回（含 Inbox）。
- **設定**：左邊是分類（一般、外觀、Agent、MCP、擴充、備份），和 Claude／Codex 桌面版一樣一次看一頁。
  - 一般：開機自動啟動、需要處理時跳出通知、額度浮動視窗（和系統匣選單同步）。外觀：點一下就換。
  - Agent：對話視窗能用的四個（OpenCode、Claude、Codex、Grok CLI）各一張卡片：程式版本（找不到會標出來）、預設模型與推理強度、登入的帳號與方案、MCP／擴充數量與需要處理的事。Claude Desktop／Cowork 與 GrokBot 已不列出也不再提供新增（舊的登記仍保存在 `data/agents.json`）。
  - MCP：每個 MCP 一列（只定義一次：遠端網址、uvx/npx 套件、GitHub Release 下載、自訂指令），右邊每個 Agent 一個開關。點開關就排入加入／移除，再點一次復原；要更新、被其他程式移除、已停用的會直接寫在那一列。
  - 擴充：MCP 以外的 hook／plugin（例如 [rtk](https://github.com/rtk-ai/rtk)），一樣每個 Agent 一個開關；Claude、Codex 寫入 hook，OpenCode 放 plugin，不支援的 Agent 沒有開關。
  - 變更會累積在下方，最後一次預覽、備份、寫入。

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
敏感設定可從「設定 → 備份」匯出為加密的 `.adsecrets`，再於新電腦匯入（本機已有的值不會被覆寫）。

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
