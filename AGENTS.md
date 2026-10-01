# AgentDock 開發指引

先讀 [docs/STATUS.md](docs/STATUS.md) 了解現況，再依任務讀 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 或 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
重複的開發／驗收工作可使用 [agentdock-maintainer Skill](docs/skills/agentdock-maintainer/SKILL.md)。

- AgentDock 是獨立桌面平台；QAI 是選配模組。平台發行包不得包含 QAI 實作或 MCP SDK。
- 源碼位於 src/platform、src/shared、extensions/QAInteract/src。勿修改 dist 或 output 作為源碼修正。
- 問答的 pending、cancelled、timeout 都不代表使用者同意。新問題不能搶走正在輸入的焦點。
- 設定管理先預覽，确认後備份及套用。測試只能使用隔離資料，不寫真實客戶端設定。
- 文件與运行資料分開。docs 保存現況、決策和歷史；.local/agentdock 保存開發執行資料且不提交 Git。
- 修改完執行相關測試；發行依 docs/RELEASING.md。只有使用者授權時才推送或發布。
- 不因缺少圖譜工具阻止工作，也不得聲稱使用了不可用工具。圖譜證據不完整時直接讀來源。

## Codebase Memory

有 codebase-memory-mcp 時，session 開始先 list_projects 或 index_status 確認目前目錄與 generation。
結構探索依序偏好 search_graph、trace_path、get_code_snippet，預設 Tier 2。
找到候選路徑後，以 check_index_coverage 檢查所有依賴檔案；否定或全面結論還要檢查範圍。
stale、not_tracked、skipped、excluded、partial、unknown 的範圍必須直接讀來源補足，不把沒有結果當不存在。
文字常量、設定、非程式檔或圖譜不足時可用 rg。不要沿用 AgentInteractionLayer 舊目錄的索引當作新倉庫證據。
