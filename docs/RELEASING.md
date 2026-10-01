# 發行

同一 GitHub repository，分開提供平台與 QAI。

1. 提高 package.json、套件／平台相容版本與安裝說明中的版本。
2. 執行相關測試，再 package:win、test:package。
3. 檢查 Git staged 檔案沒有執行資料、秘密、測試產物或依賴。
4. 提交及推送 main；以該提交建立版本 tag。
5. 將 output 的下列檔案放到該版本 GitHub Release。

| 附件 | 用途 |
| --- | --- |
| AgentDock-版本-win-x64.zip | 平台與共用執行環境，不含 QAI |
| QAInteract-版本.zip | QAI .admod、Setup-QAI.ps1、平台依賴資訊 |
| QAInteract-版本.admod | AgentDock 內可直接匯入或下載 |
| SHA256SUMS.txt | 發行檔校驗值 |

QAI ZIP 的 platform-dependency.json 記錄平台版本、直接下載 URL 與 SHA-256。
源碼中的 extensions/QAInteract/Install.ps1 下載指定版本 QAI ZIP、驗證校驗值，再交給其中的 Setup-QAI.ps1。
需要平台時 Setup-QAI.ps1 取得同版平台並驗證雜湊；已有平台則沿用。
安裝器不修改現有模組版本、不關閉使用者正在運行的平台；自動寫設定前須先結束平台。

主分支的腳本固定對應本版，避免 latest 在未來指到不相容版本。
發布成功後，從遠端下載一次並核對 SHA-256；新版本的每個必要附件都應存在。

尚未授予開源授權條款；GitHub 公開可讀不等於選定了 MIT 或其他授權。由專案擁有者另行決定。
