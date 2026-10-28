---
name: agentdock-dispatch
description: 維護 AgentDock 的 Agent 收發、事件正規化、對話草稿、問答及子行程生命週期；新增協定或修復串流與錯誤恢復時使用。
---

# AgentDock 收發與狀態

相對路徑以 AgentDock 倉庫根目錄為準。先讀 AGENTS.md、docs/STATUS.md、docs/DISPATCH.md；協定版本以目前 adapter 與官方資料為準，不從技能記憶猜版本。

- UI → Dispatcher → adapter 是執行路徑；broker/MCP 不得新增回答、授權、送出路由。mcp_server 的匯入鏈不得含 PySide6 或 stdout 日誌。
- UI 只消費 OpenCode V2 形狀事件，廠商差異留 adapter。新增事件時追到 store、transcript、UI 接收端及假服務，不只修來源。
- pending／cancelled／timeout 絕不表示同意。授權、回答、停止只沿用使用者明確操作；傳送失敗保留請求，不能自動批准或自動重送決策。
- 終止錯誤必須離開 running；可重試錯誤保留重試狀態。去重遲到完成事件，不能結束下一輪。
- 送出失敗恢復原草稿與附件，但不得覆蓋使用者後來輸入。每個對話／專案草稿隔離；切換時不重建仍在作答的表單。
- 對話移除只影響 AgentDock 清單，不刪原生工作階段。編輯／回退遵守目前 adapter 契約，不順便還原磁碟檔案。
- App 啟動不主動啟動客戶端；子行程啟停與 OpenCode 共享服務例外見 DISPATCH。背景 CREATE_NO_WINDOW；登入／在終端機接續僅由使用者操作啟動可見主控台。
- 診斷只記 executable、版本、request/session 識別、錯誤類型等必要資訊，不記提示詞／憑證。Windows 非 UTF-8 stderr 不得拖垮 RPC。

驗證使用 tests/fakes 與暫存 store：正常、終止錯誤、重試、取消、子行程退出、遲到事件、重開及草稿切換。只有任務確實涉及真實服務且已有授權才使用真實 Agent；假服務通過不能稱真實協定已驗證。
