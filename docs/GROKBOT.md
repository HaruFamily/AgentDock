# GrokBot 接入

2026-10-18，依本機 Grok Bot 0.71.2 的 `main-app.cjs`、`main-core.cjs` 與 `proto.cjs` 核對。

## 支援範圍

- 設定 → GrokBot → 管理 MCP：讀取目前登入帳號的 MCP，新增、編輯、移除自訂遠端 HTTPS MCP，從 MCP 庫加入遠端項目，或從備份預覽還原。接受 http／sse 類型與 Headers。
- 每次變更單獨預覽、確認、備份、套用及回讀，不參與本機設定的「全部更新」。同帳號的其他客戶端也可能讀取此設定；網址與 Headers 會送往帳號服務。
- 額度頁自動偵測桌面版登入，顯示每週用量／重置時間與方案；統一訂閱標示共用額度。缺少個人百分比、企業共用池、登入過期與網路錯誤都不偽裝為零用量。
- 雲端指令型 MCP、外掛管理的 MCP 及含未支援欄位的遠端項目只列出並保留，不能透過此介面覆寫。沒有實作開關旗標，以免把移除誤稱為停用。
- 尚未找到已驗證的桌面 hook 設定／事件接入，因此不把 Grok Build CLI 的 `.grok/hooks` 套用到 GrokBot，也不宣稱提供 Inbox 原生活動提醒。

GrokBot 的電腦位於雲端，見 [官方 Grok Bot 101](https://x.ai/bot/guides/grok-bot-101)。本機程式也明確將 stdio MCP 放入雲端電腦；因此 Windows 本機的 Inbox、rtk、資料夾路徑不能原樣加入。沒有新增公網通道或修改本機 broker 的存取限制。

## 實作與帳號邊界

`agentdock/grokbot.py` 不依賴 PySide6。`ui/grokbot_view.py` 的網路讀寫在背景執行，傳輸中不允許關閉對話框。新增設定仍在使用者確認後才送出；打開管理介面與額度輪詢只有讀取。

讀取 `%APPDATA%/Grok Bot/sand-secrets.json`，支援 `SAND_USER_DATA_DIR`；依 `cursor-accounts.active` 選擇帳號。以同一 Windows 使用者的 DPAPI 解開 `Local State` 內的金鑰，再解析 Electron v10 AES-GCM 登入值。驗證 JWT principal 的 SHA-256 與 active account 相符、token 未過期，並保留所選團隊。只在記憶體使用 access token，不存入 AgentDock、不讀取 refresh token、不更新 GrokBot 憑證。過期時請開啟 GrokBot 讓它自行更新登入。

固定呼叫 `https://api2.cursor.sh/aiserver.v1.DashboardService/` 的三個方法：

- `GetMcpConfig`：`teamScope=false`、`redactSecrets=false`，完整保留原設定與 `serverMetadataByName`。
- `SetMcpConfig`：單次寫入個人 MCP 設定，保留仍存在項目的 `serverIdsByName`；不修改團隊 MCP 政策。
- `GetSandUsageStatus`：取得 `usagePercent`、`nextResetTimestampUtc`、方案及共用池資訊。統一方案的共享額度亦見 [官方訂閱說明](https://docs.x.ai/x-subscription/grok-bot)。

禁止 HTTP 重新導向，不把登入 token 傳給任意主機；限制回應大小及逾時，錯誤不輸出 HTTP body 或憑證。這是已安裝客戶端使用的非公開端點，格式改變時會拒絕處理。

## 預覽、備份與失敗處理

備份存於 `data/grokbot-backups/`，包含原 MCP 設定與 metadata，可能含原 MCP 的 Headers；不含 GrokBot 登入 token，不提交 Git。

套用前及備份後各重讀一次，逐項比對帳號、團隊、設定與 metadata。不同則中止。服務沒有 revision／compare-and-swap 參數，兩次查詢無法提供跨客戶端的原子鎖；確認畫面明示不要同時在其他客戶端編輯。

寫入逾時或回讀不符時，不能推定服務未寫入，也不能安全覆寫其他客戶端的改動。因此不自動重送或盲目復原，而是保留備份、顯示結果未確認，使用者重新整理後可「從備份還原」，再次預覽確認。移除後還原 MCP 不保證恢復原 OAuth 授權，可能需重新登入。

## 驗證

實機只讀查詢已取得目前帳號的 MCP 設定及每週用量，未執行任何真實帳號寫入。新增／移除／更新、備份、還原、衝突、模糊失敗、格式拒絕、登入解析與 UI 取消均以暫存目錄及隔離假服務測試；真實帳號寫入仍待使用者在介面預覽確認後驗收。
