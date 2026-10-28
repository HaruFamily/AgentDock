---
name: agentdock-maintainer
description: 維護 AgentDock（Python 系統匣 Agent 工作台）的問答、設定管理、浮動介面與外部工具分頁；僅用於此專案的開發工作。
---

# AgentDock 維護

先讀倉庫根目錄 AGENTS.md 及 docs/STATUS.md，不把本 Skill 當作永遠最新的完成清單。
需要結構時讀 docs/ARCHITECTURE.md；執行、測試與加入工具讀 docs/DEVELOPMENT.md。

程式在 agentdock/；個人資料在 data/，不要讀寫它來做測試。
mcp_server.py 及其匯入鏈不可匯入 PySide6，也不可寫 stdout。

修改設定管理時驗證預覽、備份、外部修改衝突、失敗復原；不要測試真實客戶端設定。
修改問答時保留 pending／cancelled 語意、caller 隔離、草稿與不搶焦點。
修改後執行 uv run pytest -q，並更新 docs/STATUS.md 與 CHANGELOG。
使用原任務的推送授權，Skill 本身不新增遠端修改授權。


## 專用規範

以下路徑以 AgentDock 倉庫根目錄為準，只讀本次任務相關者：

- 介面與文案：docs/skills/agentdock-ui/SKILL.md
- 收發、問答、錯誤恢復：docs/skills/agentdock-dispatch/SKILL.md
- MCP／擴充／Agent 設定套用：docs/skills/agentdock-config-transactions/SKILL.md
- 免安裝、更新、外部工具：docs/skills/agentdock-runtime-tools/SKILL.md
- 隔離測試與交付：docs/skills/agentdock-validation/SKILL.md

docs/skills 是版本控制內的規範來源；更新技能後同步到目前 Codex skills 目錄內同名 AgentDock 技能。不要修改其他專案的技能。
