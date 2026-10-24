---
id: 2026-10-18-health-bank-html-claude-artifact-dashboard
title: "健保快易通健康存摺 HTML 轉化 Claude Artifact：個人醫療數據本地脫敏與互動式健康追蹤儀表板（Generative UI）實踐解構"
source_url: "https://www.threads.com/share/BATf6m8fjz/"
source_platform: "Threads"
author: "ingramchen (@ingramchen)"
analyzed_at: 2026-10-18
analyzed_by: "Antigravity"
status: done # pending | in-progress | done | archived
cluster: 2
cluster_role: "互動生成"
short_title: "健保快易通 Dashboard"
tags:
  - health-bank
  - claude-artifact
  - generative-ui
  - data-privacy
  - medical-dashboard
---

# 健保快易通健康存摺 HTML 轉化 Claude Artifact：個人醫療數據本地脫敏與互動式健康追蹤儀表板（Generative UI）實踐解構

- **來源連結**：[Threads 原貼文 (ingramchen)](https://www.threads.com/share/BATf6m8fjz/)
- **真實貼文網址**：[https://www.threads.com/@ingramchen/post/DemPbZlCfoC](https://www.threads.com/@ingramchen/post/DemPbZlCfoC)
- **發布平台 / 作者**：`Threads` / `ingramchen (@ingramchen)`
- **分析日期**：`2026-10-18`
- **當前狀態**：`🟢 已做 (Done)`

---

## 1. 核心內容摘要

### 1.1 核心主張：讓沉睡的健康存摺變成可互動的個人健康儀表板

台灣衛生福利部中央健康保險署推出的「全民健保行動快易通」App 內建「健康存摺（My Health Bank）」功能，長期記錄了民眾完整的門診就醫、西醫用藥、檢驗檢查、手術、牙醫與疫苗接種等資料。然而，多數人僅在看病時被動查閱，官方匯出的 HTML/ZIP 檔案排版原始、充滿密密麻麻的純 HTML 表格（`<table>`），缺乏時間序列圖表、異常指標預警與直觀的趨勢分析。

軟體架構專家 **Ingram Chen (@ingramchen)** 提出了極度精簡而實用的個人健康管理工作流：
1. **資料匯出**：`健保快易通` $\rightarrow$ `健康存摺` $\rightarrow$ 右上角 `下載專區` $\rightarrow$ 點擊 `匯出 HTML`。
2. **取得數據包**：系統背景產生資料後，下載完整的 `.zip` 壓縮包（內含個人歷年完整健康網頁數據）。
3. **AI 視覺化轉化**：將檔案提供給 Claude，利用 **Claude Artifact (Generative UI / 單檔互動網頁)**，將原本難以閱讀的病歷表格轉化為清晰、可篩選、具備時間軸與檢驗指數折線圖的「個人健康追蹤中樞（Personal Health Dashboard）」。

```
[健康存摺 HTML 到 Claude Artifact 視覺化流程]

  ┌──────────────────┐
  │ 健保行動快易通    │
  │ (健康存摺下載專區) │
  └─────────┬────────┘
            │ 匯出 HTML / 下載 ZIP
            ▼
  ┌────────────────────────────────────────────────────────┐
  │ 本地資料包 (歷年門診、用藥代碼、檢驗檢查數值 tables)     │
  └─────────┬──────────────────────────────────────────────┘
            │ 🛡️ 本地離線去識別化 (抹除身分證、真實姓名等 PII)
            ▼
  ┌──────────────────┐
  │ 脫敏後健康數據包  │
  └─────────┬────────┘
            │ 餵給 Claude / Antigravity + Dashboard Prompt
            ▼
  ┌────────────────────────────────────────────────────────┐
  │ 互動式 Health Artifact (Generative UI)                  │
  │  ├─ 1. 歷年檢驗指標折線趨勢 (血糖 / 膽固醇 / 肝腎安全帶)  │
  │  ├─ 2. 門診就醫與處方籤時間軸 (科別篩選 / 藥物分類)        │
  │  ├─ 3. 用藥交互作用與過敏警示 (Medication Safety Guard) │
  │  └─ 4. 下次回診「醫病溝通一頁紙」(Doctor Brief Card)    │
  └────────────────────────────────────────────────────────┘
```

---

### 1.2 數據結構與醫療維度解析

健康存摺匯出的 HTML 檔案主要包含以下核心醫療資料集：
- **檢驗檢查結果 (Lab Tests)**：包含檢驗日期、項目名稱（如空腹血糖 AC Sugar、總膽固醇 T-Cholesterol、三酸甘油酯 TG、GPT/ALT、肌酸酐 Creatinine）、檢驗值、參考正常區間（Reference Range）與異常標記（High/Low）。
- **門診就醫紀錄 (Outpatient Visits)**：包含就診日期、就診院所（醫院/診所）、科別、主診斷碼（ICD-10-CM）與處置代碼。
- **西醫用藥明細 (Medications)**：包含藥品代碼、健保代碼、藥品英文名/商品名、成分（ATC Code）、劑量、頻率（如 TID, BID, QD）、給藥天數。
- **過敏史與疫苗紀錄 (Allergies & Vaccinations)**：藥物過敏記錄、預防接種時間與疫苗批號。

---

### 1.3 隱私安全防線（極其重要的硬閘門）

健康存摺屬於**最高等級敏感個人資料（Special Category / Protected Health Information, PHI）**，內含真實姓名、身分證字號（國民身分證統一編號）、出生年月日、就診醫院與隱私病史。
- **絕對嚴禁將未脫敏的原始 ZIP/HTML 直接上傳至任何雲端模型或公開平台**！
- 必須先透過本機端離線腳本進行 **去識別化（De-identification / Pseudonymization）**：
  1. 將身分證號替換為隨機雜湊值或 `[REDACTED_ID]`。
  2. 將真實姓名替換為 `Patient_User`。
  3. 將電話、地址、緊急聯絡人資訊完全剔除。
  4. 只保留醫療必需的數值、日期、藥名與 ICD 代碼。

---

## 2. 對現有系統的啟發與落差分析

### 2.1 對應現有系統與專案

| 現有專案 / 規範 / 技能 | 關聯性與啟發重點 |
| :--- | :--- |
| **`RULE[user_global]`** (全域規範) | 「API key、token、私鑰不進程式碼... 不要漫遊碰到敏感資料」。健康存摺是典型的敏感資料，本專案提供了一個「本地端離線脫敏 $\rightarrow$ 安全傳遞給 Agent $\rightarrow$ 生成純前端 Artifact」的安全落地範式。 |
| **`generative_ui`** (內建技能) | Antigravity 內建之 `generative_ui` 技能專門用於在對話或獨立 Artifact 中渲染豐富的互動 HTML/React 組件。健康存摺儀表板是該技能在生醫與個人數據管理上的頂級展示案例。 |
| **`openfda-database` / `science`** | 藉由分析出健康存摺中的用藥 ATC 代碼，可進一步調用環境內既有的 `openfda-database` 或 `chembl-database` 技能，交叉比對用藥警示或禁忌症。 |

### 2.2 落差與採納判斷

- **值得採納的部分**：
  1. **建立標準的離線脫敏腳本**：提供一個開源安全的單檔 Python 工具，讓使用者能一鍵將健保快易通的 zip 檔轉為無個資的純淨 JSON。
  2. **設計「個人健康存摺儀表板」Generative UI 模板**：使用 Chart.js 或輕量 SVG 繪製具備「安全區間帶（Green Safe Zone）」的趨勢折線圖，一眼掌握紅字異常指標。
  3. **自動生成「回診醫病溝通一頁紙」**：彙整過去半年內的關鍵檢驗指標與目前用藥清單，讓使用者看診時能直接向醫師清晰陳述病況。
- **不適用 / 需調整的部分**：
  1. **避免 AI 進行未經核准的診斷行為**：生成的 Artifact 必須明確標註「本系統僅供數據整理與趨勢呈現，不構成任何醫療診斷或治療建議，臨床判斷請諮詢執業醫師」，防範醫療安全風險。

---

## 3. 拆解待辦事項（Action Items）

> ⚠️ 狀態變更時，請同步更新根目錄 [`README.md`](../README.md) 的總表。

- [x] **`T-20261018-13`**｜**[優先級：高]** 建立「健保快易通健康存摺離線脫敏與數據解析腳本（Health Bank Anonymizer & Parser）」
  - **目標範圍**：`SanHsien/ai-inbox` 與 本地個人健康工具鏈（`tools/health_bank_parser.py`）
  - **具體做法**：
    1. 實作單檔本地離線 Python 腳本 `tools/health_bank_parser.py`，支援讀取健保署健康存摺匯出之 HTML/ZIP 檔案。
    2. 自動辨識並抹除身分證字號、姓名、生日、電話、特定隱私欄位等個人可識別資訊（PII）。
    3. 解析並抽取檢驗檢查數據表格（日期、項目、數值、參考區間、高低異常）與門診用藥記錄，匯出為乾淨、標準化的 `health_data_clean.json`。
  - **驗收標準（DoD）**：腳本能成功解析測試 HTML，確保輸出的 JSON 中零身分證字號與個資洩漏，並輸出結構化指標數據，通過單元測試。
  - **完成日期**：2026-10-23
  - **實作紀錄 / 證據**：以 ai-inbox 範本形式交付。實作 stdlib 離線脫敏解析腳本 `tools/health_bank_parser.py`，新增純虛構測試樣本 `tests/fixtures/fake_health_bank.html`（檔頭註明虛構資料）與單元測試 `tests/test_health_bank_parser.py`。驗證涵蓋身分證、姓名、生日、病歷號、地址與電話之完全脫敏（單元測試通過）。 2026-10-24 依真實匯出格式重寫為表頭驅動解析器（主檔／明細分組、民國日期轉 ISO、健保卡就醫序號一併遮蔽、檢驗判定改由參考值比對，無法比對標「未判定」而非預設正常、Big5 編碼自動判斷、加密 ZIP 拒絕、輸出目錄位於 git repo 內預設拒絕），單元測試 38 項通過；並以維護者本機真實匯出（11 類 HTML）實測：11 類皆可解析、主檔筆數與原始非空白列逐類核對一致、輸出經身分證／電話格式掃描 0 命中。真實資料與輸出僅存於維護者本機 repo 外資料夾。

- [x] **`T-20261018-14`**｜**[優先級：中]** 設計「個人健康存摺互動式儀表板（Health Bank Dashboard Artifact）提示詞與 Generative UI 模板」
  - **目標範圍**：`generative_ui` 技能庫與健康視覺化 Prompts
  - **具體做法**：
    1. 設計一套專用 Prompt 與單檔 HTML/React 範本（利用 Tailwind CSS 與 Chart.js）。
    2. 整合健康指標趨勢視覺化（折線圖疊加綠色正常範圍區間帶）、門診科別時間軸篩選器、異常警示清單，以及就診溝通卡（Doctor Brief Card）。
    3. 納入醫療免責聲明與資料純本地運算原則（零外部上傳），相容於 Claude Artifact 與 Antigravity Generative UI。
  - **驗收標準（DoD）**：產出之單檔 HTML 在瀏覽器與 Generative UI 中能正確載入乾淨 JSON 並流暢切換指標圖表，驗證無控制台錯誤，並通過 `python tools/verify_inbox.py`。
  - **完成日期**：2026-10-23
  - **實作紀錄 / 證據**：以 ai-inbox 範本形式交付。設計標準提示詞範本 `templates/health-bank-dashboard-prompt.md` 與單檔 Generative UI 互動儀表板 `assets/health-bank-dashboard/index.html`（純虛構示範資料、Tailwind + Chart.js 4.4.1 CDN、含 Doctor Brief Card 與本地免責聲明）。
