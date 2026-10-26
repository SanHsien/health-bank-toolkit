# Changelog

## 未發布

- 脫敏補強（只變嚴）：遮蔽 `02-2345-6789`、`+886 2 …` 這類分組寫法的市話、`1990年5月12日` 西元年月日、小寫手打的身分證；個資 key-value 列落在表頭位置時，值不再外洩成 JSON 的鍵。個資欄位標題的比對改為不分大小寫（`Email`、`EMAIL` 也整欄遮蔽）。
- 補齊維護骨架：`docs/DEVELOPMENT.md`、`docs/DECISIONS.md`、`SECURITY.md`、issue 範本設定、`.editorconfig`，並在 Dependabot 設定註明依賴新鮮度不適用的理由。

## 2026-10-25

- 從 `SanHsien/ai-inbox` 獨立成 repo，保留相關檔案歷史。
- 新增已發布內容檢查測試：不得出現身分證字號、電話格式，並須保留免責聲明。
