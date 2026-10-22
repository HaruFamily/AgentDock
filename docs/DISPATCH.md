# 收發：在 AgentDock 對 Agent 送任務

對話視窗可以對 OpenCode、Claude Code、Codex、Grok 送任務、看即時回覆、批准授權、回答問題、接續對話和停止。各家走自己的 CLI 協定，在 AgentDock 裡長得一樣。

## 使用

- 系統匣圖示或 `AgentDock.exe` 打開對話視窗；關閉視窗只是收回系統匣。
- 最左窄邊欄：平常只有圖示，滑鼠移上去展開；聊天、工具在上，設定固定最下方。
- 左側清單：「新對話」、搜尋、「專案」（＋新增），每個專案底下是它的對話，不屬於任何專案的放「未分類」。拖曳對話到另一個專案（或未分類）即可移動；移動只整理清單，工作階段原本的資料夾不變。
- 專案（`data/projects.json`）：名稱、根目錄、預設 Agent。在專案裡開新對話一律在根目錄執行。對話歸屬：明確拖過去的為準，否則依工作資料夾落在哪個專案根目錄（取最深的），都不是就未分類。
- 新對話：選 Agent（OpenCode／Claude Code／Codex／Grok）、選專案或未分類（未分類要選資料夾）。在未分類送出時記住這次的 Agent，下次預設用它；專案則用專案的預設 Agent。
- 對話：完整紀錄（每次 60 則，往上可載入更早），新內容即時出字；工具動作收成一行，點開看參數與輸出；授權與問題顯示在對話最下方；Enter 送出、Shift+Enter 換行，執行中出現「停止」。貼上檔案會變成檔案路徑文字（附件尚未支援）。
- 授權：「允許」「一律允許」「拒絕」。不按就一直等，不會自動決定。問題：選項或自填，「回答」或「略過」。

## 各家的差別

| | OpenCode 2.x | Claude Code | Codex | Grok |
| --- | --- | --- | --- | --- |
| 怎麼接 | 共用背景服務 `opencode service`（HTTP＋SSE） | 每個使用中的對話一個 `claude` 子行程（SDK 的 stream-json 協定） | 一個 `codex app-server` 子行程服務全部對話（JSON-RPC） | 一個 `grok agent stdio` 子行程服務全部對話（ACP，JSON-RPC） |
| 關掉 AgentDock | 任務繼續跑；重開後先「狀態未知」再對帳 | 這一輪中斷；重開後標「AgentDock 結束時中斷」，可接續 | 同 Claude Code | 同 Claude Code |
| 接續 | 同一工作階段 | `--resume <session>`；閒置 10 分鐘自動關掉行程，下次送出再接上 | `thread/resume` | `session/load`（重播紀錄後接著用） |
| 紀錄從哪讀 | 服務 API | `~/.claude/projects/<資料夾>/<session>.jsonl`（`CLAUDE_CONFIG_DIR` 優先） | `thread/read` | `session/load` 的重播（Grok 存在 `~/.grok/sessions`） |
| 「一律允許」 | OpenCode 的 always | 只套用 Claude Code 建議的「新增規則」（寫進專案 `.claude/settings.local.json`），不切換權限模式 | `acceptForSession`（本次工作階段） | Grok 的 allow_always 選項（照 Grok 自己的規則記住） |
| 問題 | question 工具 | AskUserQuestion | request_user_input（Codex 實驗功能，有出現才會顯示） | ask_user_question（`_x.ai/ask_user_question`） |
| 在原工具打開 | `opencode -s <id>` | `claude --resume <id>` | `codex resume <id>` | `grok --resume <id>` |

共通：

- 權限規則沿用你在各工具裡的設定（OpenCode 規則、Claude Code 權限模式與 allow 規則、Codex approval policy／sandbox）；AgentDock 不加也不放寬。設定成不詢問的動作就不會出現授權卡。
- 執行檔：直接找真正的 exe，不經 npm 的 .cmd（避免閃視窗），一律 CREATE_NO_WINDOW。
  - Claude Code：PATH 上 `claude` 旁的 `node_modules\@anthropic-ai\claude-code\bin\claude.exe`、`%APPDATA%\npm` 同位置、`~\.local\bin\claude.exe`。
  - Grok：官方安裝程式的 `~\.grok\bin\grok.exe`、npm `@xai-official/grok` 解開的 `grok-native`、PATH 上的 `grok`。
  - Codex：PATH 上獨立安裝的 `codex.exe`、npm 的 `node_modules\@openai\codex*\…\codex.exe`、`%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe`（取最新），最後才用 PATH 上的 shim。
  - 找不到時送出會顯示「找不到 Claude Code（claude）」等訊息。
- 子行程意外結束：這一輪標為失敗，訊息附上最後幾行錯誤輸出；下一則訊息會重新啟動並接續。
- 移除對話只移除 AgentDock 的紀錄，不刪各工具裡的工作階段。

## 架構

```
ChatWindow ──點擊──> Dispatcher(App 行程, 背景執行緒)
                       ├─ OpenCode：HTTP Basic + SSE ──> opencode service
                       ├─ ClaudeClient：stdin/stdout JSON lines ──> claude（每個對話一個）
                       ├─ CodexClient：JSON-RPC lines ──> codex app-server（共用一個）
                       └─ GrokClient：ACP（JSON-RPC lines）──> grok agent stdio（共用一個）
           <──Qt 信號── DispatchStore(data/dispatch.json) <── 統一成 OpenCode V2 形狀的事件
```

- `dispatch/runner.py`：`Dispatcher` 依 key 前綴（`opencode:`、`claude-code:`、`codex:`）分派；所有動作在 daemon 執行緒，失敗經 `notify` 顯示。`clients` 是可收發的 Agent。`resume()` 只對 OpenCode 開監聽，其他兩家未完成的卡片直接標中斷。
- `dispatch/adapter.py`：轉接器介面（`new_session`、`prompt`、`interrupt`、`reply_permission`、`reply_form`、`history`、`close`）與子行程包裝（JSON lines、stderr 尾巴、無視窗）。轉接器把各家輸出轉成 OpenCode V2 的事件（`session.text.delta`、`session.tool.called`、`permission.asked`、`form.created`、`session.execution.*`…）與訊息（`user`／`assistant`／`idle`），所以 store、通知、transcript 與對話畫面不用分家處理。
- `dispatch/claude.py`：Claude Agent SDK 的協定，直接實作（SDK 套件會再帶一份 CLI）。指令 `claude --output-format stream-json --verbose --input-format stream-json --include-partial-messages --permission-prompt-tool stdio --session-id|--resume <id>`，環境 `CLAUDE_CODE_ENTRYPOINT=sdk-py`、移除 `CLAUDECODE`。先送 `initialize` control request；使用者訊息帶自訂 uuid，與紀錄檔的 uuid 一致，重讀紀錄不重複。`can_use_tool` 轉授權或表單（AskUserQuestion，回覆 `updatedInput.answers`，多選以逗號串接），`control_cancel_request` 撤掉卡片，停止送 `interrupt`。子代理（`parent_tool_use_id`）的內容不顯示。紀錄沿 `parentUuid` 從最新主線節點往回組，略過 sidechain、meta、指令輸出；同一個 API message 的多筆紀錄合併。
- `dispatch/codex.py`：`initialize`→`initialized`→`thread/start {cwd}`／`thread/resume`→`turn/start`；`turn/interrupt` 需要 turn id。通知 `turn/started`、`item/started|completed`、`item/agentMessage/delta`、`turn/completed`、`error`、`serverRequest/resolved`。伺服器請求 `item/commandExecution/requestApproval`、`item/fileChange/requestApproval`、`item/permissions/requestApproval`（以及舊版 `execCommandApproval`／`applyPatchApproval`）轉授權；`item/tool/requestUserInput` 轉表單；MCP elicitation 一律婉拒，其他回不支援。指令顯示 `commandActions` 的原始指令，不含 Codex 加的 shell 外殼。每個項目（訊息、指令、檔案修改、MCP 工具）各自是一個 assistant 區塊。
- `dispatch/grok.py`：ACP。`initialize`（不提供 fs／terminal，Grok 用自己的工具）→ `session/new {cwd, mcpServers: []}`（不設 yoloMode）／`session/load`→ `session/prompt`（回應帶 stopReason：end_turn、cancelled、refusal；錯誤回應為失敗）；停止送 `session/cancel` 並把等待中的授權回 cancelled。`session/update` 的 `agent_message_chunk`、`tool_call`、`tool_call_update` 依 `_meta.promptId` 歸到同一則 assistant，文字依中間的工具分段編號，所以重播和即時事件對得上。授權為 `session/request_permission`（選 allow_once／allow_always／reject_once 的 optionId）；問題為 Grok 擴充 `_x.ai/ask_user_question`，回 `{outcome: "accepted", answers: {問題: 選項或文字}}` 或 `{outcome: "cancelled"}`。其他 `_x.ai/*` 請求回不支援。
- 子行程一律由同一條常駐執行緒啟動：grok 會在啟動它的執行緒結束時跟著被終止（Linux 實測），送訊息用的工作執行緒是短命的。
- `dispatch/opencode.py`：讀 `<XDG_STATE_HOME 或 ~/.local/state>/opencode/service.json` 的網址與密碼，`GET /api/info` 確認版本 ≥ 2；只在送出時執行 `opencode service start`。事件監聽先訂閱再對帳，送出前等訂閱完成。
- `dispatch/store.py`：以 `<client>:<session id>` 為鍵；owner 為 `sha256("agentdock-<profile id>")`（沒有對應設定檔時用 `agentdock-<client>`）；work_id `session:<client>:<id>`。
- `dispatch/projects.py` 專案與歸屬；`dispatch/transcript.py` 把訊息與即時事件轉成對話項目（文字區塊依 ordinal 對齊、id 去重）。`ui/chat_window.py` 是視窗；背景執行緒的結果一律經 Qt 信號回到 UI 執行緒。

不變的原則：broker HTTP 沒有任何送出、授權或回答路由；批准與回答只來自 UI 點擊；`dispatch` 套件不匯入 PySide6。

## OpenCode V2 API（2.0.32 實測）

| 用途 | 呼叫 |
| --- | --- |
| 建立 | `POST /api/session {title, location:{directory}}` |
| 送出 | `POST /api/session/{id}/prompt {text}` |
| 停止 | `POST /api/session/{id}/interrupt` |
| 授權 | `GET /api/session/{id}/permission`、`POST …/permission/{rid}/reply {decision: once/always/reject}` |
| 問題 | `GET /api/session/{id}/form`、`POST …/form/{fid}/reply {answer:{key:value}}`、`DELETE …/form/{fid}` | ask_user_question（`_x.ai/ask_user_question`） |
| 狀態 | `GET /api/session/{id}`（outcome）、`GET /api/session/active`、`GET …/message?limit=`（cursor 與 order 不能同時給） |
| 事件 | `GET /api/event`：`session.execution.started/succeeded/failed/interrupted`、`session.text.*`、`session.tool.*`、`session.inbox.enqueued`、`permission.asked/replied`、`form.created/replied/cancelled` |

失敗原因在最新一則 `type: idle` 訊息的 `error.message`。

## 驗證

- `tests/test_dispatch_clients.py`：以 `tests/fakes/` 的假 claude、假 codex app-server（照兩家協定）測送出、接續、即時事件與紀錄一致不重複、授權（允許／一律允許／拒絕，等點擊）、檔案修改授權、問題（回答／略過）、停止、失敗原因、子行程當掉後重啟並接續、找不到執行檔、重啟後標中斷且不碰 OpenCode、失效授權移除、紀錄檔解析、Codex 項目轉換、執行檔搜尋，以及對話視窗用 Claude Code／Codex 送出、在對話內按允許。
- `tests/test_dispatch.py`、`test_dispatch_ui.py`、`test_chat_window.py`：OpenCode 與對話視窗。
- Grok 另有假 `grok agent stdio`（`tests/fakes/fake_grok.py`），涵蓋上述項目與重開後以 session/load 重播紀錄、文字與工具分段編號。
- 開發時另以真實 Claude Code 2.1.288、Codex 0.163.0、Grok 1.0.67 搭配本機假模型 API（Anthropic Messages／OpenAI Responses 串流；Grok 用 `[model.*]` 自訂模型指到假伺服器）實測：建立、即時出字、指令授權（含「一律允許」）、問題、停止、重開後接續與讀紀錄。尚未在使用者電腦上以真實模型驗收。
