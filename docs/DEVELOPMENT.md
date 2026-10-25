# 開發指南

## 環境

- Python 3.11 以上，只用標準庫；不需要安裝任何套件。
- 不要設 `PYTHONUTF8`：Windows 終端的編碼問題要在程式裡處理，CI 也刻意不設。

## 本機驗證（gate）

```bash
python -m unittest discover -s tests
```

| 步驟 | 與 CI 對照 |
| :--- | :--- |
| 單元測試（含 `tests/test_published_content.py`：已發布內容不得出現身分證字號或電話格式、必須保留免責聲明） | `CI` 的 Ubuntu／Windows 兩個 job |
| 靜態安全分析 | `CodeQL` workflow |

驗證與 `git commit` 用 `&&` 串接，不用 `;`。

## 改脫敏規則

1. 先補一個「舊規則漏遮」的反例測試，確認修改前會失敗。
2. 再改 `tools/health_bank_parser.py`。
3. 脫敏只能變嚴；放寬要在提交訊息寫明理由。
4. 格式或判讀規則有變，同步更新 `docs/health-bank-export-format.md`。

## 測試資料

只用 `tests/fixtures/` 的純虛構資料。真實匯出檔、解析結果、截圖或任何片段都不可進 repo、提交訊息或 issue。
