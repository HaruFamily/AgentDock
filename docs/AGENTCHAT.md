# Inbox：問答與任務結果

Inbox 集中處理 Agent 的待答問題與任務結果。完整對話、原生聊天圖片及工具執行歷史留在各 Agent App。

## Agent 使用方式

- 需要使用者決定時呼叫 ask_user；提供穩定的 work_id 與 request_key。
- 等待逾時用 get_user_answer 繼續等待。pending、timeout、cancelled 不代表同意；不得自行選預設答案。
- 任務完成、失敗或取消時呼叫 report_to_user，kind 分別為 completed、failed、cancelled。text 寫簡短結果與必要下一步。
- 同一任務的問題與結果共用 work_id；每個結果用不同 request_key，僅重試同一結果時重用。
- 不鏡像使用者指令、進度或整份聊天。尚有待答問題時不能回報結束。

```json
{"request_key":"result-1","work_id":"task-1","work_title":"修正登入問題","kind":"completed","text":"登入問題已修正，相關測試通過。"}
```

## 顯示與保留

依 Agent 分類顯示任務列表，分類可用箭頭展開／收合；收合保存草稿，新事件不自動展開分類，隱藏的結果不標記已讀，每個任務只留進行中／已完成／待回答三種狀態，不需進入第二層，也沒有歷史瀏覽。待答問題點擊後原地展開 Foldout；保留文字、選項、備註、附件及草稿，送出後收合成「進行中」。所有未答問題都保留，不會被新問題覆蓋；既有儲存資料不刪除。

小圖示紅點代表待答或未讀結果；需視窗啟用且訊息可見才標記已讀。不自動彈窗、不搶焦點、不發送新任務。

問答、結果及未讀狀態跨重啟保留。移除區域不等於取消 Agent 任務或同意問題。

## 從 AgentChat 升級

顯示名稱改成 Inbox；MCP 設定 key 改為 inbox，舊 agentchat 由「全部更新」預覽後遷移，ask_user／get_user_answer／read_answer_attachment／report_to_user 工具名不變，broker 路由也不變。

重啟 AgentDock 並重新連接客戶端 MCP，以載入新的工具指引。舊版 started／user_message／progress 回報會被拒絕。

不再啟動任何原生聊天接收器。先前匯入的鏡像聊天資料與附件保留在本機，但不在 Inbox 顯示；問答回到原始 caller 與 work_id，不再套用原生對話 alias。

沒有自動取圖、額外登入或自動完成偵測。結果通知依賴 Agent 明確呼叫工具，不能保證各客戶端都會主動回報。
