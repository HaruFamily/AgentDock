# 收發：從 Inbox 送任務給 Agent

Inbox 除了接收問答與結果，也能直接送任務、看回覆、批准授權、回答問題、接續對話和停止。第一個接上的是 OpenCode 2.x；Claude Code、Codex 之後依同一套介面加入。

## 使用

- Inbox 右上角「＋」：選資料夾（記住最近 8 個）、輸入內容、送出（Ctrl+Enter）。只有一個可用 Agent 時不顯示選單。
- 卡片顯示狀態、最後一則你送出的內容、Agent 最新回覆（Markdown）。
- 待授權：顯示動作與內容，按「允許」「一律允許」「拒絕」。不按就一直等，不會自動決定。
- 待回答：OpenCode 的 question 工具在卡片上顯示選項與自填欄位，「回答」或「略過」。
- 下方輸入框送出即接續同一個工作階段；進行中可按「停止」。
- OpenCode 原生活動卡片有「接手」：把終端機開的工作階段收進 Inbox，之後可在這裡授權、回答和接續。
- 移除卡片只移除 AgentDock 的卡片，不刪 OpenCode 的工作階段；有待授權、待回答或輸入中的文字時不能移除。

OpenCode 2.x 用一個共用的背景服務（`opencode service`），TUI 也連同一個。所以：

- 關掉 AgentDock 任務仍在跑；重開後卡片先顯示「狀態未知」，連上服務後自動更新，期間完成的會標為未讀。
- 同一個工作階段也能在 OpenCode TUI 打開（`opencode -s <session id>`）。
- 權限規則沿用你的 OpenCode 設定；AgentDock 不加也不放寬規則。

## 對話視窗

系統匣「對話視窗」、Inbox 的「＋」或送出卡片上的「↗」打開。浮動卡片仍負責通知、快速授權與快速回覆。

- 最左窄邊欄：平常只有圖示，滑鼠移上去展開蓋在清單上；聊天、工具在上，設定固定最下方。工具與設定目前打開浮動卡片的額度頁／設定頁。
- 左側清單：「新對話」、搜尋、「專案」（＋新增），每個專案底下是它的對話，不屬於任何專案的放「未分類」。拖曳對話到另一個專案（或未分類）即可移動；移動只整理清單，工作階段原本的資料夾不變。
- 專案（`data/projects.json`）：名稱、根目錄、預設 Agent。在專案裡開新對話一律在根目錄執行。對話歸屬：明確拖過去的為準，否則依工作資料夾落在哪個專案根目錄（取最深的），都不是就未分類。專案頁可改名、更換根目錄、改預設 Agent、刪除專案（對話不刪）。
- 對話：從 OpenCode 讀完整紀錄（每次 60 則，往上可載入更早），新內容即時出字（每秒最多重繪約 12 次）；工具動作收成一行，點開看參數與輸出；授權與 question 顯示在對話最下方；底部輸入框 Enter 送出、Shift+Enter 換行，執行中出現「停止」。貼上檔案會變成檔案路徑文字（附件尚未支援）。草稿依對話保留到 AgentDock 結束。
- 新對話：選 Agent（Claude Code、Codex 顯示但尚未支援）、選專案或未分類（未分類要選資料夾），送出後直接切到該對話。
- 視窗位置大小存在 `ui.json` 的 `chat_geometry`；關閉視窗只是隱藏。

## 架構

```
InboxView ──點擊──> Dispatcher(App 行程, 背景執行緒) ──HTTP Basic──> opencode service
          <──Qt 信號── DispatchStore(data/dispatch.json) <──SSE /api/event──┘
```

- `dispatch/opencode.py`：讀 `<XDG_STATE_HOME 或 ~/.local/state>/opencode/service.json` 的網址與密碼（不另存），`GET /api/info` 確認版本 ≥ 2。服務沒開時只在使用者送出或接手時執行 `opencode service start`（直接呼叫 npm 套件裡的 opencode.exe，CREATE_NO_WINDOW，不經 .cmd）；App 啟動時不主動啟動服務。連線不經系統 proxy。
- `dispatch/runner.py`：`Dispatcher` 所有網路動作在 daemon 執行緒，失敗經 `notify` 顯示。事件監聽先訂閱再對帳（session、active、permission、form、最近訊息），送出前持有 hold 並等訂閱完成，避免太快完成的回覆漏接；沒有未結束的卡片時停止監聽。
- `dispatch/store.py`：以 `opencode:<session id>` 為鍵；`running` 只在客戶端確認時成立，重啟後為 `unknown`。owner 與該 Agent 的 Inbox MCP／活動 hook 相同（`sha256("agentdock-<profile id>")`），work_id 為 `session:opencode:<id>`，因此 MCP 回報、原生活動與送出的工作階段合併成一張卡；同一工作階段的原生活動卡片不再重複列出。
- `dispatch/projects.py` 專案與歸屬；`dispatch/transcript.py` 把儲存的訊息與即時事件轉成對話項目（文字區塊依 ordinal 對齊，避免重複）；`Dispatcher.history()` 以 cursor 分頁（OpenCode 不接受 cursor 與 order 同時出現），`Dispatcher.listeners` 轉送追蹤中工作階段的即時事件。`ui/chat_window.py` 是視窗；背景執行緒的結果一律經 Qt 信號回到 UI 執行緒。
- `QuestionStore.inbox_tasks()` 合併送出卡片；`mark_read`、`remove_conversation` 支援；通知角標把授權算「待授權」、question form 算「待回答」、結束算完成／失敗／取消。

不變的原則：broker HTTP 沒有任何送出、授權或回答路由；批准與回答只來自 UI 點擊；`dispatch` 套件不匯入 PySide6（MCP 端也會載入 store）。

## OpenCode V2 API（2.0.32 實測）

| 用途 | 呼叫 |
| --- | --- |
| 建立 | `POST /api/session {title, location:{directory}}` |
| 送出 | `POST /api/session/{id}/prompt {text}` |
| 停止 | `POST /api/session/{id}/interrupt` |
| 授權 | `GET /api/session/{id}/permission`、`POST …/permission/{rid}/reply {decision: once/always/reject}` |
| 問題 | `GET /api/session/{id}/form`、`POST …/form/{fid}/reply {answer:{key:value}}`、`DELETE …/form/{fid}` |
| 狀態 | `GET /api/session/{id}`（outcome）、`GET /api/session/active`、`GET …/message?order=desc` |
| 事件 | `GET /api/event`：`session.execution.started/succeeded/failed/interrupted`、`session.text.ended`、`session.tool.input.started`、`session.inbox.enqueued`、`permission.asked/replied`、`form.created/replied/cancelled` |

失敗原因在最新一則 `type: idle` 訊息的 `error.message`。公開文件的 V1 路徑（`/session/:id/prompt_async` 等）在 2.x 不適用。

## 驗證

- `tests/test_dispatch.py`：仿 2.0.32 事件格式的假服務，涵蓋送出、接續、授權（允許／拒絕）、question form（回答／略過）、停止、失敗原因、重啟對帳、離線、接手、MCP 合併、服務探索／版本／密碼、無視窗啟動、broker 無送出路由、無 GUI 匯入。
- `tests/test_dispatch_ui.py`：按鈕只在點擊時轉送、表單驗證、輸入框跨刷新保留、移除限制、狀態文字、Composer 驗證、接手按鈕。
- `tests/test_chat_window.py`：專案驗證／歸屬／移除、拖曳移動後保留、對話項目解析與即時事件不重複、歷史分頁、在專案開新對話並即時出字、Enter／Shift+Enter、重開載入紀錄與對話內授權、拖曳與搜尋、未支援 Agent 不送出、邊欄展開收合與設定置底、新增專案與貼上檔案路徑。`test_dock_layout` 另驗證 App 開啟對話視窗、邊欄開設定頁、結束時記住位置。
- 開發時另以真實 opencode 2.0.32 服務搭配本機假模型實測完整往返（建立、shell 授權、question、停止、對帳）。尚未在使用者電腦上以真實模型驗收。
