# 驗證記錄（0.6.0）

自動化（Linux 離屏環境，pytest 13 項）：

- 問答儲存：重複 request_key 冪等、內容衝突拒絕、單選限制、空白提交拒絕、取消不產生答案、重新載入後保留。
- broker：錯誤 token、帶 Origin、別的 owner 都被拒絕。
- MCP：真實 stdio 行程呼叫 ask_user → 回答（選項、備註、文字、附件）→ read_answer_attachment；逾時回傳 pending 而非答案。
- 設定管理：Codex 保留註解、冪等、停用；Claude 停用移出並還原、未還原前不可移除登錄；舊 Electron 項目替換、他人同名項目拒絕；預覽後原檔變動拒絕；遠端只接受 HTTPS；Store 版 Claude 路徑偵測。
- UI：透過實際元件選擇、備註、輸入並提交；新問題不會搶走正在回答的題目。

尚待 Windows 實機確認：小球與面板的置頂、系統匣、pythonw 從客戶端自動啟動、各客戶端實際呼叫 ask_user。
