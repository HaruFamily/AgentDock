# AgentDock

Windows 桌面 Agent 工作台。**平台獨立使用，擴充按需下載。**

[下載發行版](https://github.com/HaruFamily/AgentDock/releases) · [QAI 安裝說明](extensions/QAInteract/README.md) · [專案現況](docs/STATUS.md)

## 使用 AgentDock

1. 在 Releases 下載 `AgentDock-0.5.0-win-x64.zip`，解壓到可寫入的位置。
2. 開啟 `Start.cmd` 或 `AgentDock.exe`。不需要另外安裝 Node.js。
3. 「Agent 清單」登錄客戶端、名稱與設定檔位置。
4. 「擴充」匯入另行下載的 `QAInteract-0.5.0.admod`，或貼上它的直接下載網址。
5. 勾選 QAI 要提供給哪些 Agent → 預覽 → 確認套用。
6. 重新載入客戶端，請 Agent 呼叫 `ask_user` 驗證。

程式關閉視窗時會收至系統匣；右鍵圖示可結束。資料保存在 exe 旁的 `data`，已安裝擴充在 `modules`。搬移程式時一起保留，並重新套用 Agent 連接路徑。

## 只需要 QAI

把 [QAI 的說明頁](https://github.com/HaruFamily/AgentDock/tree/main/extensions/QAInteract) 交給 Agent，請它依說明安裝。
QAI 發行包獨立於平台；已有相容平台便沿用，沒有則可下載同版平台。沒有自訂 `.git?path=` 協定。

## 資料夾

| 位置 | 內容 |
| --- | --- |
| `src/platform` | 平台原始碼 |
| `src/shared` | 共用協定與工具 |
| `extensions/QAInteract` | QAI 原始碼、說明、獨立安裝入口 |
| `tests`、`scripts` | 測試與開發工具 |
| `docs` | 現況、架構、開發、發行與歷史文件 |
| `docs/skills` | 可供 Agent 使用的開發 Skill |
| `AGENTS.md` | Agent 進入本專案時的開發指引 |
| `output` | 本機產出的可使用程式與 ZIP，不提交 Git |
| `.local/agentdock` | 開發時的個人執行資料，不提交 Git |

一個 GitHub repository 管理原始碼，同一個 Releases 提供多個獨立下載檔。
**GitHub 的 Source code ZIP 是原始碼；一般使用者請下載 Release 附件。**

開發入口：[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。歷史：[docs/history/CHANGELOG.md](docs/history/CHANGELOG.md)。

本版管理 Codex、OpenCode、Claude Code、Claude Desktop 的指定設定檔；已寫入設定不等於客戶端已連線。
第三方 MCP 可啟停或新增 HTTPS 遠端端點，尚無任意 GitHub 專案自動安裝器。
UsageMonitor、AgentConnector 尚未實作。程式尚未簽章；只執行信任來源。
