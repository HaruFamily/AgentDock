---
name: agentdock-ui
description: 統一 AgentDock 的 Qt 介面語意、控制項外觀、版面與鍵盤互動；修改專案頁、聊天、設定、Git 或共用主題時使用。僅適用 AgentDock。
---

# AgentDock 介面規範

先讀專案 AGENTS.md、docs/STATUS.md。此技能以倉庫根目錄為相對路徑基準；不要載入 data/private_theme.py 或真實個人資料來測試。

## 語意與元件

| 角色 | 選用 | 外觀與行為 |
| --- | --- | --- |
| 頁標題 | QLabel / PageTitle | 22px、粗體、純文字；不得用輸入框假扮 |
| 區塊／欄位名稱 | QLabel / SectionLabel、FieldLabel | 12px 次要色、600；不要當操作 |
| 說明／狀態 | QLabel / muted、ErrorText | 只寫辨識用途、實際錯誤或決策必要資訊 |
| 唯讀 | QLabel；需複製的路徑／ID 用 readOnly QLineEdit | 唯讀框用 CANVAS／MUTED，可選取複製，不接操作 click |
| 輸入 | QLineEdit、QPlainTextEdit | CARD 底色、LINE 邊框，焦點 ACCENT；有欄位名稱 |
| 一般操作 | qa_view.button | 有可見外框；flat 僅為緊湊次要按鈕，不能消失成標題 |
| 主要操作 | primary=True | 同一操作區只強調當下主要動作，不把導覽畫成提交 |
| 刪除／捨棄 | danger=True | 保留既有確認／復原語意，不因換樣式改成立即執行 |
| 圖示操作 | widgets.icon_button 或既有 IconButton／RowMore | 有 accessibleName、hover、focus；tooltip 只辨識用途 |
| 選項 | checkable 按鈕／原生選擇元件 | checked 底色及外框，必須與 hover 不同 |
| 導覽 | 側欄列／SettingsNavItem | 可保留無外框列樣式；選取與鍵盤焦點可辨識 |

共用規則在 agentdock/ui/control_style.py；theme._build 與 chat_common._style 都使用它，Git 獨立視窗也經 chat_common._style。局部 objectName selector 不得抵消角色。主題用既有色票，不把私人主題的字型／裝飾硬編進共用程式。自畫控制項（SettingsNavItem、Switch、RoundButton）要另檢查焦點、disabled、checked，不能假設 QSS 會畫。

## 已確立的工作流程

- 新對話置中，既有對話輸入框在底部；不在主區重複左側歷史清單。
- 專案名稱列只收合／展開，不畫箭頭；空項目顯示「沒有對話」。搜尋無命中不可假裝專案真的空白。
- 專案齒輪直接開專案頁；對話的「⋯」才是動作選單。＋在指定專案開新對話。
- 專案設定直接列出控制，圖示預覽是 QLabel，選形狀及選色另有操作。路徑列為唯讀框、瀏覽…、開啟資料夾。
- 不加入「可換行」「在下方輸入」等教學，也不移到 tooltip／placeholder 變相補回。
- 切換、背景刷新、問答到達不得覆蓋草稿／附件、搶焦點、重設游標或閱讀位置。語意變更與純樣式變更分開驗證。

## 驗收

用暫存資料預覽最窄支援視窗、一般寬度、長中文／路徑、兩種主題與較大字級。檢查預設／hover／focus／checked／disabled／error，以及 Tab、Enter、Space、複製唯讀文字。視覺變更需看渲染；若 offscreen 字型不可用，明確揭露，不把方框字截圖當排版通過。位置要求以實際 widget 座標比較，避免只測寬鬆容許範圍。復用現有測試，勿只測 QSS 字串。

## 專案變更

專案頁不提供「獨立視窗」按鈕；Git 操作集中在卡片頂列，空狀態不預留大片差異區。

Git 僅作唯讀檢視：分支、變更檔案、已暫存／工作區差異與重新整理。不要恢復列上暫存、雙擊修改、右鍵還原、提交／推拉／切分支等入口。外部 Git GUI 只在使用者點擊後開啟；它是獨立工具，不是 AgentDock 自動執行 Git 寫入。測試瀏覽前後 index、HEAD 與工作區不變。

專案變更預設嵌入專案頁，獨立視窗共用 GitView。換專案／資料夾時使舊結果失效，不把背景結果送到新選擇。外部程式設定在獨立的外部程式頁，聊天圖片與 HTTP(S) 網址使用統一 opener；只影響 App，不更改作業系統關聯。

## 設定導覽

設定依用途分為一般、外觀、Agent 與工具、外部程式、資料與備份。Agent／MCP／擴充使用單層 Tab；不為空分類新增偏好。保留各頁捲動、最後工具 Tab 與待套用操作，切頁不寫入外部設定；預覽／套用列只在工具分類出現，其他分類仍由側欄數量提示待處理。頁標題與工具 Tab 固定在內容上方，偏好沿用左名稱右控制項。
