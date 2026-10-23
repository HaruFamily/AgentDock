# 架構

## 系統匣 App（2026-10-22）

`main()` 預設建立 `ui/tray_app.py` 的 TrayApp（Dock 子類別，重用外觀、字型、開機自動啟動與啟動器檢查，但不建立浮動卡片、小圖示與 DockLayout）。ChatWindow 啟動時建立並隱藏，`add_page` 放入 AgentsView（設定）與 ToolsPage（工具）；QuotaModel 只在工具頁顯示或額度浮動視窗開著時取數字。系統匣角標由 dispatch 列計算；`dispatch-attention` 事件觸發通知。Broker 以 `inbox=False` 建立；Library `inbox_retired=True`。`ui.json` 的 `"shell": "card"` 才走舊 Dock。

## 對話視窗（2026-10-22）

`ui/chat_window.py` 的 ChatWindow 由 App 延遲建立，關閉只隱藏。資料：`dispatch/projects.py`（`data/projects.json`）、`dispatch/transcript.py`（訊息與即時事件 → 對話項目）、`Dispatcher.history()`／`listeners`。所有背景結果經 Bridge 的 Qt 信號回到 UI 執行緒；store 變更經 App 的 bus 節流刷新。細節見 [DISPATCH.md](DISPATCH.md)。

對話功能第三批：`Adapter.fork()`／`sessions()` 與 `session.context` 事件（存成列的 `context`）；`Dispatcher.fork()`、`sessions()`、`adopt()`（四家皆可接手）。Git 為 `agentdock/gitops.py`（純 `git` 指令，不匯入 Qt）與 `ui/git_view.py` 的 GitDialog，git 指令在背景執行緒跑、經信號回 UI。

## 收發（2026-10-22）

Inbox 可送任務給 OpenCode 2.x 並在卡片上授權、回答、接續與停止。`dispatch/` 套件（不匯入 PySide6）：`opencode.py` 連共用背景服務、`runner.py` 的 Dispatcher 在 App 行程背景執行緒處理 HTTP 與 SSE、`store.py` 存 `data/dispatch.json`。卡片 owner／work_id 與 Inbox MCP、活動 hook 相同（`session:opencode:<id>`），三者合併為一張；broker 沒有新增路由。細節、API 對照與驗證見 [DISPATCH.md](DISPATCH.md)。

之後改由對話視窗收發，並加入 Claude Code（每個對話一個 `claude` stream-json 子行程，`dispatch/claude.py`）、Codex（共用一個 `codex app-server` JSON-RPC 子行程，`dispatch/codex.py`）與 Grok（共用一個 `grok agent stdio` ACP 子行程，`dispatch/grok.py`）；兩者經 `dispatch/adapter.py` 的轉接器介面接到 Dispatcher，輸出統一成 OpenCode V2 形狀，store／通知／對話畫面不必分家。

## Grok CLI 接入（2026-10-18）

`grok-cli` 使用共用 TOML MCP 管理，另同步全域停用清單，移除時阻止相容設定同名項目回退。擴充寫入設定旁 `hooks/agentdock.json`，沿用合併交易。活動 adapter 解析 camelCase、排除子代理與截斷工具參數；Claude adapter 忽略 Grok 匯入事件。額度原始 provider 分開快取，`display_results` 統一品牌名稱並優先使用有效的 GrokBot 共用訂閱來源。範圍與驗證見 [GROK_CLI.md](GROK_CLI.md)。

## GrokBot 帳號接入（2026-10-18）

`grokbot.py` 以 Windows 桌面登入唯讀解密取得短期 token，直接使用 GrokBot 的 DashboardService 讀取個人 MCP 與週額度。`ui/grokbot_view.py` 提供獨立背景管理介面，遠端 MCP 的操作不混入本機 `ExtPlan`；套用前比對帳號與遠端快照、先備份再單次寫入及回讀。未知寫入結果不自動重送，備份還原重新預覽。TokenGauge 新增 grokbot provider。雲端命令與外掛設定保留唯讀，未實作桌面 hooks；細節與非原子衝突限制見 [GROKBOT.md](GROKBOT.md)。

## Codebase Memory 整合（2026-10-15）

`codebase-memory-integration` 是擴充庫定義，`mcp` 指向 MCP 庫中的項目，沿用其原生執行檔與環境，不另下載、不依賴 PATH。`codebase_integration.py` 辨識既有接入並產生受管理 hooks；`codebase_hook.py` 以 stdin 轉送事件至同一執行檔的 `hook-augment`，3 秒逾時，輸出只保留 additionalContext，不傳遞批准／拒絕決策。

- OpenCode 使用內建 `assets/opencode/cbm-augment.ts`，支援 V2 setup 與 V1 1.18.29+ server；搜尋完成後從正確 input 取得 grep/glob 參數，解析 hook JSON 後補充結果，保留原結果與 metadata。這是搜尋補充接入，不宣稱涵蓋上游開發版的所有生命週期功能。
- Codex 的 CBM hooks 寫入既有 config.toml，保留註解；亦檢查 hooks.json 並移除重複的已辨識 CBM registrations，其他 hooks 維持。事件為 SessionStart／SubagentStart，更新後需由使用者在 /hooks 信任。
- Claude Code 接 SessionStart／SubagentStart、PreToolUse Grep/Glob/Bash、PostToolUse Read；辨識上游已知的 Windows cbm-*.cmd 呼叫，只接管 registrations，不刪原啟動腳本。
- `status` 辨識部分安裝；`in_sync` 比對完整接入與 MCP 設定。MCP 更新後排入擴充更新；缺少 MCP／原生執行檔、非原生啟動器、自訂啟動參數皆明確提示，不猜測其他安裝路徑。
- `ExtensionManager.prepare_all` 將 MCP 與擴充組成同一次交易，共用 config.toml 只寫一次。預覽列明接管與同步 MCP；套用前重查檔案、Agent 清單與執行檔依賴，備份後寫入，任何一筆失敗復原整批。技能、AGENTS.md 及上游產生器維持原管理；上游重新安裝覆寫接入時，透過「全部更新」預覽恢復。

## 討論歸屬（2026-10-14）

`POST /conversation-links` 使用 broker 的 owner 驗證，保存 `chat.json.conversation_links`，將原始 work_id 連到 `session:<client>:<完整 session ID>`。hook/plugin 僅針對 Inbox ask_user/report_to_user 提取識別欄位；MCP 可選 conversation_id 走同一路徑。這是明確關聯，與已停用的舊 sync_aliases 無關。顯示、已讀、移除及待答檢查使用討論分組；儲存事件與問答的原 work_id/request_key 保持不變。同一 work_id 不能重綁其他 session；不同 owner 保持隔離。沒有關聯證據的舊卡片不猜測合併。

以下原生活動「不猜測合併」仍成立；取得上述明確關聯時，同 session 的原生活動與任務共用一張卡片，待授權優先顯示，解除後恢復問答／結果。晚到關聯會重用已展開的問答元件。

啟動：`AgentDock.exe`（launcher/launcher.c，GUI 程式）檢查 `runtime\venv` 是否已依目前的 uv.lock 準備好：
是 → 用 venv 的原始直譯器 `pythonw.exe` 執行 `AgentDock.pyw`（自行加入 venv 套件，避開部分 uv 版本的主控台 pythonw）；
否 → 開 `scripts\setup.ps1` 顯示進度，把 uv／Python／套件準備到 `runtime\` 後再啟動。`paths.gui_command()` 是唯一的啟動入口（Agent 自動喚起也用它）。

單一 Python 程式，兩種執行方式：

1. **浮動 App**（`python -m agentdock`）：PySide6 浮動視窗（小圖示／浮動視窗）、系統匣；持有 QuestionStore 並啟動 broker。
2. **MCP stdio 服務**（`python -m agentdock.mcp_server`）：由 Codex／Claude 等客戶端啟動，每個客戶端一個行程。

```
客戶端 ──stdio──> mcp_server ──HTTP 127.0.0.1 + token──> broker ──> QuestionStore <── UI
```

- `broker.py`：隨機埠、隨機 token，寫入 `data/endpoint.json`；拒絕帶 Origin 的請求。HTTP 只能建立、查詢、取附件，不能提交答案；答案只能從 UI 提交。
- `client.py`：mcp_server 用來找 broker；App 沒開時以 pythonw 啟動 `--background`。
- `qa/store.py`：`questions.json` 與 `attachments/`，格式與 0.5 相同。執行緒安全；事件經 Qt 信號轉到 UI 執行緒。
- Inbox 接收：只接受 completed／failed／cancelled 的 `report_to_user` → `POST /chat/events` → `QuestionStore.receive()`；呼叫者身分來自 broker header，事件內不能指定 owner。`chat.json` 保存事件、已讀及隱藏問題 ID；依 `(owner, work_id)` 與原有問答合併成對話。
- `ui/inbox_view.py` 依 Agent 分類顯示任務狀態與所有 pending 問題，分類可收合；Foldout 懶載入 `QaView(inline=True)` 作答編輯器，不提供歷史頁。重用已展開編輯器以保留焦點與草稿。事件去重依 owner／work_id／request_key，內容不同會拒絕；待答時拒絕結束回報。
- `ui/dock_layout.py` 管理永久 Icon、橫向 Tab 列、ContentWindow；TabBase 以透明頂層窗繪製延伸至 Icon 下方的膠囊底板，QVariantAnimation 控制最長 220ms 收放；原生透明視窗固定完整尺寸，只更新底板繪製範圍／圓角遮罩，Tab 固定尺寸搭配透明度變化，Icon 維持最上層；底板原生視窗遮罩排除 Icon 圓形區域，即使層級反轉也不遮擋或攔截其點擊。ContentWindow 沿用 FloatingCard 的頁面與縮放能力、隱藏內建 Tab 列，收合時不變形成第二顆 Icon。各頁 size／pos／detached 存於 ui.json 的 page_geometry；dock_orientation 儲存橫式／直式排列，直式選上下展開並將內容放側邊；dock_size_revision 控制一次性高度遷移，content_open 保存整組隱藏前的內容展開狀態。元件持續存在，切頁不重建；拖曳事件只更新跟隨狀態，放開時存檔。貼齊模式自動選上下，較高內容可放側邊，均限制螢幕範圍；全螢幕自動收起並停止動畫，離開後恢復。
- `activity.py`：原生工作階段最新觀測存於 activity.json，依 owner／client／session 隔離；不與 MCP work_id 猜測合併。`POST /activity` 使用既有 broker 驗證，只接受通知狀態，沒有批准 API。`activity_hook.py` 接 Codex／Claude Code stdin，OpenCode plugin 接事件；皆只轉送白名單欄位，不讀 transcript，也不回傳授權決策。兩分鐘無新事件或重啟後，動態狀態降為未知；不以長命 MCP 行程存在推定任務執行中。授權事件透過 UI 信號顯示系統通知，不切頁或搶焦點。
- 聊天讀取以已提交的不可變 `_chat`／`_questions` 快照建立快取索引；Inbox UI 用 `inbox_tasks()` 取得最新內容及 pending 問題，不呼叫全歷史匯出 `conversations()`，也不等待寫入鎖。接收與已讀以 copy-on-write 替換事件，禁止修改已發布的事件物件。已讀由獨立背景工作合併寫入，退出時 `flush_reads()` 等待完成。任務分批顯示但不隱藏 pending；依任務與問題 ID 重用 QWidget。
- 移除區域保留內部問題紀錄，避免破壞等待、caller 隔離與重試；新的事件重新建立區域，明確重試 ask_user 可恢復被隱藏的問題，背景 get_user_answer 不會使區域反覆重現。
- 呼叫者身分：`AGENTDOCK_CLIENT_ID`（設定檔中為 `agentdock-<agent id>`）雜湊成 owner；只能讀自己的問題。
- `library.py`：MCP 庫。定義在 `mcp-library.json`（佔位符 `{AGENTDOCK}` `{DATA}` `{HOME}` `{BIN}`），祕密在 `data/secrets.json`，
  下載物在 `data/mcp/<key>/`。`render()` 依客戶端類型產生設定項目；`in_sync()` 判斷是否「需更新」；`from_config()` 把既有設定收進庫。
- `extensions.py`：擴充（非 MCP）。mcp-library.json 的 `extensions` 依客戶端類型列出接法：claude-code／codex 是 hook
  `{event, matcher, command}`，分別寫入 `<claude 目錄>/settings.json`（CLAUDE_CONFIG_DIR 或 ~/.claude）與 `<codex 目錄>/hooks.json`；
  opencode 是 plugin `{plugin, source}`，把範本複製到 `<opencode 目錄>/plugins/`。目錄由登錄的設定檔位置推得。
  `prepare()`/`apply()` 與 agents.py 相同：預覽、確認原檔未變、備份、寫入、失敗復原。安裝與否由檔案內容判斷（含 `rtk init` 寫的）。
  標準 RTK OpenCode 範本使用 AgentDock 維護的 V2 setup／V1 1.18.29+ server 接入檔；library 的 binary 更新不下載覆蓋此接入檔，舊 source_url 與版本快取也不優先於它。以 repo 與 source 判別，改名範本仍有效，自訂來源維持下載優先。status 只代表存在，in_sync 用內容差異提示需更新，不代表已通過客戶端執行驗證。
- `userpath.py`：hook 以名稱呼叫工具（`rtk hook claude`），改寫後的指令也是 `rtk ...`，所以工具必須在 PATH 上。
  `data/bin/` 放目前版本的複本，經使用者確認後加入 HKCU PATH；偵測時讀登錄檔的 PATH（本行程的可能過期）。
- `agents.py`：以操作清單（enable/disable/remove/add/update）規劃變更。Codex／OpenCode 用 `enabled` 停用；Claude 系列沒有此旗標，停用時移出設定存到 `data/disabled-<id>.json`，啟用時還原。
  寫入前確認原檔未被改動，先備份成 `*.agentdock-<時間>-<id>.bak`，批次失敗會嘗試復原。
- QAI 是 MCP 庫的內建項目，指向 venv 的 `python.exe -m agentdock.mcp_server`，並設 `PYTHONPATH` 與 `AGENTDOCK_DATA_DIR`。
  設定檔中的名稱是 `inbox`，服務名稱為 Inbox；舊名稱 agentchat／agentdock-qa／agentdock_qa（含 Electron 版）會在「全部更新」預覽確認後換新。
- `ui/card.py`：浮動視窗共用基底，支援 heart / card（舊的 pill 視為 card）。app.py 的 LauncherIcon 固定為小圖示；self.ball 使用 ContentWindow，與橫向 Tab 列一起由 DockLayout 管理顯示／隱藏。內容可用 EdgeGrip（同檔）縮放，有「額度、問答、設定」三頁（QStackedWidget），
  問答頁內嵌 `inbox_view.InboxView`，設定頁內嵌 `agents_view.AgentsView`（外觀只在右鍵選單切換），外部工具成為額外的頁。
  新通知由 app.py 交給 ui/notifications.py 彙整分級數量、主題色符號角標及可選提示音（無文字彈窗）；點擊時依待授權、新問題、舊待答、失敗、完成與其他結果導覽，不自動切頁。提示控制器以穩定請求識別去重，持續保留待處理標記，原生活動失效後移除授權提醒。
  裝飾繪圖由本機主題的 draw_frame、draw_icon、draw_bar 提供，共用程式只包含預設極簡外觀；Ctrl+Alt+H 切回極簡時也關閉舊外觀的選擇題泡泡。全螢幕偵測在 winutil.foreground_is_fullscreen()。
- `ui/theme.py`：內建黑綠極簡外觀，load_private() 從 data/private_theme.py 載入 CONFIG 與三個繪圖函式。對話視窗另可選用 `draw_canvas(p, rect)`、`draw_panel(p, rect, radius)`（對話清單）、`draw_box(p, rect, radius)`（輸入框）、`draw_heading(p, rect, text_rect)`（新對話標題）：`theme.decorated(name)`／`theme.decorate(name, ...)` 只在可愛外觀且本機主題有該函式時使用，畫了的部分樣式表改為透明並留出邊距，函式出錯只記 log、退回一般外觀。另有 `draw_bubble(p, rect)`（自己的訊息）、`draw_round(p, rect, icon, enabled, hovered)`（送出／停止）與 `draw_dialog(p, rect, title, close_rect, hovered)`。
- `ui/cute.py`：App 層級的事件過濾器（TrayApp 安裝）。本機主題有 draw_dialog 時，所有 QDialog（含 QMessageBox、QInputDialog，不含原生檔案對話框）在 Polish 時改為無框透明，由主題畫外框、標題與關閉鈕，標題列可拖曳、點關閉＝reject；可愛外觀下 QMenu 與提示框改透明視窗，圓角才不會露出方角；QMessageBox 按鈕改中文，可愛外觀用 draw_icon 取代系統圖示。`tint_caption()` 在 Windows 11 用 DWM 把主視窗標題列與邊框換成主題色並圓角。檔案缺少、無效或 DEVICE 不符合本機名稱時退回極簡；私人檔案不納入 Git 或設定匯出。
  本機主題可使用額外字型，字型快取放在 data/fonts。新問題通知仍由 app.py 負責。
- `agents_view._track_known()`：ui.json 的 `known_mcp` 記住各 Agent 設定檔裡的 MCP 名稱；少掉的（不是經 AgentDock 移除的）標成「被移除了」並排入「全部更新」的補回。
- `secretbox.py`：祕密檔匯出／匯入（scrypt＋AES-256-GCM，只補缺少的項目）。`library.split_local()` 讓本機絕對路徑跟祕密一樣只存 data/secrets.json，
  `recover_secrets()` 在缺少時從 Agent 設定補回。
- `tools/`：外部工具頁的註冊點。
- `tools/tokengauge/`：額度資料。`gauge.py` 是 TokenGauge 的取數字程式（舊的 tkinter 視窗已移除）；`QuotaModel` 在主行程定時取數字
  （卡片為愛心時不取），寫 `data/tokengauge/latest.json`。同時只有一個行程取數字（OAuth 換發會寫回各 CLI 憑證），另外開著的 TokenGauge 會被關閉。

Inbox 不讀原生聊天；舊 external 事件只保留在儲存檔、不參與顯示與未讀。原生 alias、來源封存清單與標題不再影響問答或結果。App 啟動不重設可見歷史。設定識別統一為 inbox，agentchat 僅作舊設定辨識，服務顯示名為 Inbox。
