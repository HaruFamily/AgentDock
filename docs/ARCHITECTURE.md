# 架構

單一 Python 程式，兩種執行方式：

1. **浮動 App**（`python -m agentdock`）：PySide6 小球、面板、系統匣；持有 QuestionStore 並啟動 broker。
2. **MCP stdio 服務**（`python -m agentdock.mcp_server`）：由 Codex／Claude 等客戶端啟動，每個客戶端一個行程。

```
客戶端 ──stdio──> mcp_server ──HTTP 127.0.0.1 + token──> broker ──> QuestionStore <── UI
```

- `broker.py`：隨機埠、隨機 token，寫入 `data/endpoint.json`；拒絕帶 Origin 的請求。HTTP 只能建立、查詢、取附件，不能提交答案；答案只能從 UI 提交。
- `client.py`：mcp_server 用來找 broker；App 沒開時以 pythonw 啟動 `--background`。
- `qa/store.py`：`questions.json` 與 `attachments/`，格式與 0.5 相同。執行緒安全；事件經 Qt 信號轉到 UI 執行緒。
- 呼叫者身分：`AGENTDOCK_CLIENT_ID`（設定檔中為 `agentdock-<agent id>`）雜湊成 owner；只能讀自己的問題。
- `library.py`：MCP 庫。定義在 `mcp-library.json`（佔位符 `{AGENTDOCK}` `{DATA}` `{HOME}` `{BIN}`），祕密在 `data/secrets.json`，
  下載物在 `data/mcp/<key>/`。`render()` 依客戶端類型產生設定項目；`in_sync()` 判斷是否「需更新」；`from_config()` 把既有設定收進庫。
- `agents.py`：以操作清單（enable/disable/remove/add/update）規劃變更。Codex／OpenCode 用 `enabled` 停用；Claude 系列沒有此旗標，停用時移出設定存到 `data/disabled-<id>.json`，啟用時還原。
  寫入前確認原檔未被改動，先備份成 `*.agentdock-<時間>-<id>.bak`，批次失敗會嘗試復原。
- QAI 是 MCP 庫的內建項目，指向 venv 的 `python.exe -m agentdock.mcp_server`，並設 `PYTHONPATH` 與 `AGENTDOCK_DATA_DIR`。
  設定檔中的名稱是 `agentdock-qa`；舊名稱 agentdock_qa（含 Electron 版）會在「全部更新」時自動換新。
- `ui/`：`ball.py` 小球、`panel.py` 面板外框、`qa_view.py` 問答、`agents_view.py` Agent 與 MCP 分頁（MCP 庫＋Agent 卡片＋待套用列）、`app.py` 組合與單一實例鎖。
- `tools/`：外部工具分頁的註冊點。
