# 現況：0.7.1（Python 版）

2026-10-28：專案頁移除獨立視窗按鈕；Git 改為一個整合卡片，標題／分支與外部工具／重新整理集中於頂列。空儲存庫與非 Git 資料夾只顯示狀態，有變更才展開清單與差異；隱藏空分組、自動選取第一個變更。匯入對話與刪除專案移至頁尾，主題切換同步 Git 卡片。

2026-10-28：設定重整為一般、外觀、Agent 與工具、外部程式、資料與備份。Agent／MCP／擴充改為同頁 Tab，沿用舊導覽狀態並記住最後工具 Tab；切頁保留捲動位置與待套用變更。套用列僅在工具頁顯示，側欄保留待處理計數。外部程式由一般移出，一般的重新啟動合併到偏好區。以暫存資料驗證兩種主題與放大文字。

2026-10-28：唯讀專案變更以共用 GitView 預設嵌入專案頁，窄版改為上下清單／差異；獨立視窗共用元件。設定 → 一般加入外部工具：Git Extensions（預設）、Fork、Git GUI、自訂 exe；圖片與瀏覽器採系統預設或自訂 exe。偏好存 ui.json、不改 Windows 關聯；以參數陣列與 CREATE_NO_WINDOW 開啟，聊天圖片及 HTTP(S) 連結使用同一服務。背景讀取加 generation 隔離與元件銷毀保護。

2026-10-28：Git 視窗改為純唯讀「專案變更」：保留分支、追蹤資訊、已暫存／工作區差異、重新整理及開啟外部 Git GUI；移除所有暫存、提交、推拉、切分支、捨棄入口與事件。Git 讀取禁用 optional locks、外部 diff 及 textconv；選檔遲到結果不覆蓋新選擇。暫存 repo 驗證瀏覽與雙擊不改 index、HEAD、工作區。

2026-10-28：共用 control_style 統一一般／緊湊操作、唯讀欄位、選取與焦點外觀，由 App 與聊天／Git 樣式共同使用；專案頁分開標題、名稱輸入及圖示預覽，刪除採危險操作樣式，自畫設定導覽／開關／主題卡補鍵盤焦點。新增介面、收發、設定交易、runtime 與驗證五個專用 Skill，更新維護 Skill 與 AGENTS 索引，並安裝到本機 Codex skills。

2026-10-28：專案資料夾列改為唯讀完整路徑框、瀏覽選擇資料夾、開啟資料夾三個獨立控制；路徑可選取複製，瀏覽取消不變更。

2026-10-28：側欄專案入口由「⋯」改為齒輪「專案設定」。專案頁直接列出圖示選擇、顏色／預設顏色、資料夾開啟／更換、Git、匯入對話及刪除操作，不再藏於頁內選單；圖示形狀與顏色持久化並同步側欄。

2026-10-28：專案列「⋯」直接切換主區域專案頁，不再開選單或獨立設定視窗；新對話仍由「＋」開啟。移除展開箭頭，名稱列繼續展開／收合，空專案顯示「沒有對話」（搜尋無命中顯示「沒有符合的對話」）。新增可持久化的專案資料夾圖示顏色，點專案頁圖示選色，側欄同步更新。

2026-10-28：修正專案新對話預留 120px 頂部空間造成的置中偏移，專案／一般新對話輸入框位置一致。側欄專案名稱與箭頭只展開／收合，不切換頁面或草稿；專案細節改由「⋯ → 專案設定」獨立對話框操作。新增實際滑鼠名稱／箭頭點擊及輸入框座標一致回歸驗證。

2026-10-28：依使用回饋，新對話共用輸入框改為主區域垂直置中，專案上方保留名稱與根目錄操作；移除主區域最近／歷史對話清單，歷史統一從側欄開啟。已開始的對話仍用底部輸入框，專案草稿與附件保留。

2026-10-28：專案與新對話共用底部輸入框及草稿，切換專案保留文字、附件與已有草稿的 Agent／模型設定；最近對話依所選專案篩選。側欄新對話改為輕量圖示列，搜尋採低對比外觀，專案保留選取底色並增加滑鼠移入的新對話入口。完整測試 386 passed（暫存資料與假服務），新增共用輸入框位置及跨專案草稿回歸測試。

2026-10-28：使用者實測 AgentDock 的 OpenCode／Claude／Codex 均已可使用 Unity MCP。另依 Codex 紀錄確認重送回合先略過 pending optional unityMCP，隨後 MCP 才 initialized；CodexClient 啟動 app-server 加入行程限定 `-c mcp_optional_startup_grace_ms=0`，讓工具清單等各 MCP 的既有啟動逾時，不改個人設定或授權。透過實際 CodexClient 確認有效值為 0、Unity 工具 48 個。

2026-10-28：Unity MCP 的 Codex 啟動紀錄出現 stderr 非 UTF-8，隨後 initialize 連線關閉。MCP 庫補上 PYTHONIOENCODING=utf-8／PYTHONUTF8=1，並備份後同步本機 Codex 的 unityMCP 環境。以實際設定驗證握手、48 個工具及 Aetherwheel instance 成功，stdout／stderr 嚴格 UTF-8 解碼通過；不修改 Unity 專案。

2026-10-28：修正網頁聊天在編輯送出後停止更新、畫面空白的問題。內部動作連結被攔截時 QtWebEngine 仍發出 `loadFinished(False)`；WebTranscript 現在保留既有頁面的 ready 狀態，只在真正重建樣式頁面時清除。獨立 WebEngine 回歸測試涵蓋連續兩次原文編輯送出、清除舊輪後顯示新回覆與樣式重載。正常環境完整測試 385 passed（暫存資料與假服務）；受限環境首次執行因本機通訊限制中止。需重啟 AgentDock 載入。

2026-10-28：MCP 庫加入 CoplayDev `unityMCP`，使用內附 uvx 的 `--from mcpforunityserver mcp-for-unity --transport stdio`，快取／工具與 Python 路徑留在 runtime。已下載依賴並驗證 `--help` 成功；完整測試 384 passed（初次受限環境無法存取暫存目錄，改在正常環境重跑通過）。尚未套用至 Agent，也未安裝 Unity 專案端套件或驗證 Editor 連線。設定依據：https://coplaydev.github.io/unity-mcp/getting-started/install 。

2026-10-28：收發錯誤恢復修正。Codex 不再重試的 error 立即結束本機回合，不等 turn/completed；去重遲到的結束通知，避免蓋掉下一輪。重試中的錯誤顯示「連線重試中」。授權／回答傳送失敗保留請求，只有使用者再次點擊才重送。送出、編輯及壓縮的一般例外也會離開 running。聊天問答卡增減時保留未變動的 FormBox 與答案，切換對話清除舊卡；Codex 匯入讀完 nextCursor 分頁。空白的新對話不再儲存會覆蓋 Agent／權限的空草稿。新增 6 項回歸測試，完整測試 384 passed（專案外暫存目錄與本機假服務）；git diff --check 通過。修正兩個既有 UI 測試的前置目錄與舊元件引用／選取方式。

本次 Codex 診斷：AgentDock 選到 npm codex-cli 0.165.0；桌面版附帶 0.165.0-alpha.1.2。唯讀查詢原 Session 證實失敗回合的 codexErrorInfo 為 serverOverloaded，後續嘗試多為 interrupted；不能由此斷定是本機版本或帳號差異。兩個執行檔以相同 ChatGPT 帳號、gpt-6.1-sol、default 層級各執行一次 ephemeral 最小請求均 completed（停用 MCP／hooks、不執行工具、不修改真實設定）。當時桌面版可用而 AgentDock 失敗的服務端分流／容量原因仍無法由現有紀錄確定。已補上啟動執行檔、userAgent、Codex home、每輪模型／推理強度、錯誤代碼及 willRetry 日誌，不記錄提示詞或憑證。重啟 AgentDock 載入修正。

2026-10-28：聊天側欄整理為專案粗體／展開箭頭與縮排對話；移除專案對話總數及相對時間，保留未讀與執行／待處理／失敗狀態。狀態、更多選單分為固定寬度欄，hover 不改文字與標題寬度。專案也可由列上的更多選單操作，匯入收在專案選單。離線畫面確認與欄寬回歸測試通過；完整 pytest 為 376 passed、1 failed，未涉及側欄的 test_stop_running_task 等待 interrupted 逾時，單獨重跑亦失敗，尚未解決。

2026-10-28：恢復 OpenCode 完整支援，維持四個 CLI 的聊天、MCP 設定與擴充管理。補回 OpenCode V2 收發、模型／推理選項、歷史／匯入／分叉／編輯、rtk 與 codebase-memory 插件；載入時保留 OpenCode 登記、對話與專案預設。保留本日重啟、專案頁、Agent 更新及操作手感修改；刪除對話仍只移除 AgentDock 清單。使用資料夾內 runtime 執行完整 pytest：377 passed（假服務與暫存設定，未測真實模型）。

2026-10-28：設定 → Agent 改為垂直單列，Claude／Codex／Grok 加入「更新」。點擊後確認實際執行檔路徑，再於背景更新：npm 安裝依該執行檔所屬 prefix 更新，Claude／Grok 官方原生安裝使用 update；其他來源提示由原安裝來源更新，不另裝一份。更新完成驗證並顯示版本，提示重啟 AgentDock；不停止既有對話。測試使用假的安裝路徑與 subprocess，不執行真實更新。

本次驗證：更新及登入測試 18 項通過；完整測試 335 passed、9 failed、18 errors，失敗涉及既有 OpenCode 移除後的舊測試／資產引用（系統匣測試另有 cp950 輸出解碼問題，以 UTF-8 重跑後確認為 OpenCode 標題斷言）。

2026-10-28：操作手感。再開最近的對話立刻顯示記住的最新一頁並回到原本的捲動位置，背景比對 Agent 的紀錄有差才重畫；刪除不再確認，改為清單立刻移除＋約 6 秒「復原」；執行中狀態顯示經過時間；一輪結束或待授權／待回答時，視窗開著但不在前景就閃工作列；送出失敗把訊息與附件放回輸入框；額度浮動視窗右鍵可「立即更新」「關閉」。

2026-10-28：對話頁顯示該 Agent 帳號的全部額度（`ConversationView.set_quota`／`_place_quota`，重用精簡的 QuotaView 並以 `keys` 篩選）：寬時在右下角、窄時在輸入框下方靠右；OpenCode 不顯示。額度更新條件改為對話視窗開著或浮動視窗開著。

2026-10-28：設定「一般」與系統匣選單新增「重新啟動」：有對話進行中時先確認；正常結束後以 `launch.start('--restart')` 另開（對話視窗原本沒開就加 `--background`），新行程用 `--restart` 等舊行程放開 `agentdock.lock`（最多 30 秒）。經 AgentDock.exe 啟動，uv.lock 變了也會順便更新 runtime。

2026-10-28：專案頁改版：名稱平常是純文字、根目錄可點開資料夾，頁面直接放新對話輸入框（選的 Agent 就是專案預設），對話清單沿用「最近」的樣式；匯入、更換根目錄、刪除收進「⋯」。

2026-10-28：對話標題列靠視窗左邊，不再對齊中間的閱讀欄。輸入框下方的 Git 標籤（與每輪結束後查 Git 狀態）移除，Git 只從專案頁或專案右鍵「Git」開；GitDialog 改為無父視窗的獨立視窗（工作列、最小化、最大化、關閉），自己套用對話樣式，可愛外觀也不改成無框（`cute_plain`）。

2026-10-28：刪除對話只從 AgentDock 移除，拿掉「同時刪除 X 裡的紀錄」勾選項與各家的遠端刪除（`Adapter.delete`、`Dispatcher.delete`、OpenCode `delete_session`）；Agent 那邊的紀錄保留，可再匯入。

2026-10-28：編輯訊息改成在訊息泡泡上直接編輯（Qt：`MessageWidget.start_edit`／`edit_submitted`；網頁：頁面上的 `startEdit`，送出走 `edit` 動作帶 `[id, text]` JSON），移除輸入框的「編輯訊息 ✕」編輯模式；`ConversationView.submit_edit` 呼叫 `Dispatcher.edit`。

2026-10-28：可愛外觀（Qt 畫的對話）對齊網頁顯示：一輪結束後步驟收成「處理了 N 個步驟」（`ConversationView._fold_process`、`MessageWidget.set_process`／`set_rule`），執行中思考過程展開、結束後收起（`ReasoningRow.set_live`，使用者點過就不再自動）。網頁版執行中自動展開的思考標 `data-live`，結束後不再被當成使用者展開而保留。

2026-10-28：訊息的「編輯」改成真的編輯：送出時在原對話拿掉那則訊息與之後的內容再送新的（`Adapter.rewind`、`Dispatcher.edit`；Claude Code `--resume-session-at`、Codex `thread/revert`、Grok `_x.ai/rewind/execute`、OpenCode `revert/stage`＋`revert/commit`，都不還原檔案）。Claude Code 編輯第一則時改開新工作階段取代（Bridge.replaced）。輸入框編輯時上方有「編輯訊息 ✕」。

2026-10-27：Claude Code 子行程加上 `--allow-dangerously-skip-permissions`：沒有它時對話中途切成「全部允許」會被 Claude Code 拒絕（bypass_not_launched），而 AgentDock 不顯示錯誤，仍每次詢問。這個參數只讓模式可切換，不會自行開啟。

2026-10-27：MCP 庫中 Google OAuth 的 MCP（目前為 mcp-google-sheets，需本機祕密 CREDENTIALS_PATH、TOKEN_PATH）的「⋯」加入「登入 Google」（`agentdock/oauth_login.py`）。MCP 自己在 token 無法續期時會開登入，但客戶端約 30 秒就關掉它，token 永遠存不下來，每個 Agent 啟動都再跳一次。這裡以 `uvx --from google-auth-oauthlib` 在背景（CREATE_NO_WINDOW）跑同一流程，最多等 10 分鐘，成功才取代 token 檔。

2026-10-27：登入終端機移除 PowerShell `-NoExit`，CLI 登入成功、取消或失敗結束後即關閉，不停留在命令提示。只關閉瀏覽器而 CLI 仍等待時不會自動中止登入。

2026-10-27：設定 → Agent 的 Claude、Codex、Grok 卡片加入「登入」「切換帳號」。使用者點擊才開啟原生 CLI 登入終端機，切換先登出再登入；不保存憑證副本。登入完成後重啟 AgentDock，既有收發子行程才會讀取新登入。帳號操作使用可見主控台，與「在終端機接續」同為使用者主動操作的例外。

2026-10-27：修正自訂外觀的 Qt 聊天訊息只顯示附件檔名：沿用對話的附件路徑解析器顯示等比例圖片預覽；找不到檔案或非圖片時保留附件名稱。

2026-10-27：Git 視窗改成 VS Code／GitHub Desktop 的樣子（`ui/git_view.py`）：提交訊息與「提交 N 個檔案到 <分支>」在檔案清單上方；清單由 ChangeDelegate 自畫（彩色狀態字母、粗體檔名、淡灰資料夾、+/− 行數，滑鼠移上去出現 ＋／− 暫存或取消暫存，群組列的 ＋／− 作用於整組，取代「全部暫存」按鈕）；行數來自新的 gitops.numstat（`git diff --numstat`）。上方顯示「→ 上游 ↑N ↓N」，落後時「拉取 N 個提交」、領先時「推送 N 個提交」變成主按鈕，重新整理改成圖示。差異在網頁開啟時用 diff_page（GitHub 樣式：新舊行號、增刪底色、分段列、Pygments 依副檔名上色；超過 setHtml 上限就去掉顏色），否則仍是 QTextBrowser（測試讀它）。

2026-10-27：chat_window.py（3300 行）依面板拆成 chat_common（常數、樣式表、圖示、小按鈕）、chat_sidebar、chat_messages（Qt 畫的訊息）、chat_composer、chat_conversation、chat_new、chat_dialogs，chat_window 只剩 ChatWindow 並轉匯出舊名稱（測試與 git_view 的匯入不變）。改哪塊只讀哪塊。標題列只留對話名稱與「⋯」（專案、Agent 標籤隱藏但仍更新），Git 狀態與上下文移到輸入框底列；輸入框內距縮小。網頁對話：Agent 工作中最新一段是 turn live（步驟照常顯示、思考展開），完成後最後一個工具／思考以前的內容（含中間的說明文字）收成最上面「處理了 N 個步驟」＋細線，下面才是答案；內文字級只隨文字大小放大一半，標題縮小。圖片附件：輸入框顯示縮圖（ImageChip），送出的訊息顯示圖片（ChatWindow.sent_files 記本次送出的路徑，其他找 data/attachments/chat；轉成 data: URL，頁面仍不讀檔）。Claude 的歷史只記「圖片」，無法還原。tests/test_dispatch.py::test_stop_running_task 偶爾逾時失敗（與這次無關，重跑會過）。

2026-10-27：對話內容改用網頁顯示（`ui/web_transcript.py`，QtWebEngine＋markdown-it），排版接近 Codex／Claude 桌面版：真正的 CSS（標題、粗體、清單圓點、表格、引用、行內程式碼、Pygments 語法上色、程式碼區複製）。新依賴 `PySide6-Addons==6.11.2`（固定與 Essentials 同版本，否則 uv 會想升級 App 正在用的 Qt DLL）與 `markdown-it-py`。安全：markdown-it 關閉原始 HTML 與圖片；頁面不載入任何外部資源；按鈕是 `https://agentdock.invalid/<動作>/<參數>` 連結，由 acceptNavigationRequest 攔下交給 ConversationView._web_action（copy／edit／fork／retry／older）；使用者點答案裡的 http(s) 連結才用系統瀏覽器開。Python 每次把 Transcript 轉成 units（user／turn／end，turn＝相鄰的助理步驟），只送簽章變了的部分給頁面的 apply()，頁面保留 details 展開狀態與閱讀位置。只有 App 啟動時 `web_transcript.prepare()`（須在 QApplication 之前）且外觀沒有自己畫對話（canvas／bubble／code／reply）才用網頁；測試與本機可愛外觀沿用 Qt 元件。網頁模式下授權與問答卡片放在輸入框上方（request_area，最多半個頁面高）；Ctrl+F 用 Chromium 的 findText。

2026-10-27：回答的 Markdown 加上樣式（question_text.dressed，改寫 Qt 自己產生的 HTML）：h1／h2 用強調色、h3 以下與粗體用近黑色、各級標題固定字級；行內程式碼為 Consolas＋淺灰底＋強調色；連結用強調色；引用改為整寬淡色底板；表格滿寬、淡色框線、表頭灰底粗體。表格與引用框上下留白，儲存格內段落不加段距。

2026-10-27：對話視窗版面整理。標題列、訊息、搜尋列與輸入框共用一條欄寬（chat_window.COLUMN＝720）；標題列的標籤改成無框文字；連續兩個以上的工具（可跨步驟）收成一行「N 個工具」（ConversationView._fold_tools，展開狀態記在 open_tools；只剩被收起工具的步驟 MessageWidget.folded 不佔位），MCP 名稱顯示為「伺服器 · 工具」；訊息捲到輸入框上方時有漸層（Fade），「↓」置中在輸入框上方；Markdown 標題上方留白大於下方。新對話頁的「專案」「Agent」移進輸入框底列；推理選單加量表圖示。晨光・暖橘改為中性灰，橘色只留給送出與選取；極簡外觀的「新對話」改為白底框線鈕，自己的訊息改為淺灰底；窄欄與清單之間不畫線；清單選取列不再有 Windows 11 的強調條。額度浮動視窗蓋在使用中的視窗上時變半透明，滑鼠移上去恢復。字型維持 JhengHei：實測改用 Segoe UI 開頭或字型清單時，Qt 會失去中文粗體或改用細明體粗體。清單項目符號和文字之間的距離是 Qt 固定的，沒有改。完整 338 項測試通過；尚待使用者實機驗收。

2026-10-24：晨光・暖橘在使用者的 Windows（深色模式）有很多黑色：樣式表沒蓋到的地方用了 Windows 深色調色盤。現在 Dock.apply_theme 設 app palette（theme.palette()）與 colorScheme（theme.is_dark()），tint_caption 對所有外觀設標題列顏色與 DWMWA_USE_IMMERSIVE_DARK_MODE，Dresser 對系統框對話框也 tint，選單／提示都 round_mask。

2026-10-24：使用者要對話區像 Claude／Codex 桌面版，不要聊天室效果：本機主題不再有 draw_reply（回答不畫卡片與兔子），draw_bubble 沒有尾巴，#ToolRow 無框透明。分組（_regroup）仍用來決定間距與整段複製。

2026-10-23：對話區：items 間距 0，每則訊息自帶 GAP；_regroup 把相鄰的助理項目設成 joined（上下相接、只有第一張有兔子與蝴蝶結、只有最後一張保留複製列，copy_text 取整組）。文字大小存在 ui.json `text_size`（normal/large/larger＝1.0/1.15/1.3，預設 large），所有樣式表經 theme.sized()，QuestionText 與 App 字型用 px()／TEXT_SCALE；改大小時 ConversationView.reload()。QuotaFloat 在 resizeEvent 用 round_mask，甜蜜外觀用 draw_settings_card 畫底。fold_mark()／heart_icon() 取代 ♡ 字元。

2026-10-23：MessageWidget.has_content()：沒有文字／思考／工具的助理項目不顯示（Codex 的 reasoning 常沒有 summary）；空的思考列隱藏。question_text.cjk_emphasis 讓中文標點旁的 ** 能收尾（CommonMark 的 flanking 規則），程式碼不動。

2026-10-23：工具頁新增鬧鐘（`agentdock/tools/alarm/`：core.py 排程／文字／存檔，不匯入 Qt；__init__.py 清單、AlarmEditor、RingWindow、Bell（winsound 迴圈播放 C:\Windows\Media\Alarm01-10.wav，其他系統 beep））。每秒檢查；錯過超過 10 分鐘就跳過；響了先 rang()（單次的停用），貪睡 5 分鐘再啟用。新增 9 項測試。

2026-10-23：設定頁重做成桌面版的樣子：左邊分類（一般、外觀、Agent、MCP、擴充、備份），`ui/settings_page.py` 提供卡片、分類、開關、外觀縮圖；AgentsView(wide=True) 的 `_rebuild_wide` 畫 Agent 卡片（`agent_info.AgentFacts`：程式版本、預設模型、額度工具知道的帳號）與 MCP／擴充表（每個 Agent 一個開關，狀態 on／off／adding／removing／pending／alert／missing／disabled／system／na）。狀態判斷抽成 `_server_status`、`_library_status`、`_ext_status`、`_ext_library_status`，窄版卡片沿用。一般／外觀由 TrayApp 提供並與系統匣選單同步。本機主題可畫 draw_settings_card／draw_settings_nav／draw_switch。

2026-10-23：設定頁只列對話視窗能用的四個 Agent（AgentManager.hidden_kinds＝RETIRED_KINDS：Claude Desktop／Cowork、GrokBot 不列出、不提供新增，登記保留在 agents.json）；Claude Code 改名 Claude（舊的預設名稱自動改）。工具頁 GrokBot 額度不受影響（直接讀 GrokBot 的登入）。

2026-10-23：超娘外觀全面版。主視窗改為無框圓角，自畫粉色緞帶標題列（愛心與果凍按鈕、拖曳、邊緣縮放、雙擊放大）；最左窄欄改為荷葉邊緞帶；AI 回覆放進蕾絲奶油卡片並有兔子頭像；程式碼區塊為草莓牛奶格紋；新對話頁有抱著愛心的兔子；整套按鈕、欄位、勾選框、捲軸、選單、提示改為糖果樣式；收合符號改為愛心。都由本機主題繪製，切回極簡即恢復系統外框。完整 313 項測試通過。

2026-10-23：可愛外觀擴及每個角落。所有對話框（含提示訊息、輸入框）改為無框，由本機主題畫蕾絲外框、緞帶標題與愛心關閉鈕；選單與提示框不再露出方角；提示訊息按鈕改中文、圖示改主題圖示；送出／停止鈕與自己的訊息泡泡可由主題繪製；Windows 11 主視窗標題列換成主題色。完整 312 項測試通過。

2026-10-23：可愛外觀的圓角與裝飾。單行的泡泡與工具列圓角超過高度一半時 Qt 會整個不畫，改為依高度設定半徑；清單選取改為整列一個圓角底色（原本斷成兩段並露出系統藍色方塊）；本機主題可替對話視窗畫背景、清單、輸入框與標題（theme.decorate），使用者的本機主題加上圓點背景、蕾絲花邊卡片、緞帶蝴蝶結與標題緞帶橫幅。完整 311 項測試通過。

2026-10-23：操作對齊 Claude／Codex 桌面版。新對話頁改為標題＋大輸入框（底列「＋」、權限、模型・推理、圓形送出），下方「專案或資料夾 ▾」「Agent ▾」與「最近」對話；送出／停止合成一顆圓鈕；窄邊欄不再滑過展開，再點「聊天」或 Ctrl+B 收起清單，清單可拖寬、滑過出現「⋯」、可釘選；空白輸入框按 ↑ 叫回上一則、`/` 指令（含 Claude Code 回報的指令）、`@` 檔案、整個視窗可拖檔；Ctrl+K 改為搜尋面板；往上捲時出現「↓」。對話視窗裡 Claude Code 顯示為「Claude」；Claude 在開第一個對話前就列出模型（只送 initialize，實測 Claude Code 2.1.309），名稱含版本（Opus 5.5、Fable 5.1…），預設模型顯示目前對應的版本；權限選單以各 Agent 的預設模式代替「預設權限」，Claude 拿掉「自動判斷」。完整 308 項測試通過；尚待使用者實機驗收。

2026-10-22：對話視窗第三批。回覆上的「分叉」把對話到那一輪複製成新對話；「匯入對話」列出各 Agent 自己存的工作階段（含在終端機或其他 App 開的），匯入後在 AgentDock 接續；標題列顯示上下文用量與 Git 分支／變更數，點開 Git 視窗可看差異、暫存、提交、推送、拉取、切換分支。完整 297 項測試通過，另以真實 Claude Code、Codex、Grok 與 OpenCode 服務實測分叉、匯入清單與上下文；尚待使用者以真實帳號驗收。

2026-10-22：對話視窗第二批。附件與圖片（迴紋針、拖曳、貼上截圖）；模型、推理強度、權限模式選單（清單來自各 Agent，新對話依 Agent 記住，對話中立即套用）；思考過程收合；檔案修改上色差異；壓縮對話（⋯ 或 /compact）；左側搜尋比對內容、對話內 Ctrl+F。完整 283 項測試通過，另以真實 Claude Code、Codex、Grok 實測圖片、設定、推理、壓縮，以真實 OpenCode 服務實測模型與 agent 清單、切換與壓縮；尚待使用者以真實帳號驗收。

2026-10-22：對話視窗第一批補強。清單右鍵選單（改名、移到專案、複製工作階段 ID、在終端機接續、刪除；F2／Delete），刪除可選擇連 Agent 端紀錄一起刪；清單顯示最後更新時間；訊息與程式碼區塊複製、編輯後重送、失敗或停止後重試；執行中可繼續送（Codex 用 turn/steer）；Ctrl+N、Ctrl+K、Esc、Alt+↑↓；重開回到上次的對話。完整 276 項測試通過，另以真實 Claude Code、Codex、Grok 實測排隊／插話與刪除。

2026-10-22：Grok 接上收發。透過 `grok agent stdio`（ACP 標準協定）一個子行程服務全部 Grok 對話；授權用 session/request_permission、問題用 Grok 的 ask_user_question 擴充、停止用 session/cancel，紀錄由 session/load 重播；不開 yolo，沿用 Grok 自己的權限模式。完整 260 項測試通過，另以真實 Grok 1.0.67 搭配假模型實測；尚待使用者以真實帳號驗收。

2026-10-22：Claude Code 與 Codex 接上收發，對話視窗三家都能用。Claude Code 直接講 SDK 的 stream-json 協定（不另裝 SDK），每個使用中的對話一個子行程、閒置 10 分鐘關閉、下次以 --resume 接續，紀錄讀 ~/.claude/projects 的 jsonl；Codex 用一個 codex app-server（JSON-RPC），thread/start／resume、turn/start／interrupt，紀錄用 thread/read。授權（允許／一律允許／拒絕）、問題（AskUserQuestion／request_user_input）、停止、失敗原因與子行程當掉都顯示在對話裡；權限規則沿用各工具自己的設定。關掉 AgentDock 時這兩家的進行中回合會中斷（OpenCode 不受影響）。完整 247 項測試通過，另以真實 Claude Code 2.1.288、Codex 0.163.0 搭配假模型實測；尚待使用者以真實帳號驗收。詳見 [DISPATCH.md](DISPATCH.md)。

2026-10-22：改為系統匣 App，浮動卡片退場、Inbox 停用。啟動後常駐系統匣（AgentDock.exe 直接開啟會顯示對話視窗；開機自動啟動只在系統匣），關閉視窗收回系統匣。設定（Agent／MCP／擴充）與工具（額度、額度浮動視窗開關）移入對話視窗。系統匣角標與「待授權／待回答」Windows 通知（可關）。broker 對 ask_user 回停用錯誤、丟棄 report 與活動事件；設定「全部更新」改為移除 Inbox MCP 與 inbox-activity 擴充。舊浮動卡片以 `"shell": "card"` 保留一版。完整 217 項測試通過。

2026-10-22：新增對話視窗（第一版）。像 Claude Desktop 的獨立視窗：可展開的窄邊欄（聊天、工具、設定置底）、依專案分組的對話清單（拖曳換專案、搜尋、未分類）、完整對話紀錄與即時出字、工具動作收合、對話內授權與問題、Enter 送出；專案有根目錄與預設 Agent。只接 OpenCode；Git、工具頁、附件、模型選擇留待下一版。完整 212 項測試通過，並以真實 opencode 2.0.32 服務搭配假模型在極簡與私人主題下實測。詳見 [DISPATCH.md](DISPATCH.md)。

2026-10-22：Inbox 從只收改為收發。「＋」送任務給 OpenCode 2.x（共用背景服務，關掉 AgentDock 任務仍繼續），卡片顯示最新回覆，可批准／拒絕授權、回答 question、接續、停止；原生 OpenCode 卡片可「接手」。重啟後先顯示狀態未知，連上後對帳。broker 未新增路由，批准只來自點擊。完整 201 項測試通過（含 24 項新測試），另以真實 opencode 2.0.32 服務搭配假模型實測往返；尚待使用者以真實模型驗收。Claude Code、Codex 尚未接上。詳見 [DISPATCH.md](DISPATCH.md)。

2026-10-18：新增 Grok CLI Agent，支援原生 config.toml MCP 與 hooks/agentdock.json 擴充管理，包含 Inbox 活動／授權提醒範本。MCP 停用／移除同步處理相容設定回退；匯入 Claude hooks 的 Grok 事件不重複回報。額度卡改為 Claude／Grok，GrokBot 明確共用訂閱作為個人額度主要來源，失敗時回退既有 xAI 來源。完整 178 項測試通過（45.11 秒），包含暫存設定預覽、備份、衝突及合併交易復原；未套用真實 CLI 設定。重啟後可從設定新增 Grok CLI，套用仍需預覽確認；CLI 載入與 hooks 實機驗收待套用。詳見 [GROK_CLI.md](GROK_CLI.md)。

最新驗證（2026-10-18）：GrokBot 帳號管理與額度接入後完整 172 項測試通過（44.82 秒），介面已用示範資料檢視。真實 MCP／額度讀取成功，寫入功能僅在隔離假服務驗證，尚待使用者預覽確認後實機驗收。

2026-10-18：GrokBot 卡片新增「管理 MCP」，可讀取目前登入帳號，新增／編輯／移除遠端 HTTPS MCP、由 MCP 庫加入遠端項目，並以備份重新預覽還原。帳號變更單獨預覽、備份、衝突重查、寫入與回讀；不混入本機全部更新，模糊失敗不自動重送。額度頁新增 GrokBot 每週用量、重置時間與統一訂閱共用標記。實機唯讀 MCP／額度查詢成功，未修改真實帳號設定；雲端指令、外掛與桌面 hooks 不宣稱支援。詳見 [GROKBOT.md](GROKBOT.md)。

最新驗證（2026-10-18）：GrokBot 登錄修改後完整 158 項測試通過（52.09 秒）；使用暫存資料，未寫入真實客戶端設定。

2026-10-18：設定「新增 Agent」加入 GrokBot，預設指向 `~/.grokbot/settings.json`，可登錄、編輯與移除。依本機已安裝 Grok Bot 的 app.asar 確認 MCP 透過 accountConfigProvider／accountMcpWriter 管理，settings.json 的 mcpBoxServers 只是識別清單；因此卡片顯示「MCP 由 GrokBot 管理」，不提供本機 MCP 新增，後端亦拒絕寫入。這次不新增額度或原生活動接入，不變更真實客戶端設定；重啟 AgentDock 後可選擇新類型。

2026-10-15：移除 Inbox 選項上方的操作說明及回答／備註框的冗長提示；AGENTS.md 明訂後續功能不得重新加入常識性操作教學或改放 tooltip／placeholder。

2026-10-15：新增 `codebase-memory-integration` 擴充庫項目與範本，管理 OpenCode V2／V1 1.18.29+ 搜尋補充外掛、Codex SessionStart／SubagentStart、Claude Code 生命週期與搜尋／讀檔 hooks。接管既有已辨識接入、清除重複 registrations，沿用 MCP 庫執行檔與環境；MCP 更新連帶排入擴充預覽。MCP 與擴充合併為單次備份／套用／失敗復原交易，保留其他 hooks 及 TOML 註解。完整 156 項測試通過（54.03 秒），測試只用暫存資料。已加入庫定義，尚未套用真實客戶端；重啟 AgentDock 後「全部更新 → 預覽並套用」，新接入可從 Agent 的 ＋ 加入。Codex 更新後需信任 hooks；技能／AGENTS.md 及上游產生器未改動，客戶端實際載入與圖譜補充效果仍待套用後驗收。

最新驗證：Codex 背景建議過濾修正後完整 148 項測試通過（45.75 秒）。測試使用暫存資料，未改真實客戶端設定；尚需重啟 AgentDock 後確認實際下一輪對話。

2026-10-14 Codex／WNReport 洗頻追查：實際新增的 session 診斷標記為 composer_predictions（產生下一句建議輸入），不是新的使用者討論。先前只排除純 SessionEnd 不足，因背景工作亦有活動事件。hook 現在辨識 UserPromptSubmit 的明確 app-generated suggested-next-message 前綴，只傳 background 布林，不存 prompt、不讀 transcript；標記在同 owner/client/session 的後續事件與重啟後保留，背景一般活動不列卡，意外的待授權仍可見。所有 ended 原生活動退出列表，舊背景結束卡片一併隱藏，原始紀錄保留；正常任務結果不受影響。需退出並重開 AgentDock，Codex hook 指向原始檔不需更新設定。OpenCode 使用者尚未實測，不宣稱已完成實機驗收。

本次討論歸屬修正驗證：完整 146 項測試通過（44.92 秒），包含 V1/V2 外掛對隔離 broker 的 session 關聯、MCP 回報、重啟、caller 隔離、重試、授權同卡及問答元件保留。

2026-10-14 Inbox 討論歸屬修正：新增持久化的 owner／work_id → 完整 session 關聯，同一討論的不同子任務只顯示一張卡片，保留原始工作與重試 ID；待答問題全部保留，晚到的關聯重用輸入元件。相同 session 的原生活動不重複列卡，待授權合併在同卡，解除後恢復結果。OpenCode V1／V2 工具執行 hook 與 Codex／Claude Code hook 只傳 Inbox 工具的 work_id/session_id；MCP 另接受可選 conversation_id，指引要求跨後續任務保持 work_id。不依名稱、時間或短碼猜測關係；舊紀錄收到明確關聯後可合併，缺少證據的歷史卡片維持分開。需重啟 App／重新連線 MCP，OpenCode 接入檔須經設定預覽、備份、確認更新後重啟客戶端；本次未寫入真實客戶端設定。真實客戶端自動關聯尚待實機驗收。

2026-10-14 Inbox 原生工作階段結束事件只更新既有卡片，不再單憑結束事件建立任務；同一 owner／client／完整 session ID 持續更新原卡，結束不重新標成未讀。舊版只有 ended 狀態、沒有活動證據標記的卡片退出列表（包含無法判別的舊已結束聊天），儲存紀錄保留；再次收到活動時恢復同一卡片。不同聊天仍分開，不依專案名稱或 ID 前 8 碼合併。完整 141 項測試通過（53.19 秒）；重啟 AgentDock 後生效。

2026-10-13 Inbox 單選可再次點擊已選選項取消，支援圓鈕、整列及鍵盤空白鍵；仍維持最多選一項。取消後保留補充說明與草稿，可只送出文字答案。

2026-10-13 Google Sheets MCP 斷線診斷：整張表的格式回傳經 FastMCP 文字／結構化雙重表示後，實測單次 15,799,712 與 12,609,477 bytes，超過 OpenCode 接收端 10 MiB。新增可選啟動器與範圍／8 MiB 回應防護，保持資料完整與連線可用；完整 138 項測試通過（44.17 秒）。設定尚未套用，須預覽確認與備份。詳見 [診斷與設定預覽](GOOGLE_SHEETS_MCP.md)。

2026-10-12 Inbox OpenCode 外掛亦補上 V2 default id/setup 與事件串流訂閱，保留 V1 1.18.29+ server 接法；卸載時中止並等待訂閱結束，斷線不影響客戶端，維持事件白名單及逐筆 permission ID 提醒，不傳聊天內容或批准決策。隔離 broker 整合測試涵蓋兩種介面、清理及串流失敗；完整 136 項測試通過。

2026-10-12 RTK OpenCode 修正：內建外掛改為 default id/setup（V2）與 server（V1 1.18.29+）雙介面，透過有逾時的 execFile 呼叫 rtk rewrite，支援成功退出碼 3、V2 替換 event.input，失敗保留原指令；不依賴 which 或額外 npm 套件。標準 RTK 範本固定使用 AgentDock 維護的接入檔，舊擴充庫 source_url 與既有下載快取不再將它覆蓋回舊格式；自訂來源仍保留原更新方式。共用 RTK 執行檔、PATH、Claude/Codex hooks 不因外掛修正而改動。既有 OpenCode 外掛差異透過「全部更新」預覽、備份後套用。完整測試 135 項通過，包含隔離的新舊介面合約、升級、備份、衝突及復原；尚未在真實 OpenCode 工作階段驗證實際指令改寫。早於 V1 1.18.29 的版本不在此雙介面支援範圍。

2026-10-08 測試精煉：近期通知／Markdown 測試共用初始化及主題還原，動畫改等待實際 timeout，移除 5.2 秒固定等待；保留優先級、焦點、聲音去重、主題及 MCP 無 GUI 匯入驗證。產品程式未改動，完整 132 項通過（本次 42.66 秒）。

2026-10-08 提問格式：單題、多題前言與選項說明支援 Markdown 段落、粗體、標題、清單、引用與程式碼；選項標題維持純文字。可自由穿插 :::warning／:::note 區塊，依主題顯示警示或較小的補充文字，不增加必填欄位。提問不再整段使用強調色，長程式碼可在窄視窗換行；不解析來源 HTML 或載入外部圖片。MCP 工具與欄位說明已同步，重啟 App 並重新連線 MCP 後生效。完整測試 132 項通過，最後排版調整後相關 10 項通過，深淺主題已檢視。

2026-10-08 通知改為跟隨主題：移除自動跳出的文字提示與其右鍵開關，不再將整顆 Icon 改色。原圖示旁持續顯示狀態角標（! 待授權、? 待回答、× 失敗、✓ 完成）與數量，新通知播放三輪光圈後停止；狀態角標保留至處理／已讀。通知預設沿用 ACCENT，主題可選填 NOTIFICATION_ACTION／ERROR／SUCCESS／INFO 自訂配色；切換主題立即更新且不重播。浮動內容視窗與 Icon 不再顯示通知數量的滑鼠提示，避免遮住閱讀；數量保留在角標。最新完整測試 129 項通過，另以 Windows 原生視窗驗證 3 項通知測試（無新增彈窗、焦點保留、光圈停止與主題切換）。點擊優先級與可選待處理提示音維持，原生回合結束不當作任務完成。

更新日期：2026-10-07。最新完整測試 126 項通過（含直式排列、方向記憶、邊界及草稿保留）。伸縮底板一般高度縮至 38px，起始端點 36px 藏於 42px Icon 內；完全收起時保留非空的畫面外遮罩，避免 Qt 將空遮罩視為取消裁切。動畫改為固定透明視窗尺寸、只更新繪製與遮罩，避免原生視窗縮放造成黑框／亮線；Tab 固定尺寸並淡入淡出，Icon 改為四格工具入口。黑框／亮線仍待原使用者桌面實機確認。Icon 遮擋修正後，Dock／通知相關 7 項通過，包含獨立 Windows 原生視窗測試：強制將底板置頂後，以 WindowFromPoint 驗證 Icon 點擊命中，涵蓋左右展開及動畫期間，再驗證點擊收合。實際 Qt 主事件迴圈的啟動測試使用獨立程序，避免執行其他測試遺留的回呼；正常退出允許內容視窗關閉，避免被平時的收合處理攔截。先前 Qt 原生存取違例的其他根因仍未確認消除。

Inbox 依 Agent 分類，可用箭頭展開／收合。任務直接顯示待回答／等待 Agent 回報／已完成／失敗／已取消，不用 Hover 區分結果；不顯示結果內文。待答問題點擊後原地展開 Foldout，送出後收合。所有未回答問題保留，新問題不搶焦點、不重建既有輸入框；收合保留草稿。沒有第二層對話頁或歷史瀏覽，既有資料仍保存在本機。

已回答／取消的舊問答不再當作進行中證據：無 pending、無最新結束回報、也非本次啟動後回答中的任務退出列表；不刪儲存紀錄。本次已交回答、尚未收到結束回報的任務顯示「等待 Agent 回報」。

新增 inbox-activity 擴充範本：Codex、Claude Code hooks 與 OpenCode plugin 只接原生生命週期，不讀聊天內容、不代替批准。授權提醒使用系統通知與紅點，回原客戶端批准。原生工作階段另列專案／session 短碼，不猜測它與 MCP work_id 的對應；近期活動顯示時間，超過兩分鐘無事件或 Inbox 重啟即顯示未知。回合結束不等於任務完成。Claude Desktop 一般 Chat／Cowork 尚無已驗證接入；Desktop 中 Claude Code 可使用其 hooks。

擴充仍須在設定預覽、確認及備份後加入客戶端；Codex hooks 另須於客戶端信任。需先啟動新版 AgentDock，未啟動時事件不補送。Codex PermissionRequest 可能走自動審核；Codex／Claude Code 缺少完整的批准結果關聯，未確認解除前保留請求提示，回合結束清除或逾時顯示未知。OpenCode 依 permission ID 逐筆解除。尚需各真實客戶端實機驗收。

Inbox 已收斂為問答與任務結果收件匣。ask_user／get_user_answer／附件回答維持原協定；report_to_user 只接受 completed、failed、cancelled，回傳摘要即可，不要求複製原聊天全文。

- 移除原生聊天掃描、Desktop IndexedDB／HTTP 快取及 vendored 解析器。不讀聊天歷史、不補抓原生附件、不增加登入流程。
- 舊鏡像事件與附件仍保留在 data，不在 Inbox 顯示；舊 native alias 不再影響問答歸屬。問答與主動回報的結果、未讀狀態跨重啟保留。
- 介面及 MCP 服務顯示名稱為 Inbox；設定識別統一為 inbox；舊 agentchat 透過設定預覽、備份後遷移，工具名稱及 broker 路由維持。四種本機客戶端已經使用者確認、備份並套用 agentchat → inbox；檢查均只保留一個啟用的 inbox 連線，客戶端需重新連線載入。
- 未讀／待答以主題色圖示角標提示，不自動開啟內容視窗、不搶走輸入焦點。保留先前縮放修正，實機效果仍待驗收。
- 收斂前版本已提交為 4d97351，可由 Git 找回；歷史研究與驗證紀錄保留在 VERIFICATION.md、history/CHANGELOG.md。

## 已實作

- 設定頁改為分離的代理圓角卡片、分區標題與固定底部設定選單；可愛外觀使用柔粉標題底色。加大操作間距，長工具名稱可省略並以提示顯示全名，空擴充提示自動換行，避免窄視窗擠出按鈕。本機私人主題另調整奶白閱讀底板、低彩度粉色與邊緣蝴蝶結；私人檔案不提交 Git。離屏示範資料已檢查 580px／360px 寬度，實際桌面外觀需重啟後確認。

- Icon 右鍵「展開方向」可選橫式／直式，預設橫式並記住選擇。直式 Tab 文字不旋轉，通常向下展開、底部空間不足改向上，內容貼側邊；切換保留目前分頁、草稿與拖開位置。

- 內建「晨光・暖橘」淺色外觀（柔白底、柔和螢光橘、灰藍字），深色外觀命名為「墨夜・青檸」（黑綠底、白字）；Icon 右鍵 → 外觀切換，套用至 Icon、Tab、Inbox 及設定並記住選擇。

- Icon 與橫向 Tab 列共用視覺膠囊底板，細框／柔和深色底板搭配綠色選中分頁，實心 Icon 加入識別符號；220ms 動畫可中途反向。底板以原生視窗遮罩排除 Icon 圓形區域，避免動畫期間遮擋顯示及攔截點擊。啟動只顯示 Icon；無新通知時點擊展開純 Tab 列或恢復收合前的分頁視窗，再點 Icon 整組隱藏。點目前 Tab 只關內容，保留 Tab 列，不另設「收合」按鈕。各頁記住尺寸與獨立位置；Inbox 預設內容高 700、設定高 380。首次載入此版重設這兩頁的舊高度，之後繼續記憶調整。
- 內容預設貼在 Tab 列下方，空間不足改上方；較高視窗上下放不下且側邊足夠時改靠側邊，保留高度，均限制於可用螢幕範圍。拖動內容標題／背景後獨立定位，顯示「貼回」按鈕；跟隨狀態下移動 Icon 會一起移動橫列與內容。收放與切頁重用原元件，保留草稿、捲動位置與問題展開；只有新通知會在重新開啟時觸發通知優先導覽。

- 點擊小 Icon 直接跳到通知：原生待授權 → 尚未展開的新問題（最新優先）→ 曾展開但未回答的舊問題（最早優先）→ 未讀失敗／完成／其他結果。問題自動展開、展開紀錄跨重啟保存，完成通知定位至任務；無待處理項目時維持展開／收合。只在使用者點擊時導覽，新通知本身不搶焦點。

- 小圖示在新通知時顯示主題色外框、符號角標與三輪漣漪（共 4.8 秒），之後保留符號與數量；全部處理／已讀後清除。隱藏或全螢幕時停止動畫計時，不改變焦點、圖示尺寸或位置。更新後需重啟載入。

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
