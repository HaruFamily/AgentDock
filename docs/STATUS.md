# 現況：0.7.1（Python 版）

更新日期：2026-10-07。分開程序驗證：Agent 分類與 Foldout 5 項、其餘 104 項皆通過；合併單程序執行仍有 Qt 原生存取違例，待追查跨測試生命週期。

Inbox 依 Agent 分類，可用箭頭展開／收合，各分類內顯示任務列表，每個任務只顯示進行中／已完成／待回答三種狀態，不顯示結果內文；待答問題點擊後原地展開 Foldout，送出後收合。所有未回答問題保留，新問題不搶焦點、不重建既有輸入框；收合保留草稿。沒有第二層對話頁或歷史瀏覽，既有資料仍保存在本機。

已回答／取消的舊問答不再當作進行中證據：無 pending、無最新結束回報、也非本次啟動後回答中的任務退出列表；不刪儲存紀錄。進行中表示本次已交回答、尚未收到結束回報，不代表已驗證 Agent 執行狀態。

Inbox 已收斂為問答與任務結果收件匣。ask_user／get_user_answer／附件回答維持原協定；report_to_user 只接受 completed、failed、cancelled，回傳摘要即可，不要求複製原聊天全文。

- 移除原生聊天掃描、Desktop IndexedDB／HTTP 快取及 vendored 解析器。不讀聊天歷史、不補抓原生附件、不增加登入流程。
- 舊鏡像事件與附件仍保留在 data，不在 Inbox 顯示；舊 native alias 不再影響問答歸屬。問答與主動回報的結果、未讀狀態跨重啟保留。
- 介面及 MCP 服務顯示名稱為 Inbox；設定識別統一為 inbox；舊 agentchat 透過設定預覽、備份後遷移，工具名稱及 broker 路由維持。四種本機客戶端已經使用者確認、備份並套用 agentchat → inbox；檢查均只保留一個啟用的 inbox 連線，客戶端需重新連線載入。
- 未讀／待答以小圖示紅點提示，不自動彈窗、不搶走輸入焦點。保留先前縮放修正，實機效果仍待驗收。
- 收斂前版本已提交為 4d97351，可由 Git 找回；歷史研究與驗證紀錄保留在 VERIFICATION.md、history/CHANGELOG.md。

## 已實作

- 啟動時只顯示圓形小圖示（可見直徑約 38 px），每次啟動都在主螢幕右側置中；展開時保留圖示，點圖示切換顯示／隱藏視窗。
- 預設黑綠極簡外觀；可從本機 data/private_theme.py 載入額外外觀，缺少或載入失敗時只提供預設外觀。本機檔案不提交 Git，也不納入設定匯出。Ctrl+Alt+H 快速切回極簡，同步更新視窗與圖示。
- 額度（TokenGauge 整合）：Claude Code、Codex 多 workspace、Grok；每個視窗（5H／W／M）都以進度條列出。
- Inbox 問答：文字／附圖提問、單選／多選（點選項整列即可選取）／自由文字、選項備註、回答附件（選檔、拖放、貼上圖片）、草稿自動保存、原地展開作答與最新任務結果。
- 多個 MCP caller 獨立；取消及逾時不產生預設答案，可用 get_user_answer 恢復等待。
- MCP 庫：遠端網址、uvx/npx、GitHub Release、下載網址、自訂指令；祕密與本機路徑只存本機（可加密匯出／匯入到另一台電腦）；可改用本機已安裝的執行檔。
- GitHub 更新：每天背景檢查一次；新版下載到 data/mcp/<名稱>/<版本>/，所有用到的代理一起排入更新，舊版在不再使用後清除。
- 「設定」頁：MCP 庫與各代理皆可收合並記住狀態；「＋ MCP」加入、「−」移除（不在庫中的會先自動收進庫）、「編輯」代理；「需更新」一鍵同步；預覽／備份／批次寫入／失敗復原；Codex TOML 保留註解。
- 敏感設定的加密匯入／匯出放在設定頁最底部共用的「設定選單 → 進階／備份與還原」，固定顯示，不隨內容捲動。
- 擴充庫（hook／plugin）：rtk 範本；Claude Code、Codex 寫入 PreToolUse hook，OpenCode 放 plugin；「加入 PATH」把工具放進 data\bin 並加到使用者 PATH。
- 自動偵測 Microsoft Store 版 Claude 的設定檔路徑。
- 外部 Python 工具分頁的自動載入機制（`agentdock/tools/`）。
- 開發歷史集中在 `docs/history/`，本機 Git 備份與舊操作腳本放在不提交 Git 的 `local-backups/`；已移除停用的舊小球／面板模組。


## 限制

- 更新後重啟 AgentDock 並重新連接 MCP，以載入新版工具指引；不必重新安裝或更換設定名稱。
- 必須由 Agent 呼叫 report_to_user 才有結果通知；不自動推定完成，也未新增自動通知 hook。
- pending、cancelled、timeout 都不是同意；問答 caller 隔離、草稿及附件限制維持。
- 只支援 Windows 個人自用；不做打包、發行或自動更新。
- 客戶端工具逾時及 OpenCode JSONC 寫回限制仍在；設定變更維持預覽、確認、備份及復原。
