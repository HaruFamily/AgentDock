# 現況：0.7.1（Python 版）

更新日期：2026-10-03。

## 已實作

- 啟動時只顯示圓形小圖示（可見直徑約 38 px），每次啟動都在主螢幕右側置中；展開時保留圖示，點圖示切換顯示／隱藏視窗。
- 預設黑綠極簡外觀；可從本機 data/private_theme.py 載入額外外觀，缺少或載入失敗時只提供預設外觀。本機檔案不提交 Git，也不納入設定匯出。Ctrl+Alt+H 快速切回極簡，同步更新視窗與圖示。
- 額度（TokenGauge 整合）：Claude Code、Codex 多 workspace、Grok；每個視窗（5H／W／M）都以進度條列出。
- QAI 問答：文字／附圖提問、單選／多選（點選項整列即可選取）／自由文字、選項備註、回答附件（選檔、拖放、貼上圖片）、草稿自動保存、歷史。
- 多個 MCP caller 獨立；取消及逾時不產生預設答案，可用 get_user_answer 恢復等待。
- MCP 庫：遠端網址、uvx/npx、GitHub Release、下載網址、自訂指令；祕密與本機路徑只存本機（可加密匯出／匯入到另一台電腦）；可改用本機已安裝的執行檔。
- 新問題通知（小圖示時的 Windows 通知）；Agent 設定檔裡的 MCP 被外部移除時標示並可補回。
- GitHub 更新：每天背景檢查一次；新版下載到 data/mcp/<名稱>/<版本>/，所有用到的代理一起排入更新，舊版在不再使用後清除。
- 「設定」頁：MCP 庫與各代理皆可收合並記住狀態；「＋ MCP」加入、「−」移除（不在庫中的會先自動收進庫）、「編輯」代理；「需更新」一鍵同步；預覽／備份／批次寫入／失敗復原；Codex TOML 保留註解。
- 敏感設定的加密匯入／匯出放在設定頁最底部共用的「設定選單 → 進階／備份與還原」，固定顯示，不隨內容捲動。
- 擴充庫（hook／plugin）：rtk 範本；Claude Code、Codex 寫入 PreToolUse hook，OpenCode 放 plugin；「加入 PATH」把工具放進 data\bin 並加到使用者 PATH。
- 自動偵測 Microsoft Store 版 Claude 的設定檔路徑。
- 外部 Python 工具分頁的自動載入機制（`agentdock/tools/`）。
- 開發歷史集中在 `docs/history/`，本機 Git 備份與舊操作腳本放在不提交 Git 的 `local-backups/`；已移除停用的舊小球／面板模組。

## 限制

- 只支援 Windows 個人使用；沒有打包、簽章或自動更新。
- OpenCode 的 JSONC 若有註解，寫回後會變成標準 JSON（原檔有備份，預覽會提示）。
- 等待仍受各客戶端工具逾時影響（Codex 設 1860 秒、Claude Code 設 1860000 毫秒）。
- 附件不自動解析 PDF／Office。
- 擴充只寫 hook（等同 `rtk init --hook-only`），不產生 RTK.md／AGENTS.md 說明；修改擴充定義後，已加入的 Agent 需先移除再加入才會套用新 hook。
- rtk 擴充尚待 Windows 實機確認（hook 實際觸發、PATH 生效）。
- 0.7.x 介面尚待 Windows 實機確認，逐項清單見 [VERIFICATION.md](VERIFICATION.md)。
