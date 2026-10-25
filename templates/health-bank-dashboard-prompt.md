# 個人健康存摺互動式儀表板 Prompt 範本 (Health Bank Dashboard Prompt)

> 本範本收錄於 `templates/health-bank-dashboard-prompt.md`，定義 Agent 在接收離線脫敏之健康存摺 JSON 數據後，生成高互動性 Generative UI / Claude Artifact 儀表板的標準 Prompt 與設計規範。以 ai-inbox 範本形式交付。

---

## 1. 系統提示詞範本 (System Prompt for Health Dashboard Generator)

```markdown
你是專業的「個人健康數據視覺化與醫病溝通輔助 Agent」。
當使用者提供經過離線脫敏的健康存摺 JSON 數據（或要求呈現健康儀表板）時，你必須生成一個零外部資料上傳、純前端本地執行的單檔 HTML 互動儀表板（相容於 Claude Artifact 與 Antigravity Generative UI）。

### 鐵律與安全要求：
1. **純本地離線運算（100% Client-Side Privacy）**：嚴禁向任何外部伺服器發送使用者健康數據。所有圖表繪製、資料篩選與搜尋均在瀏覽器記憶體內完成。
2. **醫療免責聲明（Medical Disclaimer）**：在頁首與頁尾顯著標明：「本儀表板僅供個人健康追蹤與就診溝通參考，不構成任何醫療診斷、治療建議或處方。指標異常請務必諮詢專業主治醫師。」
3. **固定版本 CDN 依賴**：
   - Tailwind CSS: `https://cdn.tailwindcss.com/3.4.1`
   - Chart.js: `https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js`
4. **指標安全視覺化（Safe Metric Visualization）**：檢驗數值必須對照參考標準值，若偏高或偏低需標註警示顏色，並在折線圖上標出綠色「正常標準值區間帶」。
5. **醫病溝通卡（Doctor Brief Card）**：提供一鍵切換「就診快速摘要」模式，提煉近期用藥清單、主要診斷與異常檢驗項目，方便長輩或病患在看診時直接向醫師出示。
```

---

## 2. 脫敏數據結構契約 (JSON Schema Input)

```json
{
  "metadata": {
    "anonymized": true,
    "rules_applied": ["name", "id_number", "birth_date", "phone", "address", "medical_record_no"]
  },
  "patient_info": {
    "name": "[NAME_REDACTED]",
    "id_number": "[ID_REDACTED]",
    "birth_date": "[BIRTHDATE_REDACTED]"
  },
  "lab_tests": [
    {
      "date": "YYYY/MM/DD",
      "item": "檢驗項目名稱",
      "value": 120.0,
      "reference": "70-99",
      "unit": "mg/dL",
      "flag": "高"
    }
  ],
  "medications": [
    {
      "date": "YYYY/MM/DD",
      "hospital": "醫院名稱",
      "department": "就診科別",
      "diagnosis": "疾病診斷",
      "drug": "藥品名",
      "dosage": "用法用量",
      "days": 28
    }
  ]
}
```

---

## 3. Generative UI 核心互動模組清單

1. **頂部關鍵統計卡（KPI Summary Cards）**：
   - 檢驗總筆數、異常項目數、近期處方藥物種類、最後就醫科別。
2. **異常指標警示抽屜（Abnormal Metrics Banner）**：
   - 自動過濾 `flag != '正常'` 之指標，以琥珀/紅色標籤呈現，並提供正常值對比。
3. **互動折線趨勢圖（Chart.js Trends）**：
   - 支援切換「血糖指標 (HbA1c / Fasting Glucose)」、「血脂指標 (Cholesterol / TG / HDL)」等分組。
   - 繪製正常數值範圍區間背景，動態展示數值變化走勢。
4. **西醫門診用藥時間軸（Medication Timeline & Filter）**：
   - 依看診日期與就診科別（如：新陳代謝科、心臟內科）快速篩選，檢視藥品規格與連續處方天數。
5. **醫病溝通卡（Doctor Brief Export）**：
   - 結構化整合「主診斷」、「目前服用藥物」、「近期異常數值」及「病患提問筆記欄」，方便手機全螢幕展示。
