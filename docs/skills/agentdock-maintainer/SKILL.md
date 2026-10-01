---
name: agentdock-maintainer
description: 維護 AgentDock 平台與 QAInteract 的原始碼、設定管理、分包和驗證流程；僅用於此專案的開發或發行工作。
---

# AgentDock 維護

先讀倉庫根目錄 AGENTS.md 及 docs/STATUS.md，不把本 Skill 當作永遠最新的完成清單。
需要結構時讀 docs/ARCHITECTURE.md；建置讀 docs/DEVELOPMENT.md；發行讀 docs/RELEASING.md。

平台源碼在 src/platform，共用工具在 src/shared，QAI 在 extensions/QAInteract/src。
維持平台不包含 QAI／MCP SDK 的分包邊界；不要修改 dist 或 output 代替改源碼。
現況與歷史維持在 docs，執行資料留在 .local 或指定的測試目錄。

修改設定管理時驗證預覽、備份、外部修改衝突、失敗復原；不要測試真實客戶端設定。
修改問答時保留 pending／cancelled 語意、caller 隔離、草稿與不搶焦點。
版本變更要同步平台、模組、安裝入口及文件，再驗證實際封裝。
使用原任務的推送／發布授權，Skill 本身不新增遠端修改授權。
