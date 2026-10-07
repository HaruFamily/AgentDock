# 架構

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
- `userpath.py`：hook 以名稱呼叫工具（`rtk hook claude`），改寫後的指令也是 `rtk ...`，所以工具必須在 PATH 上。
  `data/bin/` 放目前版本的複本，經使用者確認後加入 HKCU PATH；偵測時讀登錄檔的 PATH（本行程的可能過期）。
- `agents.py`：以操作清單（enable/disable/remove/add/update）規劃變更。Codex／OpenCode 用 `enabled` 停用；Claude 系列沒有此旗標，停用時移出設定存到 `data/disabled-<id>.json`，啟用時還原。
  寫入前確認原檔未被改動，先備份成 `*.agentdock-<時間>-<id>.bak`，批次失敗會嘗試復原。
- QAI 是 MCP 庫的內建項目，指向 venv 的 `python.exe -m agentdock.mcp_server`，並設 `PYTHONPATH` 與 `AGENTDOCK_DATA_DIR`。
  設定檔中的名稱是 `inbox`，服務名稱為 Inbox；舊名稱 agentchat／agentdock-qa／agentdock_qa（含 Electron 版）會在「全部更新」預覽確認後換新。
- `ui/card.py`：浮動視窗共用基底，支援 heart / card（舊的 pill 視為 card）。app.py 的 LauncherIcon 固定為小圖示；self.ball 使用 ContentWindow，與橫向 Tab 列一起由 DockLayout 管理顯示／隱藏。內容可用 EdgeGrip（同檔）縮放，有「額度、問答、設定」三頁（QStackedWidget），
  問答頁內嵌 `inbox_view.InboxView`，設定頁內嵌 `agents_view.AgentsView`（外觀只在右鍵選單切換），外部工具成為額外的頁。
  新通知由 app.py 更新小圖示提醒；點擊開啟時才依新問題、舊待答、待授權、結果的順序導覽，不自動切頁。
  裝飾繪圖由本機主題的 draw_frame、draw_icon、draw_bar 提供，共用程式只包含預設極簡外觀；Ctrl+Alt+H 切回極簡時也關閉舊外觀的選擇題泡泡。全螢幕偵測在 winutil.foreground_is_fullscreen()。
- `ui/theme.py`：內建黑綠極簡外觀，load_private() 從 data/private_theme.py 載入 CONFIG 與三個繪圖函式。檔案缺少、無效或 DEVICE 不符合本機名稱時退回極簡；私人檔案不納入 Git 或設定匯出。
  本機主題可使用額外字型，字型快取放在 data/fonts。新問題通知仍由 app.py 負責。
- `agents_view._track_known()`：ui.json 的 `known_mcp` 記住各 Agent 設定檔裡的 MCP 名稱；少掉的（不是經 AgentDock 移除的）標成「被移除了」並排入「全部更新」的補回。
- `secretbox.py`：祕密檔匯出／匯入（scrypt＋AES-256-GCM，只補缺少的項目）。`library.split_local()` 讓本機絕對路徑跟祕密一樣只存 data/secrets.json，
  `recover_secrets()` 在缺少時從 Agent 設定補回。
- `tools/`：外部工具頁的註冊點。
- `tools/tokengauge/`：額度資料。`gauge.py` 是 TokenGauge 的取數字程式（舊的 tkinter 視窗已移除）；`QuotaModel` 在主行程定時取數字
  （卡片為愛心時不取），寫 `data/tokengauge/latest.json`。同時只有一個行程取數字（OAuth 換發會寫回各 CLI 憑證），另外開著的 TokenGauge 會被關閉。

Inbox 不讀原生聊天；舊 external 事件只保留在儲存檔、不參與顯示與未讀。原生 alias、來源封存清單與標題不再影響問答或結果。App 啟動不重設可見歷史。設定識別統一為 inbox，agentchat 僅作舊設定辨識，服務顯示名為 Inbox。
