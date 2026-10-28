---
name: agentdock-runtime-tools
description: 維護 AgentDock 的免安裝啟動、runtime、外部工具載入與系統整合；修改 launcher、setup、更新或新增工具時使用。
---

# AgentDock 執行環境與工具

在 AgentDock 倉庫讀 AGENTS.md、docs/DEVELOPMENT.md 與 docs/STATUS.md，依修改再讀 launch.py、paths.py、scripts/setup.ps1 或工具註冊來源。

- 原始碼執行，無一般套件發行流程；AgentDock.exe → setup → runtime 的既有可攜式入口需保留。不要因依賴缺少改成系統安裝。
- Python、uv、套件與下載快取留 runtime，私人資料留 data。測試必須把資料與路徑導向暫存目錄；不要為驗證讀寫真實 data。
- 不加入開始功能表、系統 PATH 或登錄項。只有既有 UI 明確操作的加入 PATH／HKCU Run 可按該功能契約執行。
- 外部工具在 agentdock/tools/<名稱>.py 宣告 TOOL = ToolSpec(...)。可選依賴與載入失敗不得阻止主程式啟動；不要在 import 時下載、登入或啟動服務。
- 背景程式 CREATE_NO_WINDOW；可見主控台只沿用明確的登入／終端機接續使用者操作。更新需針對目前選到的實際安裝來源，不另裝不相干副本。
- App 關閉要清理自己擁有的資源；共享服務生命週期依 DISPATCH，不憑程序名稱殺其他工作。重新啟動遵守 lock 釋放，不同時起第二個 broker。

測試用假 executable／subprocess 與暫存目錄，驗證參數、環境、安裝來源、失敗隔離與重啟狀態。不要真的更新個人客戶端來驗證測試。依賴版本與鎖檔同時檢查，尤其 PySide6 Essentials／Addons 必須一致。
