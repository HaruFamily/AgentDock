# AgentDock 開發指引

先讀 [docs/STATUS.md](docs/STATUS.md)，依任務再讀 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 或 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
重複的開發／驗收工作可使用 [agentdock-maintainer Skill](docs/skills/agentdock-maintainer/SKILL.md)。

- 這是使用者個人自用的 Python 程式，直接從原始碼執行，沒有打包或發行流程。
- 程式在 `agentdock/`；個人資料在 `data/`，不得提交 Git，測試也不得讀寫它。
- 問答的 pending、cancelled、timeout 都不代表使用者同意。新問題不能搶走正在輸入的焦點。
- `mcp_server.py` 與它匯入的模組不可匯入 PySide6，也不可寫入 stdout（那是 MCP 通道）。
- 設定管理先預覽，確認後備份及套用。測試只用暫存目錄，不寫真實客戶端設定。
- 外部工具放在 `agentdock/tools/<名稱>.py` 並定義 `TOOL = ToolSpec(...)`；載入失敗不得影響主程式。
- 修改後執行 `uv run pytest -q`。只有使用者授權時才推送。
- 不因缺少圖譜工具阻止工作，也不得聲稱使用了不可用工具。圖譜證據不完整時直接讀來源。

## Codebase Memory

有 codebase-memory-mcp 時，session 開始先 list_projects 或 index_status 確認目前目錄與 generation。
結構探索依序偏好 search_graph、trace_path、get_code_snippet，預設 Tier 2。
找到候選路徑後，以 check_index_coverage 檢查所有依賴檔案；否定或全面結論還要檢查範圍。
stale、not_tracked、skipped、excluded、partial、unknown 的範圍必須直接讀來源補足，不把沒有結果當不存在。
文字常量、設定、非程式檔或圖譜不足時可用 rg。0.5 以前的 TypeScript 索引已不適用於現在的 Python 程式。
