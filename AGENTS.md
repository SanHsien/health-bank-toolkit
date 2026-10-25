# AGENTS.md — health-bank-toolkit

給在本 repo 工作的 AI Agent（Claude Code、Codex、Antigravity 等）。

## 鐵律

1. **真實資料絕不進 repo。** 不可把真實健康存摺匯出檔、解析結果、截圖或任何片段寫進檔案、測試、提交訊息或 issue。測試只用 `tests/fixtures/` 的純虛構資料。
2. **脫敏只能變嚴，不能變鬆。** 修改 `tools/health_bank_parser.py` 的遮罩規則時，先補一個「舊規則漏遮」的反例測試，確認它在修改前會失敗；放寬任何規則都要在提交訊息寫明理由。
3. **不提供醫療建議。** 範本與儀表板不下診斷、不建議調整藥物，並保留醫療免責聲明。
4. **只用 Python 標準庫。** 不新增第三方依賴。

## 開發流程

- 改動後執行 `python -m unittest discover -s tests`，全綠才提交（測試與 `git commit` 用 `&&` 串接，不用 `;`）。
- 不要設 `PYTHONUTF8` 來讓測試通過；Windows 終端的編碼問題要在程式裡處理。
- 格式或判讀規則有變，同步更新 `docs/health-bank-export-format.md`。
