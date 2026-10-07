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
- AgentChat 接收：`report_to_user` → `POST /chat/events` → `QuestionStore.receive()`；呼叫者身分來自 broker header，事件內不能指定 owner。`chat.json` 保存事件、已讀及隱藏問題 ID；依 `(owner, work_id)` 與原有問答合併成對話。
- `ui/qa_view.py` 顯示對話列表、任務歷史與既有問答編輯器。事件去重依 owner／work_id／request_key，內容不同會拒絕；待答時拒絕正常結束回報。進行中只在本次行程由事件設定，重開不猜測執行狀態。讀取標記僅對可見完成訊息生效。
- `chat_sync.py`：App 啟動背景接收執行緒，每 2 秒檢查本機檔案變更，未變更不重讀內容、不更新 UI。Codex 讀取 `state_*.sqlite` 的 name/title、rollout_path 與 history_mode，paginated 紀錄取 item_completed 的原生訊息，legacy 取 user_message/agent_message；task_complete 才標記結束。Claude Code 取主對話 JSONL 的文字、end_turn、custom-title/ai-title；OpenCode 用 `mode=ro`、query_only SQLite 同一讀取交易取 session/message/part，不使用登入憑證。排除子代理、工具與思考內容。
- 接收器的 `chat-sync.json` 記錄 JSONL 位移及資料庫版本戳；只提交完整行，先匯入再存位移，重播以原生 ID 去重。`QuestionStore.import_chat()` 原子更新 chat.json，保留原始時間與已讀；移除留下 sync_removed 標記防止舊訊息重現。原生對話 metadata 與 MCP alias 也存於 chat.json。問答連結依相同 owner 的 literal work_id/request_key 或原始 MCP 結果 ID；不以標題／文字猜測。開始呼叫使用變數而無法解析時，等工具結果才能連結。
- 同步只新增本機讀取，不寫客戶端設定、不發送任務。來源失敗重試並在 AgentChat 標頭提示；Qt 信號在主執行緒合併重繪，內容變更不自動縮放或搶焦點。
- 聊天讀取以已提交的不可變 `_chat`／`_questions` 快照建立快取索引；UI 用 `conversation_summaries()` 與 `conversation_page()`，不呼叫全歷史匯出 `conversations()`，也不等待寫入鎖。接收與已讀以 copy-on-write 替換事件，禁止修改已發布的事件物件。已讀由獨立背景工作合併寫入，退出時 `flush_reads()` 等待完成。對話以固定上界分頁及訊息 ID 重用 QWidget，避免每次串流或捲動重建全部泡泡。
- 每個成功讀取的來源清單透過 `set_source_sessions()` 更新 `sync_visible`；Codex 以未封存 threads、OpenCode 以未封存主 session、Claude Code 以仍存在的主紀錄檔為準。已隱藏的紀錄仍保存在內部，不改問答狀態。已存在但為空的 Codex 索引是有效空清單，不能回退掃描殘留 rollout；曾有索引後暫時找不到時視為讀取失敗，保留顯示快照。日期不參與顯示篩選。
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
  設定檔中的名稱是 `agentchat`，服務名稱為 AgentChat；舊名稱 agentdock-qa／agentdock_qa（含 Electron 版）會在「全部更新」預覽確認後換新。
- `ui/card.py`：浮動視窗（app.py 以 self.ball 驅動）。兩種模式 heart / card（舊的 pill 視為 card）；展開時由 app.py 的 LauncherIcon 保留獨立小圖示，點擊切換顯示／隱藏，位置存 ui.json icon_pos。隱藏時原視窗以 heart 接替圖示。card 可用 EdgeGrip（同檔）縮放（大小存 ui.json card_size），有「額度、問答、設定」三頁（QStackedWidget），
  問答頁內嵌 `qa_view.QaView`，設定頁內嵌 `agents_view.AgentsView`（外觀只在右鍵選單切換），外部工具成為額外的頁。
  `question_arrived()` 決定新問題的呈現（小圖示發光，簡單單選題用 `Bubble` 泡泡；浮動視窗切頁並記住 `return_page`），不搶焦點。
  裝飾繪圖由本機主題的 draw_frame、draw_icon、draw_bar 提供，共用程式只包含預設極簡外觀；Ctrl+Alt+H 切回極簡時也關閉舊外觀的選擇題泡泡。全螢幕偵測在 winutil.foreground_is_fullscreen()。
- `ui/theme.py`：內建黑綠極簡外觀，load_private() 從 data/private_theme.py 載入 CONFIG 與三個繪圖函式。檔案缺少、無效或 DEVICE 不符合本機名稱時退回極簡；私人檔案不納入 Git 或設定匯出。
  本機主題可使用額外字型，字型快取放在 data/fonts。新問題通知仍由 app.py 負責。
- `agents_view._track_known()`：ui.json 的 `known_mcp` 記住各 Agent 設定檔裡的 MCP 名稱；少掉的（不是經 AgentDock 移除的）標成「被移除了」並排入「全部更新」的補回。
- `secretbox.py`：祕密檔匯出／匯入（scrypt＋AES-256-GCM，只補缺少的項目）。`library.split_local()` 讓本機絕對路徑跟祕密一樣只存 data/secrets.json，
  `recover_secrets()` 在缺少時從 Agent 設定補回。
- `tools/`：外部工具頁的註冊點。
- `tools/tokengauge/`：額度資料。`gauge.py` 是 TokenGauge 的取數字程式（舊的 tkinter 視窗已移除）；`QuotaModel` 在主行程定時取數字
  （卡片為愛心時不取），寫 `data/tokengauge/latest.json`。同時只有一個行程取數字（OAuth 換發會寫回各 CLI 憑證），另外開著的 TokenGauge 會被關閉。
