# 開發

從倉庫根目錄執行。需要 Node.js >=22.12；Windows 桌面與封裝驗證使用 Windows x64。

```powershell
npm ci
npm run build
npm start
```

```powershell
npm test
npm run test:platform
npm run test:desktop
npm run test:startup
npm run package:win
npm run test:package
```

- src、extensions 是源碼；不要直接改 dist/output 修正程式。
- node_modules、dist、.build、.packaging、test-results、.local、output 都是本機生成且被 Git 忽略。
- 以 AIL_DATA_DIR 指向唯一測試資料夾；不要使用真實個人 Agent 設定做自動測試。
- package:win 產生 output/AgentDock、output/QAInteract 及 ZIP。若 output/AgentDock 已有使用者 data/modules，會拒絕覆蓋；先搬到個人使用位置。
- 修改模組內容後正式發行需提高版本；相同版本不同內容禁止覆蓋。
- 標準 npm ci 可重建依賴，不需保留旧開發目錄。

GitHub repo 與本機資料夾不必與產品模組一對一；本專案用一個倉庫維護平台及所有第一方擴充。
