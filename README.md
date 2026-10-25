# health-bank-toolkit

把健保快易通「健康存摺」匯出的 HTML，**在本機離線**轉成脫敏後的結構化 JSON，再交給 AI 產出互動儀表板或 PDF 健康報告。

- 只用 Python 標準庫，不連網、不需要安裝套件
- 姓名、身分證字號、生日、電話、地址、病歷號、健保卡就醫序號一律遮蔽；個人資料表整張丟棄
- 醫事機構、診斷、藥品名稱、檢驗數值屬於有用資料，保留

> ⚠️ **真實資料絕不進 git。** 原始匯出檔與解析結果都放在私人資料夾。輸出資料夾位於任何 git 工作樹內時，工具會直接拒絕執行。
>
> 本工具與範本不提供醫療建議；報告與儀表板都要求附上醫療免責聲明。

## 快速開始

```bash
python tools/health_bank_parser.py <匯出資料夾｜單一 HTML｜ZIP> <輸出資料夾>
```

輸出：每個有資料的類別一個 JSON，外加 `summary.json`（各類筆數、日期範圍、套用了哪些脫敏規則）。

| 檔案 | 內容 |
| :--- | :--- |
| `labs.json` | 檢驗檢查結果（含偏高／偏低／正常／未判定） |
| `outpatient.json`、`inpatient.json`、`surgery.json` | 門診、住診、手術（含醫令明細） |
| `medications.json` | 用藥 |
| `dental.json`、`vaccinations.json` | 牙醫門診、預防接種 |
| `imaging_reports.json` | 影像或病理報告（自由文字經過脫敏） |
| `directives.json` | 器捐或安寧緩和醫療意願註記 |
| `generic.json` | 其他表格 |

類別由表頭判斷，不靠檔名。這次沒有資料的類別，上一輪留下的同名檔會被刪掉，不會誤當成本次結果。

## 內容

| 路徑 | 用途 |
| :--- | :--- |
| `tools/health_bank_parser.py` | 離線脫敏解析器 |
| `docs/health-bank-export-format.md` | 匯出格式、各類表頭、日期與檢驗判讀規則、已知陷阱（給 AI 讀） |
| `templates/health-report-pdf-prompt.md` | 產出 PDF 健康報告的提示詞 |
| `templates/health-bank-dashboard-prompt.md` | 產出互動儀表板的提示詞與 JSON 輸入契約 |
| `assets/health-bank-dashboard/index.html` | 單檔互動儀表板範例（純虛構資料，可直接用瀏覽器開啟） |
| `tests/` | 單元測試；`tests/fixtures/fake_health_bank.html` 為純虛構樣本 |
| `notes/` | 本工具的來源分析筆記（原在 [ai-inbox](https://github.com/SanHsien/ai-inbox)） |

## 測試

```bash
python -m unittest discover -s tests
```

CI 在 Ubuntu 與 Windows 上執行（不設 `PYTHONUTF8`，確保 Windows 終端也不會因編碼出錯）。

## 來源

2026-10-25 從 [`SanHsien/ai-inbox`](https://github.com/SanHsien/ai-inbox) 獨立出來，保留相關檔案的 git 歷史。

## 授權

[MIT](LICENSE)
