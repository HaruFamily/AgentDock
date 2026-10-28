---
name: agentdock-validation
description: 執行 AgentDock 修改後的隔離測試、介面驗收與結果記錄；適用功能修改及回歸排查，不自行觸發真實 Agent 或推送。
---

# AgentDock 驗證

先讀倉庫 AGENTS.md、docs/STATUS.md、docs/DEVELOPMENT.md，查看 git status，保留使用者與其他工作留下的變更；不清理整個工作目錄。

- 結構證據依目前圖譜 tier，確認 project/generation，候選檔做 check_index_coverage。partial/stale/unknown 直接讀來源補足；__pycache__ 排除不是程式來源缺漏。沒結果不是不存在，沒工具就來源 fallback。
- 用 repo runtime/venv/Scripts/python.exe 或 Test AgentDock.cmd 的環境跑測試；也可 uv run pytest -q。Qt 測試用 QT_QPA_PLATFORM=offscreen，所有資料／client 設定在 tmp_path。只設定本次 process 環境，不改機器設定。
- 先測變更涉及的行為，再依 AGENTS 要求完整 pytest；通過後只在新增修改、失敗或未決風險時重跑。不要為樣式常數寫鏡像測試。
- 測使用者可觀察的結果：按鈕是否做正確動作、草稿不串台、取消不落盤、唯讀可複製、圖示顏色重開仍在、實際幾何對齊。
- 視覺驗收以假資料 render/screenshot，不啟動使用者真實 App 或讀私人主題。記錄實際確認的頁面、尺寸、主題；字型缺字、未驗證真實服務要揭露，不能以測試通過取代畫面檢查。
- git diff --check；更新 docs/STATUS.md 與 docs/history/CHANGELOG.md。結果寫實際通過數與限制；已知失敗應辨別是否本次造成，不直接貼「既有」標籤。需要重啟載入程式時在交付說明。
- 不因驗證而提交／推送；只有本次任務既有明確授權才可推送。不得刪除使用者變更來使測試通過。
