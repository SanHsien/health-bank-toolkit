# 路線圖

> 本 repo 後續工作的單一真相源。做完一項，就在同一個 commit 刪掉那一列；新工作只加在這裡。
> 現況段的數字是 2026-10-29 依 `main` `1132890f4969` 查的快照，開工前先重查 GitHub。

## 現況

健保快易通「健康存摺」離線脫敏解析器：HTML／ZIP／資料夾 → 結構化 JSON，附儀表板與 PDF 報告提示詞，純 Python 標準庫、不主動連網（`tools/health_bank_parser.py`）。2026-10-29 快照：open issue 0、open PR 0；Dependabot 與 code scanning 告警皆 0；`main` 上 CI、CodeQL 最近一次皆 success；無 release。2026-10-26 的深掃已補完四類脫敏漏遮（`CHANGELOG.md`、commits `6976361`..`338c495`），68 個測試綠；沒有已知未修的洩漏路徑。

## 近期：可以直接動手
| 項目 | 為什麼 | 驗收方式 | 建議執行者 | 證據 |
| --- | --- | --- | --- | --- |
| 決定 PDF 報告提示詞要不要保留「一般性健康建議」，並讓範本與鐵律 3 一致 | `templates/health-report-pdf-prompt.md:20` 要求「給一般性的健康建議」，`AGENTS.md` 鐵律 3 寫「不提供醫療建議」，兩者措辭有張力；範本是對外給使用者複製的內容 | 維護者決定後：範本與 `AGENTS.md`／`docs/DECISIONS.md`「範本不下診斷」同一措辭；`tests/test_published_content.py` 新增斷言鎖定該措辭與免責聲明並先確認舊文本會使其失敗；`python -m unittest discover -s tests` 綠 | 維護者（決定）→ Claude Sonnet（改寫＋測試） | `templates/health-report-pdf-prompt.md:20`；`AGENTS.md` 鐵律 3；2026-10-26 專責報告「需要維護者決定」 |
| 把審查時用的「大量虛構表格對比 origin 版本」做成 repo 內的確定性測試 | 脫敏規則「只能變嚴」目前靠每次人工審查驗證；固化後能機器擋住回歸 | `tests/` 新增以固定隨機種子產生的虛構表格（只用公認假值）；斷言已知會被遮的值在輸出中不以明文出現；在 `338c495` 之前的版本上至少有一條失敗（證明有擋力）；全套測試綠且執行時間不明顯變長；不得引入第三方套件 | Claude Sonnet | 2026-10-26 深掃報告「審查」段（6 萬餘張虛構表格比對）；`AGENTS.md` 鐵律 2、4 |
| 確認 `ai-inbox` 的 git 歷史只含工具碼、不含真實健康存摺資料 | 本 repo 已獨立；`ai-inbox` 目前追蹤檔已無健康存摺相關檔（2026-10-29 `git ls-files`，`20df187a8306`），但歷史可能仍留有，內容含個資風險 | `git -C <ai-inbox> log --all --name-only --format= \| grep -i health` 的結果逐檔判定（只有工具碼／測試虛構 fixture 即結案），結論寫入本 repo `docs/DECISIONS.md` 一則；若發現真實資料，停下交維護者決定，不得改寫歷史 | Claude Sonnet（盤點）→ 維護者（發現真實資料時決定） | 2026-10-29 `git -C ai-inbox ls-files` 無含 health 的追蹤檔；專責報告（2026-10-26）「需要維護者決定」 |

## 中期：等條件成立再做
| 項目 | 觸發條件 | 驗收方式 | 建議執行者 | 證據 |
| --- | --- | --- | --- | --- |
| `to_iso` 認得「2023年5月12日」格式 | 維護者拿到真實匯出中確實出現此寫法的欄位（目前真實匯出用斜線，現行行為是整格被遮罩：遺失日期但不外洩） | 先用虛構 fixture 寫失敗測試，修後日期轉為 ISO；遮罩測試仍全綠（不得因此讓任何個資欄變明文）；`docs/health-bank-export-format.md` 同步 | Claude Sonnet | `tools/health_bank_parser.py:382`；2026-10-26 深掃「刻意不修」第一項 |
| 跟進健保快易通匯出格式變動 | 維護者發現新匯出的 HTML／ZIP 結構解析不全（欄位為空或進 `generic`） | 取得變動處的**虛構化**樣本寫入 `tests/fixtures/`；解析器新增對應分類並有測試；`docs/health-bank-export-format.md` 更新；真實檔案不進 repo、提交訊息與 issue | Claude Sonnet | `docs/health-bank-export-format.md`；`AGENTS.md` 鐵律 1 |
| 引入 lint（如 ruff） | 維護者放寬「只用標準庫」，允許開發期工具 | `pyproject` 或 CI 設定 lint 並綠；不影響執行期零依賴；`AGENTS.md`、`docs/DECISIONS.md` 同步修訂 | 維護者 | `AGENTS.md` 鐵律 4；`docs/DECISIONS.md`「只用 Python 標準庫」 |

## 刻意不做
| 不做什麼 | 理由 | 什麼情況下重新考慮 |
| --- | --- | --- |
| 放寬任何脫敏規則 | 鐵律 2：洩漏個資的風險遠大於漏遮的修補成本 | 不重新考慮；真要放寬須在提交訊息寫理由並由維護者核准 |
| 遮罩 `0800` 免付費號碼、處理 UTF-32 BOM 誤判為 UTF-16 | 前者非個人資料；後者罕見（2026-10-26 深掃「刻意不修」） | 前者：發現有個人使用的 0800 號；後者：真實匯出出現 UTF-32 |
| 設 branch protection、PR 流程 | 維護者全專案慣例為單人直推 `main` | 出現第二位協作者 |
| 新增第三方依賴，包括 Dependabot 管理 Python 套件 | 鐵律 4；`dependabot.yml` 只管 GitHub Actions，新鮮度檢查以 `fleet-waiver: freshness` 豁免 | 同上「引入 lint」的條件 |

## 維護方式

自有 repo，非 fork，無 release。每次改 `tools/health_bank_parser.py` 先補反例測試再改，並用 `python -m unittest discover -s tests && git commit`（不用 `;`）；Dependabot 只更新 GitHub Actions。
