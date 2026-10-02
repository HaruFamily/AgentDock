# 版本歷史

## 0.6.0 — 2026-10-02

- 改寫為 Python（PySide6）個人版，直接從原始碼執行，移除 Electron、TypeScript、打包與模組包（.admod）流程。
- 介面改為永遠置頂的浮動小球＋展開面板。
- QAI 內建，不再是另行安裝的模組；資料格式沿用 0.5。
- Codex TOML 寫回保留註解；自動偵測 Microsoft Store 版 Claude 設定檔路徑。
- 新增 `agentdock/tools/` 外部 Python 工具分頁機制。
- 移除 UsageMonitor、AgentConnector 預留項目。
- 新增 MCP 庫（mcp-library.json），Agent 與 MCP 合併為「Agent」分頁；預設收錄 codebase-memory-mcp。
- MCP 庫支援 GitHub 自動檢查更新與版本化安裝；區塊可收合；代理可編輯；面板邊緣可縮放。
- 「Agent」分頁改為清單式介面：圖示按鈕、橘點提示、底部「全部更新」；客戶端自管的 MCP（如 Codex 的 node_repl）標示為系統且不可移除；代理既有 MCP 自動收進 MCP 庫。
- Agent 可拖曳排序，Agent／MCP 可依名稱或類型排序（類型分組）；QAI 在設定檔中的名稱改為 `agentdock-qa`，舊名稱自動遷移。

## 0.5.0 — 2026-10-01

- 搬移至 AgentDock 正式 Git 倉庫。
- 平台源碼集中 src；QAI 集中 extensions/QAInteract，提供獨立安裝入口。
- 文件整理為 docs、根目錄 AGENTS.md 與維護 Skill。
- 清除舊 .ail、archive 和舊輸出，執行資料改用 .local/agentdock。
- 發行仍保持平台／QAI 分包。

## 0.4.0 — 2026-10-01

免安裝平台、Agent 設定檔登錄、批次 MCP 管理、QAI 自動設定與平台補齊。

## 0.3.0 及以前

建立 QAI 問答、選項與附件、共享桌面平台，以及平台／模組分包。
早期名稱 AgentInteractionLayer；AIL_* 協定／環境變數為相容性仍保留。
舊二進位、測試個人資料不作為版本歷史保存。
