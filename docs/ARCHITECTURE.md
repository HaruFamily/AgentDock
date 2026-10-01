# 架構

AgentDock 是平台產品，不另安裝一個叫 Core 的程式。

- src/platform：Electron 主程序、UI、Agent 設定、外部模組管理。
- src/shared：平台 broker、模組介面、MCP 到桌面的 client。
- extensions/QAInteract：問答服務、UI、儲存與 MCP adapter。
- tests/helpers：測試用整合介面，平台本身不依賴 QAI。

平台的 asar 不包含 QAI 問答實作或 MCP SDK；QAI 的 .admod 以 ZIP 包含已打包 JS、UI、manifest 及授權說明。
載入前檢查路徑白名單、版本、解壓大小和 SHA-256。雜湊可發現損壞，不代表發行者身分簽章。
模組在同一個使用者信任邊界內執行，並非沙箱化第三方程式。

MCP stdio 使用 Node 或平台自帶的 Electron Node 模式，透過本機 127.0.0.1 broker 找到共用桌面。
broker 使用隨機 token 並拒絕瀏覽器 Origin；答案只能由桌面 IPC 提交。
待答問題及草稿持久化；外部 Agent 等待逾時不刪除問題。

正式版資料：exe/data；擴充：exe/modules。開發版資料：.local/agentdock。
docs 只放維護文件，與上述執行資料完全分開。AIL_* 環境變數名稱暫留相容。

設定管理只處理登錄檔案。Codex/OpenCode 使用 enabled；Claude 系列停用時移出設定並存入 data，重新啟用還原。
TOML 寫回會重排並移除註解；JSONC 保留 MCP 區塊外內容。確認時檢查原檔未變，先備份，批次失敗嘗試復原。
