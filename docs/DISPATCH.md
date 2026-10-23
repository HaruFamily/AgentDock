# 收發：在 AgentDock 對 Agent 送任務

對話視窗可以對 OpenCode、Claude Code、Codex、Grok 送任務、看即時回覆、批准授權、回答問題、接續對話和停止。各家走自己的 CLI 協定，在 AgentDock 裡長得一樣。

## 使用

- 系統匣圖示或 `AgentDock.exe` 打開對話視窗；關閉視窗只是收回系統匣。
- 最左窄邊欄：只有圖示（滑鼠停留看名稱）；聊天、工具在上，設定固定最下方。聊天頁再點一次「聊天」或按 Ctrl+B 收起／展開對話清單（記住），收起時窄邊欄多一個「＋」開新對話。清單右緣可拖拉寬度（220–460，記住）。
- 左側清單：「新對話」、搜尋、「專案」（＋新增），每個專案底下是它的對話，不屬於任何專案的放「未分類」。拖曳對話到另一個專案（或未分類）即可移動；移動只整理清單，工作階段原本的資料夾不變。每個對話右邊是狀態（待授權、進行中、失敗）或最後更新時間（剛剛、5 分、14:03、昨天、10/20）；滑鼠移上去時換成「⋯」，內容同右鍵選單。釘選的對話集中在最上方「釘選」（`dispatch.json` 的 `pinned`）。
- 對話的右鍵選單：釘選／取消釘選、改名（F2；改過的名字不再被 Agent 的標題蓋掉）、移到專案、複製工作階段 ID、在終端機接續（Windows；用該 Agent 自己的指令開新視窗，例如 `claude --resume <id>`，執行中不可用）、刪除（Delete）。專案的右鍵選單：在此專案開新對話、專案設定。
- 刪除：預設只從 AgentDock 移除；勾「同時刪除 X 裡的紀錄」才刪 Agent 那邊的工作階段（OpenCode `DELETE /api/session/{id}`、Claude Code 刪紀錄檔與同名資料夾、Codex `thread/delete`、Grok `_x.ai/session/delete`），成功後才從清單移除。執行中的對話不能刪。
- 專案（`data/projects.json`）：名稱、根目錄、預設 Agent。在專案裡開新對話一律在根目錄執行。對話歸屬：明確拖過去的為準，否則依工作資料夾落在哪個專案根目錄（取最深的），都不是就未分類。
- 新對話（版面同 Claude／Codex 桌面版）：標題、輸入框（底列左邊「＋」附加檔案與權限，右邊模型・推理與圓形送出），輸入框下方「📁 專案或資料夾 ▾」（列出專案、最近用過的資料夾、選擇資料夾…、新增專案…）與「Agent ▾」，最下面「最近」三則對話可直接點回去。在未分類送出時記住這次的 Agent，下次預設用它；專案則用專案的預設 Agent。
- 對話：完整紀錄（每次 60 則，往上可載入更早），新內容即時出字；工具動作收成一行，點開看參數與輸出；授權與問題顯示在對話最下方；Enter 送出、Shift+Enter 換行；右下角一顆圓鈕，執行中且沒打字時是 ■ 停止，打了字就是 ↑ 送出。往上捲超過一段時右下角出現「↓」回到最新。執行中也能繼續送：OpenCode、Claude Code、Grok 排隊依序處理，Codex 用 `turn/steer` 加進正在跑的那一輪。
- 訊息：滑鼠移上去出現「複製」（自己的訊息另有「編輯」：放回輸入框改完再送，原訊息保留）；回覆裡的程式碼區塊各自有「複製」，不換行、可左右捲。最後一輪失敗或停止時旁邊有「重試」，重送最後一則訊息。
- 輸入：空白輸入框按 ↑ 叫回上一則自己的訊息。開頭打 `/` 列出指令（AgentDock 的 `/compact`，加上 Agent 回報的指令：目前是 Claude Code initialize 的 `commands`），打 `@` 列出對話資料夾裡的檔案（略過 .git、node_modules、venv、runtime 等，最多 5000 個、快取 30 秒）；↑↓ 選、Tab／Enter 帶入、Esc 關閉。
- 附件：輸入框左下的「＋」、拖曳檔案到視窗任何地方、貼上檔案或截圖（截圖存在 `data/attachments/chat/`）。送出前是一排可點掉的標籤，送出後顯示在自己的訊息下方。圖片（png、jpg、gif、webp，5 MB 內）直接給模型看：Claude Code 為 base64 圖片區塊、Codex 為 `localImage`、Grok 為 ACP image 區塊；其他檔案給路徑讓 Agent 自己讀（OpenCode 用 prompt 的 `files`、Grok 用 `resource_link`、Claude Code 與 Codex 在訊息後列出「附件：路徑」）。OpenCode 的圖片同樣走 `files`。
- 模型、推理強度、權限模式：輸入框底列兩顆晶片（右邊模型與推理同一個選單、左邊權限），「預設…」代表用該 Agent 自己的設定。清單來自各 Agent（Claude Code 的 initialize 回應、Codex `model/list`、Grok `_x.ai/models/list`、OpenCode `/api/model` 與 `/api/agent`），記在 `dispatch.json` 的 `options`，下次先用記住的再更新。推理強度依所選模型列出。新對話時的選擇依 Agent 記住（`ui.json` 的 `new_settings`）；對話中改了立刻套用：Claude Code 用 `set_model`、`apply_flag_settings`（effortLevel）、`set_permission_mode`，或下次啟動時帶 `--model`／`--effort`／`--permission-mode`；Codex 在下一輪 `turn/start` 帶 model、effort、approvalPolicy、sandboxPolicy（唯讀／自動／完全存取＝Codex 自己的三種預設）；Grok 用 `session/set_config_option`，權限模式（詢問／自動判斷／全部允許）只能在開新對話時選；OpenCode 用 `/model`（推理強度＝模型 variant）與 `/agent`（Build、Plan 等）。
- 思考過程：模型的推理（Claude Code thinking、Codex reasoning 摘要、Grok thought、OpenCode reasoning）收成「思考過程」一行，點開才看。
- 檔案修改：編輯類工具點開會顯示上色的差異（Codex、Grok 由 Agent 給，Claude Code、OpenCode 由舊／新文字算出）。
- 壓縮對話：標題列「⋯」→「壓縮對話」，或直接送出 `/compact`。OpenCode `/compact`、Codex `thread/compact/start`、Claude Code 與 Grok 送 `/compact` 指令。
- 分叉：回覆上的「分叉」把對話到那一輪為止複製成新對話（標題加「（分叉）」，同資料夾、專案與設定），原對話不變。以一輪為單位，避免停在工具呼叫與結果之間。Claude Code 複製紀錄檔（新 uuid、保留 parentUuid 鏈、`forkedFrom`、custom-title，與 Claude Code 自己的分叉相同）；Codex `thread/fork` 後以 `thread/revert` 拿掉之後的回合；Grok `_x.ai/session/fork` 的 `targetPromptIndex`；OpenCode `POST /api/session/{id}/fork`，`before` 為下一則使用者訊息。執行中的對話不能分叉。
- 匯入對話：專案標題旁「⇩」、專案右鍵「匯入對話到此專案…」或專案頁「匯入對話」。列出 Agent 自己存的工作階段（Claude Code 掃 `~/.claude/projects/*/*.jsonl`；Codex `thread/list`；Grok `session/list`；OpenCode `GET /api/session`，不含子工作階段），可搜尋；從專案開時預設只列專案資料夾內的。已在 AgentDock 的不能再選。匯入後狀態為完成、直接打開，之後像一般對話接續。
- 上下文：標題列「上下文 N%」為 Agent 最後回報的上下文用量（80% 以上變紅；滑鼠停留看 token 數）。Claude Code 每輪結束送 `get_context_usage`（與 `/context` 同）；Codex `thread/tokenUsage/updated` 的 `last.totalTokens`／`modelContextWindow`；Grok `session/update` 的 `_meta.totalTokens`，上限取模型清單的 `totalContextTokens`；OpenCode 每輪結束讀最後一則回覆的 tokens（input＋output＋reasoning＋cache），上限取模型的 `limit.context`。不知道上限時只顯示 token 數。
- Git：對話資料夾是 Git 儲存庫時，標題列顯示「分支 · N 個變更 · ↑↓」，Agent 每輪結束後更新；點下去（或專案頁「Git」）開 Git 視窗：變更分「已暫存」「變更」，點檔案看差異，雙擊或右鍵暫存／取消暫存，右鍵「捨棄變更…」（確認後才做），提交、推送（沒有上游時 `push -u origin <分支>`）、拉取（只快轉）、切換或新增分支。都是一般 `git` 指令（`agentdock/gitops.py`），只在點擊時執行。
- 搜尋：左側搜尋也比對對話最後一則訊息、回覆、Agent 與資料夾；對話內 Ctrl+F 在已載入的訊息裡找，Enter／↓ 下一個，Esc 關閉。
- 快捷鍵：Ctrl+N 在目前對話的專案（或資料夾）用同一個 Agent 開新對話；Ctrl+K 打開搜尋面板（邊打邊列出對話，↑↓ 選、Enter 開啟、Esc 或點外面關閉）；Ctrl+B 收起／展開清單；Esc 停止目前對話（搜尋框有字時先清搜尋）；Alt+↑／↓ 切換上一個／下一個對話。
- 重開視窗回到上次開著的對話（只在視窗第一次顯示時；開機只在系統匣時什麼都不載入）。
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
- `tests/test_batch3.py`：Claude Code 分叉紀錄檔（整輪、新 id、鏈結）、列出紀錄、Grok 提示序號、Codex／Grok 分叉後各自接續、上下文用量、匯入清單與接手、OpenCode 分叉與列出（假服務）、`gitops` 與 Git 視窗提交；`test_chat_features.py` 另測對話視窗的分叉按鈕、上下文與 Git 標籤、匯入視窗。
- 開發時另以真實 Claude Code 2.1.288、Codex 0.163.0、Grok 1.0.67 搭配本機假模型 API（Anthropic Messages／OpenAI Responses 串流；Grok 用 `[model.*]` 自訂模型指到假伺服器）實測：建立、即時出字、指令授權（含「一律允許」）、問題、停止、重開後接續與讀紀錄；分叉後新對話只含到該輪且可接續、原對話不變、列出工作階段、上下文用量（另以真實 OpenCode serve 測分叉、列出）。尚未在使用者電腦上以真實模型驗收。
