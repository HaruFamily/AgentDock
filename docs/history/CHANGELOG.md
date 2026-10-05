# 版本歷史

## 0.7.1 — 2026-10-03

- 新問題通知：縮成小圖示時（含全螢幕休息）跳 Windows 通知，點通知直接打開那一題；右鍵「新問題通知」可關。
- 設定被外部改掉的偵測：記住各 Agent 設定檔裡的 MCP，若被客戶端更新或其他程式移除，該 Agent 下標示「被移除了」、
  浮動視窗「設定」分頁出現 •；「補回」與其他變更一起預覽後寫入，× 表示不要了。經 AgentDock 移除的不算。
- 祕密可帶到另一台電腦：MCP 庫 ⋯ →「匯出祕密」產生用密碼加密的 `.adsecrets`（scrypt＋AES-256-GCM），另一台「匯入祕密」只補缺少的項目，
  本機已有的值不覆寫。新增相依 cryptography。
- 本機絕對路徑（例如 mcp-google-sheets 的 SERVICE_ACCOUNT_PATH）不再寫進 mcp-library.json：和祕密一樣只存 data\secrets.json；
  舊的會自動搬過去，另一台電腦缺少時從 Agent 設定自動補回。
- 清理：刪除不再使用的 `ui/ball.py`、`ui/panel.py`（縮放邊框併入 card.py）；`gauge.py` 移除舊 TokenGauge 的 tkinter 視窗（約 1200 行），
  只留取數字的程式；移除相依 Pillow。
- 修正：拖曳 Agent 排序時會出錯（缺少匯入）。
- 新增 Windows 實機檢查清單（docs/VERIFICATION.md）。

## 0.7.0 — 2026-10-03

- 只剩一個浮動視窗（取代小球＋面板），兩種模式：浮動視窗（額度／問答／設定三頁，可拖曳四邊縮放並記住大小）與小圖示（愛心，休息不更新額度）。
  新問題：小圖示時發光跳動，簡單選擇題冒出對話泡泡一鍵回答；浮動視窗時切到問答頁，答完回原頁。新問題不搶焦點。
- 額度每家每個視窗都顯示（進度條）；問答 Ctrl+Enter 送出、Esc 縮成小圖示；拖到邊緣自動貼齊；全螢幕程式在前面時自動縮成小圖示且不跳泡泡。
- 可從右鍵選單切換外觀，偏好保存在本機。
- 右鍵選單精簡為：縮小／展開、額度更新頻率、外觀、結束。拿掉「立即更新」（額度頁有 ⟳）、「顯示剩餘／已用」（固定剩餘）、「顯示哪幾家」（有資料的都顯示）與「建立桌面捷徑」。
- 右鍵新增「背景透明度」（不透明／15／30／45／60%，只有卡片底色變透明，文字、進度條、蝴蝶結維持不透明）與「開機自動啟動」（HKCU Run，取消即刪除，資料夾搬家自動更新路徑）。
- 擴充庫也有 ⇅ 排序（名稱／類型，類型分組），各 Agent 底下的擴充跟著該 Agent 的 MCP 排序。
- 額度改由卡片顯示（TokenGauge 的取數字程式整合在同一個行程）；另外開著的 TokenGauge 視窗會自動關閉，避免兩個行程同時換發登入憑證。
- 全部介面（面板、對話框、選單、捲軸、核取方塊）改用粉紅＋薰衣草配色、圓角膠囊按鈕；系統匣與視窗圖示改成愛心。

## 0.6.1 — 2026-10-03

- 新增「擴充庫」：管理 MCP 以外的 Agent 擴充（hook、plugin），定義存在 mcp-library.json 的 `extensions`。
- 內建 rtk（rtk-ai/rtk）範本：Claude Code 與 Codex 寫入 PreToolUse hook，OpenCode 放入 plugin；Claude Desktop／Cowork 不支援，所以不會出現。
- 擴充沿用 GitHub 下載、每日檢查更新、預覽／備份／套用流程；在 Agent 的「＋」加入，「×」移除。也認得 `rtk init` 自行寫入的 hook。
- 改為免安裝：雙擊 `AgentDock.exe`（小型 C 啟動器，無 cmd 視窗）。uv、Python（固定 3.13）、套件與快取都放在資料夾內的 `runtime\`，
  第一次與 uv.lock 變更時自動準備；不寫入開始功能表、PATH 或登錄檔。取代 `Start AgentDock.cmd` 與 0.6.1 早先加入的開始功能表捷徑（會自動移除）。
- 根因：uv 搭配 Python 3.14 建立的 `.venv\Scripts\pythonw.exe` 是主控台程式，會一直留著 cmd 視窗；現在改用原始直譯器的 pythonw.exe 執行 `AgentDock.pyw`。
- uvx 類 MCP 改用資料夾內的 `runtime\uv\uvx.exe`，其他電腦不必安裝 uv。舊的 `.venv` 在沒有 Agent 使用後自動刪除。
- 新增 OpenCode 時，若只有 opencode.jsonc 就預設選它。
- 擴充庫只顯示實際使用中的版本（例如 v0.51.0，讀自 `rtk --version`）與新版提示；可加入的 Agent 與位置移到滑鼠提示。非 AgentDock 安裝的工具不會被「全部更新」覆蓋。
- 擴充的新增／編輯改為與 MCP 相同的逐欄表單（類型、GitHub 專案、執行檔…，以及各 Agent 的 hook／plugin 勾選與欄位），不再是 JSON。
- 擴充可在 AgentDock 直接更新（也包含用其他方式安裝的 rtk，例如 ~/.local/bin）：下載新版後取代 Agent 使用中的那個檔案（執行中也可），
  並自動把用到它的 Agent 更新到新版的 hook／plugin（OpenCode plugin 取自同一個 release，`source_url`），寫入前有備份。
- Agent 頁面在任何變更（加入、移除、停用、套用）後保持原本的捲動位置，不再跳回頂端；排入「將加入」的 MCP 直接出現在排序後的位置，而不是列在最後。
- 整合 TokenGauge：面板新增「額度」分頁（Claude Code、Codex 多 workspace、Grok 的剩餘額度與重置倒數），原本的浮動小視窗保留，可在分頁開關，
  跟著 AgentDock 啟動／結束。程式在 `agentdock/tools/tokengauge/`，資料在 `data/tokengauge`（自動沿用 %APPDATA%\TokenGauge 的設定並取消舊的開機啟動）。新增相依 Pillow。
- 需要在 PATH 上的工具可一鍵「加入 PATH」：複製到 data\bin，並把該資料夾加入使用者 PATH；更新時自動替換。

## 0.6.0 — 2026-10-02

- 改寫為 Python（PySide6）個人版，直接從原始碼執行，移除 Electron、TypeScript、打包與模組包（.admod）流程。
- 介面改為永遠置頂的浮動小球＋展開面板。
- QAI 內建，不再是另行安裝的模組；資料格式沿用 0.5。
- Codex TOML 寫回保留註解；自動偵測 Microsoft Store 版 Claude 設定檔路徑。
- 新增 `agentdock/tools/` 外部 Python 工具分頁機制。
- 移除 UsageMonitor、AgentConnector 預留項目。
- 新增 MCP 庫（mcp-library.json），Agent 與 MCP 合併為「Agent」分頁；預設收錄 codebase-memory-mcp。
- MCP 庫支援 GitHub 自動檢查更新與版本化安裝；區塊可收合；代理可編輯；面板邊緣可縮放。
- 「Agent」分頁改為清單式介面：圖示按鈕、橘點提示、底部「全部更新」；客戶端自管的 MCP（如 Codex 的 node_repl）標示為系統且不可移除；代理既有 MCP 自動收進 MCP 庫。
- Agent 可拖曳排序，Agent／MCP 可依名稱或類型排序（類型分組）；QAI 在設定檔中的名稱改為 `agentdock-qa`，舊名稱自動遷移。

## 0.5.0 — 2026-10-01

- 搬移至 AgentDock 正式 Git 倉庫。
- 平台源碼集中 src；QAI 集中 extensions/QAInteract，提供獨立安裝入口。
- 文件整理為 docs、根目錄 AGENTS.md 與維護 Skill。
- 清除舊 .ail、archive 和舊輸出，執行資料改用 .local/agentdock。
- 發行仍保持平台／QAI 分包。

## 0.4.0 — 2026-10-01

免安裝平台、Agent 設定檔登錄、批次 MCP 管理、QAI 自動設定與平台補齊。

## 0.3.0 及以前

建立 QAI 問答、選項與附件、共享桌面平台，以及平台／模組分包。
早期名稱 AgentInteractionLayer；AIL_* 協定／環境變數為相容性仍保留。
舊二進位、測試個人資料不作為版本歷史保存。
