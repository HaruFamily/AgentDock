# Google Sheets MCP 回應大小防護

## 原因（2026-10-13 診斷）

OpenCode 2.0.27 使用本機 `mcp-google-sheets 0.6.3` 與 `mcp 1.30.0`。
2026-10-12 16:58、18:38（台灣時間）兩次中斷前的呼叫皆包含多張工作表的
`get_sheet_data(include_grid_data=true)`，大多未限制 range；同段純數值讀取成功。
傳輸先報 `ReadBuffer exceeded maximum size of 10485760 bytes`，再出現子程序退出／BrokenPipe。

以唯讀 API 對同一試算表目前資料重新量測：兩張相關工作表的 API 精簡 JSON 分別
為 3,895,539 與 3,023,341 bytes；依實際安裝 FastMCP 的工具輸出轉換和 JSON-RPC 編碼後，
單次回應分別為 15,799,712 與 12,609,477 bytes，均超過 10 MiB。
這是現在資料的重現值，不是當時被截斷封包的擷取。

格式資料包含空白但有格式的儲存格。FastMCP 同時輸出 `content` 的縮排 JSON 文字
和 `structuredContent`，再加上 JSON 字串跳脫，使訊息明顯膨脹。
OpenCode 接收端／MCP SDK 的緩衝限制觸發中斷；不是 Google API 配額錯誤，AgentDock 也未代理資料流。
SDK 的 ReadBuffer 會移除已解析訊息，10 MiB 不是整段工作累計流量配額。
參考：[SDK stdio 原始碼](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/packages/core-internal/src/shared/stdio.ts)。

## 修補行為

`scripts/google_sheets_mcp.py` 啟動原套件，加入 `agentdock/sheets_mcp_guard.py`：

- 格式讀取需明確矩形，例如 `A1:K50`；整欄或省略範圍在送出 API 前回傳工具錯誤。
- 僅在文字 JSON 與 structuredContent 完全相等時壓縮文字版；保留所有欄位與兩種表示。
- 正規化結果 UTF-8 大於 8 MiB 時回傳小型工具錯誤，為 JSON-RPC 外層留出空間。
- 大量純數值讀取仍可使用，超限則提示分批。公式可用 `get_sheet_formulas`。
- 不截斷成假成功、不自動重試；寫入若已執行而結果超限，明確提示先查核結果，不能直接重送。
- 僅防護工具結果；未改 OpenCode 的通用傳輸層或啟動逾時。未指定範圍的格式讀取會多一次可恢復錯誤。

## 設定預覽（尚未套用）

OpenCode 的 `google-sheets.command` 改成以下陣列；保留原本 `type`、`enabled` 與憑證環境設定。
路徑依實際 AgentDock 所在位置調整。

```json
[
  "D:\\PinsData\\Private\\AgentDock\\runtime\\uv\\uvx.exe",
  "--with", "mcp==1.30.0",
  "--from", "mcp-google-sheets==0.6.3",
  "python",
  "D:\\PinsData\\Private\\AgentDock\\scripts\\google_sheets_mcp.py"
]
```

固定版本是因為防護使用 SDK 內部工具處理器介面。這些版本與目前環境一致；本次不是上游升級。
套用前確認預覽、備份客戶端設定，再更新 command。還原備份即可回到原始啟動方式。
AgentDock MCP 庫若仍保存原指令，之後同步可能覆寫客戶端修補；需另行預覽庫與其他客戶端的影響。
此文件及程式不會自行修改 MCP 庫或客戶端設定。

## 驗證

隔離測試涵蓋範圍前置檢查、資料完整性、中文 UTF-8、冪等安裝及真實 stdio 工作階段。
合成超大回應被轉成工具錯誤後，同一條連線仍可成功讀取；寫入只執行一次，不自動重送。
測試不使用真實憑證、試算表或 data 目錄。

2026-10-13 完整回歸：138 項通過（44.17 秒）。固定版本的 uvx 指令已在離線快取驗證載入 20 個工具；真實 OpenCode 套用後的驗收待設定確認。
