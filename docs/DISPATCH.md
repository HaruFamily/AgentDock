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

## 架構

```
InboxView ──點擊──> Dispatcher(App 行程, 背景執行緒) ──HTTP Basic──> opencode service
          <──Qt 信號── DispatchStore(data/dispatch.json) <──SSE /api/event──┘
```

- `dispatch/opencode.py`：讀 `<XDG_STATE_HOME 或 ~/.local/state>/opencode/service.json` 的網址與密碼（不另存），`GET /api/info` 確認版本 ≥ 2。服務沒開時只在使用者送出或接手時執行 `opencode service start`（直接呼叫 npm 套件裡的 opencode.exe，CREATE_NO_WINDOW，不經 .cmd）；App 啟動時不主動啟動服務。連線不經系統 proxy。
- `dispatch/runner.py`：`Dispatcher` 所有網路動作在 daemon 執行緒，失敗經 `notify` 顯示。事件監聽先訂閱再對帳（session、active、permission、form、最近訊息），送出前持有 hold 並等訂閱完成，避免太快完成的回覆漏接；沒有未結束的卡片時停止監聽。
- `dispatch/store.py`：以 `opencode:<session id>` 為鍵；`running` 只在客戶端確認時成立，重啟後為 `unknown`。owner 與該 Agent 的 Inbox MCP／活動 hook 相同（`sha256("agentdock-<profile id>")`），work_id 為 `session:opencode:<id>`，因此 MCP 回報、原生活動與送出的工作階段合併成一張卡；同一工作階段的原生活動卡片不再重複列出。
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
- 開發時另以真實 opencode 2.0.32 服務搭配本機假模型實測完整往返（建立、shell 授權、question、停止、對帳）。尚未在使用者電腦上以真實模型驗收。
