# Grok CLI 管理

設定 → 新增 Agent → Grok CLI。預設讀取 `$GROK_HOME/config.toml`，未指定時為 `~/.grok/config.toml`；可自行選擇設定檔。MCP 管理原生 `[mcp_servers]` 的本機指令與遠端網址，保留 TOML 註解。啟用／停用同步處理 `enabled` 與 `disabled_mcp_servers`；移除後保留停用名稱，避免 Claude／Cursor 相容設定的同名項目重新生效。

擴充庫可新增 Grok CLI command hook，寫入設定檔旁的 `hooks/agentdock.json`。Inbox 原生活動範本支援回合、工具活動、授權提醒、停止／失敗／中斷與工作階段結束。解析 Grok 的 camelCase 事件；匯入的 Claude Inbox hook 遇到 Grok 事件時忽略，避免重複顯示。只傳活動與 Inbox 工作識別，不傳聊天內容、不代替批准。其他 hooks 檔案不變；Grok 的信任與組織政策仍由客戶端決定。

MCP 和 hooks 沿用預覽、確認、備份、衝突檢查及失敗復原流程。這項功能不安裝 Grok CLI，不修改 Claude／Cursor 相容設定，也未替未驗證的 RTK／Codebase Memory Grok adapter 加入範本。

## 額度

額度卡統一標示 Claude 與 Grok。Claude 仍由既有登入取得訂閱用量，設定頁的 Claude Code 與 Desktop 登錄維持各自設定。

GrokBot 回傳 `isUnified=true` 且資料有效時，作為目前個人 Grok 共用訂閱的主要來源，不再另列無帳號識別的預設 xAI 額度估計。具有明確不同 identity 的帳號保留；不以使用百分比相同推斷同帳號。這是單一個人訂閱的來源選擇，不是跨服務帳號配對。若有多個未提供 identity 的 xAI 登入，共用模式只顯示 GrokBot 訂閱，不能據此追蹤其他帳號。非共用方案保留來源帳號列；主要來源失敗時使用可取得的既有 xAI 資料，全部失敗則保留錯誤／過期標記。未新增 Grok CLI auth.json 的刷新或寫入。

## 驗證與來源

隔離測試涵蓋原生 MCP、停用與相容回退遮蔽、註解保留、預覽衝突、hooks 安裝／移除、合併交易失敗復原、事件解析與額度來源選擇。未套用真實 Grok CLI 設定，CLI 載入與事件送達仍需設定套用後實機驗收。

- [Grok MCP 文件](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/07-mcp-servers.md)
- [Grok hooks 文件](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/10-hooks.md)
- [Grok 設定文件](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/05-configuration.md)
