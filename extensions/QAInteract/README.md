# QAInteract（QAI）

AgentDock 的選配回答中心，版本 **0.5.0**。包含 MCP 服務與回答介面；共用 AgentDock 的桌面視窗。
這個資料夾就是 QAI 的原始碼與安裝入口，無需第二個 repo。

## 已有 AgentDock

下載 [QAInteract-0.5.0.admod](https://github.com/HaruFamily/AgentDock/releases/download/v0.5.0/QAInteract-0.5.0.admod)，在平台的「擴充」匯入。
也可以把同一下載網址貼到平台。接著勾選 Agent → 預覽 → 確認套用 → 重新載入客戶端。

## 請 Agent 協助安裝

把本頁交給 Agent，例如：

> 請依這個專案的 extensions/QAInteract/README.md 幫我安裝 QAI，使用我的指定客戶端設定檔。已有 AgentDock 時請沿用。

安裝 Agent 應先確認使用者授權、客戶端種類及要修改的設定檔；不可猜測專案範圍。
下載並檢查同版 [Install.ps1](https://raw.githubusercontent.com/HaruFamily/AgentDock/v0.5.0/extensions/QAInteract/Install.ps1)，再執行，不直接將網頁內容管線交給 shell。

```powershell
# 範例：將 ConfigPath 換成實際要修改的設定檔
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 `
  -Kind codex -ConfigPath "$env:USERPROFILE\.codex\config.toml" `
  -AgentName "我的 Codex"
```

- 已開啟過平台時，使用本機記錄的位置；也可明確傳入 `-PlatformDirectory`。
- 無平台時自動下載同版免安裝平台，預設放在使用者的 LocalAppData/AgentDock/Portable。
- 下載 QAI ZIP，驗證 SHA-256，匯入模組，登錄指定 Agent 並备份、寫入 MCP 設定。
- 執行前從系統匣結束 AgentDock，避免與使用中的設定互相覆蓋。
- 完成後重新載入客戶端，允許工具並實際呼叫 `ask_user`。
- 不提供 Kind/ConfigPath 時只準備平台與模組，稍後到平台介面選擇 Agent。

Kind：`codex`、`opencode`、`claude-code`、`claude-desktop`。
設定檔：Codex config.toml；OpenCode opencode.json/jsonc；Claude Code 使用者 .claude.json 或專案 .mcp.json；Claude Desktop claude_desktop_config.json。

## 離線路線

另行下載 QAI ZIP 和同版平台 ZIP。解壓 QAI，在其中執行 Setup-QAI.ps1，傳入 `-PlatformArchive`、`-PlatformDirectory` 和客戶端參數。
已有相容平台就不必再下載平台 ZIP。它們是分開發行，QAI 不附帶 Electron。

## 限制

設定格式有測試，不等於每個真實 App／Provider 均已驗收。等待仍受客戶端逾時影響；未答問題保存，可恢復等待。
TOML 寫回會重新排版，完整原檔先備份。Claude 停用項目暫存在平台 data，重新啟用可還原。
模組尚未簽章；雜湊不是身分認證，只使用信任來源。
不使用自訂 .git?path= 安裝協定，也不宣稱此網址可直接貼進所有 MCP 客戶端。
