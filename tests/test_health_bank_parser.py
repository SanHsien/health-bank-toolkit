#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health_bank_parser.py 單元測試。

【虛構資料 / SYNTHETIC FICTIONAL DATA ONLY】
本檔所有姓名、身分證字號、電話、地址、病歷號、機構、診斷、藥品與檢驗數值均為捏造，
不對應任何真實個人或醫療機構。測試用的資料表結構仿照健康存摺匯出檔的表頭與列型態
（主列／明細列／單欄分隔列），內容全部是假的。
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 讓 `tools` 套件不依賴執行目錄
from tools import health_bank_parser as hbp  # noqa: E402
from tools.health_bank_parser import (  # noqa: E402
    anonymize_text,
    decode_html_bytes,
    lab_flag,
    parse_file,
    parse_health_bank_html,
    to_iso,
)

FIXTURE = ROOT / "tests" / "fixtures" / "fake_health_bank.html"
TOOL = ROOT / "tools" / "health_bank_parser.py"

# 虛構個資：任何輸出 JSON 都不得出現
FAKE_PII = [
    "王大明",
    "A123456789",
    "0912-345-678",
    "0912345678",
    "忠孝東路",
    "MRN-987654321",
    "B223456789",
    "IC00012345",
    "IC00067890",
]

TITLE_ROW = "姓名：王大明　身分證號：A123456789　電話：0912-345-678　地址：台北市中正區忠孝東路一段999號"


def td(cells):
    return "".join(f"<td>{c}</td>" for c in cells)


def table(rows):
    return "<table>" + "".join(f"<tr>{td(r)}</tr>" for r in rows) + "</table>"


def doc(*tables, meta=None):
    head = f'<meta charset="{meta}">' if meta else ""
    return f"<html><head>{head}</head><body>{''.join(tables)}</body></html>"


def title_table(name="檢驗檢查結果"):
    return table([[f"{name}　{TITLE_ROW}"]])


NOTES_TABLE = table([["註：本表為虛構測試資料。"]])

LAB_HEAD = [
    "健保署服務單位", "醫事機構", "就醫日期/入院日期", "檢驗(查)日期", "資料上傳時間",
    "醫囑代碼", "醫囑名稱", "檢驗項目名稱", "結果值", "單位", "參考值",
]

OPD_HEAD = [
    "健保署服務單位", "醫事機構", "就醫日期", "交付調劑、檢查或復健治療日期", "健保卡就醫序號",
    "主診斷碼", "主診斷名稱", "主處置碼", "主處置名稱", "部分負擔金額", "健保支付點數",
]
OPD_SUB = ["醫囑代碼", "醫囑名稱", "醫囑總量"]

MED_HEAD = [
    "健保署服務單位", "醫事機構", "就醫(調劑)/入院日期", "出院日期", "申報起日", "申報迄日",
    "健保卡就醫序號", "疾病分類碼", "疾病分類名稱",
]
MED_SUB = ["藥品醫囑代碼", "藥品名稱", "藥品藥理分類", "給藥日數", "執行起日", "執行迄日", "醫囑總量"]

IPD_HEAD = [
    "健保署服務單位", "醫事機構", "入院日期", "出院日期", "申報起日", "申報迄日", "健保卡就醫序號",
    "疾病分類碼", "疾病分類名稱", "處置碼", "處置名稱", "部分負擔金額", "健保支付點數",
]
IPD_SUB = ["醫囑序號", "執行起日", "執行迄日", "醫囑代碼", "醫囑名稱", "醫囑數量"]


def unit(i=1):
    return f"虛構第{i}業務組"


class TestAnonymizeAndDates(unittest.TestCase):
    def test_anonymize_text_patterns(self):
        self.assertEqual(anonymize_text("身分證號: A123456789"), "身分證號: [ID_REDACTED]")
        self.assertEqual(anonymize_text("性別女: B298765432"), "性別女: [ID_REDACTED]")
        self.assertEqual(anonymize_text("手機: 0912-345-678"), "手機: [PHONE_REDACTED]")
        self.assertEqual(anonymize_text("市話: 02-23456789"), "市話: [PHONE_REDACTED]")
        self.assertEqual(anonymize_text("出生: 民國75年08月15日"), "出生: [BIRTHDATE_REDACTED]")
        self.assertEqual(anonymize_text("病歷 MRN-987654321"), "病歷 [RECORD_NO_REDACTED]")
        self.assertEqual(anonymize_text("台北市中正區忠孝東路一段999號"), "[ADDRESS_REDACTED]")

    def test_institution_names_survive_when_address_rule_is_off(self):
        name = "虛構縣示範鄉衛生所"
        self.assertEqual(anonymize_text(name, addresses=False), name)

    def test_roc_dates_to_iso(self):
        self.assertEqual(to_iso("112/10/12"), "2023-10-12")
        self.assertEqual(to_iso("99/1/5"), "2010-01-05")
        self.assertEqual(to_iso("112/10/14 09:30:00"), "2023-10-14T09:30:00")
        self.assertEqual(to_iso("2023/10/12"), "2023-10-12")
        self.assertEqual(to_iso("１１２/１０/１２"), "2023-10-12")  # 全形數字
        self.assertIsNone(to_iso("112/02/30"))
        self.assertIsNone(to_iso("112/13/01"))
        self.assertIsNone(to_iso("not a date"))
        self.assertIsNone(to_iso(""))
        self.assertIsNone(to_iso("1121012"))
        self.assertEqual(to_iso("1121012", compact_ok=True), "2023-10-12")


class TestMaskingGapsRound2(unittest.TestCase):
    """第二輪掃描找到的漏遮：每個都是「舊規則漏遮」的反例（虛構號碼）。"""

    def test_landline_with_grouped_separators_is_masked(self):
        for raw in ("02-2345-6789", "04 2345 6789", "049-234-5678", "+886 2 2345 6789", "+886-2-2345-6789"):
            with self.subTest(raw=raw):
                out = anonymize_text(f"聯絡電話：{raw}")
                self.assertEqual(out, "聯絡電話：[PHONE_REDACTED]")

    def test_grouped_separator_rule_does_not_eat_dates_or_codes(self):
        for keep in ( "AC04512345", "0.5 mg/dL", "1-2-3"):
            with self.subTest(keep=keep):
                self.assertEqual(anonymize_text(keep), keep)

    def test_gregorian_ymd_with_han_units_is_masked(self):
        self.assertEqual(anonymize_text("出生1990年5月12日"), "出生[DATE_MASKED]")
        self.assertEqual(anonymize_text("2001 年 12 月 3 日"), "[DATE_MASKED]")

    def test_lowercase_id_is_masked(self):
        self.assertEqual(anonymize_text("身分證 a123456789"), "身分證 [ID_REDACTED]")
        self.assertEqual(anonymize_text("ab123456789"), "ab123456789")  # 長英數串不是身分證


class TestLabFlag(unittest.TestCase):
    def flag(self, value, ref):
        return lab_flag(value, ref)[0]

    def test_two_sided_ranges(self):
        self.assertEqual(self.flag("4.0", "[3.5][5.5]"), "正常")
        self.assertEqual(self.flag("6.0", "[3.5][5.5]"), "偏高")
        self.assertEqual(self.flag("3.1", "[3.5][5.5]"), "偏低")
        self.assertEqual(self.flag("3.5", "[3.5][5.5]"), "正常")  # 邊界含在內
        self.assertEqual(self.flag("5.5", "[3.5][5.5]"), "正常")

    def test_one_sided_ranges(self):
        self.assertEqual(self.flag("131", "[][130]"), "偏高")
        self.assertEqual(self.flag("129", "[][130]"), "正常")
        self.assertEqual(self.flag("39", "[40][]"), "偏低")
        self.assertEqual(self.flag("41", "[40][]"), "正常")
        self.assertEqual(lab_flag("5", "[][130]")[1:], (None, 130))

    def test_never_defaults_to_normal(self):
        for value, ref in [
            ("Negative", "[Negative][]"),
            ("Negative", "[-][]"),
            ("1.2", "[-][]"),
            ("陽性", "[無][]"),
            ("Positive", "[3.5][5.5]"),
            ("<0.5", "[0][5]"),
            ("5", ""),
            ("5", "no brackets 3-9"),
            ("5", "[][]"),
            ("5", "[abc][9]"),
            ("5", "[9][3]"),  # 界線顛倒
            ("nan", "[1][2]"),
            ("5", "[nan][9]"),
            ("", "[1][2]"),
        ]:
            self.assertEqual(self.flag(value, ref), "未判定", (value, ref))

    def test_full_width_reference_brackets(self):
        self.assertEqual(self.flag("6", "［3.5］［5.5］"), "偏高")


class TestFixtureLabs(unittest.TestCase):
    def test_fixture_file(self):
        self.assertTrue(FIXTURE.exists())
        result = parse_file(FIXTURE)
        labs = result["labs"]
        self.assertEqual(len(labs), 5)
        by_item = {r["item"]: r for r in labs}
        self.assertEqual(by_item["HbA1c"]["flag"], "偏高")
        self.assertEqual(by_item["HbA1c"]["value"], 6.8)
        self.assertEqual(by_item["Glucose AC"]["flag"], "正常")
        self.assertEqual(by_item["Potassium"]["flag"], "偏低")
        self.assertEqual(by_item["Urine Protein"]["flag"], "未判定")
        self.assertEqual(by_item["Urine Protein"]["value"], "Negative")
        self.assertEqual(by_item["LDL-C"]["flag"], "偏高")
        first = labs[0]
        self.assertEqual(first["visit_date"], "112/10/12")
        self.assertEqual(first["visit_date_iso"], "2023-10-12")
        self.assertEqual(first["test_date_iso"], "2023-10-12")
        self.assertEqual(first["uploaded_at_iso"], "2023-10-14T09:30:00")
        self.assertEqual(first["reference"], "[4.0][5.6]")
        self.assertEqual((first["ref_low"], first["ref_high"]), (4.0, 5.6))
        self.assertEqual(first["institution"], "虛構示範教學醫院")
        self.assertEqual(first["order_code"], "TEST-001")

    def test_title_and_notes_tables_dropped(self):
        result = parse_file(FIXTURE)
        dumped = json.dumps(result, ensure_ascii=False)
        for secret in FAKE_PII:
            self.assertNotIn(secret, dumped)
        self.assertNotIn("註：", dumped)
        self.assertEqual(result["metadata"]["diagnostics"]["ignored_tables"], 2)
        self.assertEqual(result["generic"], [])
        self.assertNotIn("patient_info", result)

    def test_recognised_by_header_not_file_name(self):
        html = doc(table([LAB_HEAD, [unit(), "虛構醫院", "112/1/2", "112/1/2", "112/1/3 10:00", "C1", "N1", "Na", "140", "mmol/L", "[135][145]"]]))
        result = parse_health_bank_html(html, "totally-unrelated-name")
        self.assertEqual(len(result["labs"]), 1)
        self.assertEqual(result["labs"][0]["flag"], "正常")


class TestOutpatient(unittest.TestCase):
    def build(self):
        rows = [
            OPD_HEAD,
            OPD_SUB,
            [unit(), "虛構醫院甲", "112/10/12", "112/10/12", "IC00012345", "E11.9", "第二型糖尿病", "", "", "50", "320"],
            ["虛構分隔列：本次就醫共 2 筆醫囑"],
            ["", "ORD-1", "血糖檢查", "1"],
            ["1", "ORD-2", "診察費", "1"],
            ["　"],
            # 10 欄主列：缺「交付調劑…日期」
            [unit(), "虛構醫院乙", "112/11/05", "IC00067890", "I10", "高血壓", "", "", "0", "180"],
            ["", "ORD-3", "藥事服務費", "1"],
            ["尾端備註列"],
        ]
        return doc(title_table("門診資料"), table(rows), NOTES_TABLE)

    def test_master_detail_grouping(self):
        result = parse_health_bank_html(self.build(), "門診資料")
        opd = result["outpatient"]
        self.assertEqual(len(opd), 2)
        first, second = opd
        self.assertEqual(first["institution"], "虛構醫院甲")
        self.assertEqual(first["diagnosis_code"], "E11.9")
        self.assertEqual(first["diagnosis_name"], "第二型糖尿病")
        self.assertEqual(first["visit_date_iso"], "2023-10-12")
        self.assertEqual(first["copay"], 50)
        self.assertEqual(first["points"], 320)
        self.assertEqual([o["order_code"] for o in first["orders"]], ["ORD-1", "ORD-2"])
        self.assertEqual(first["orders"][0]["quantity"], 1)
        self.assertNotIn("row_alignment", first)
        # 10 欄主列：推論缺的是 service_date
        self.assertEqual(second["row_alignment"], "inferred")
        self.assertEqual(second["service_date"], "")
        self.assertEqual(second["card_seq"], "[SEQ_REDACTED]")
        self.assertEqual(second["diagnosis_name"], "高血壓")
        self.assertEqual(second["points"], 180)
        self.assertEqual([o["order_name"] for o in second["orders"]], ["藥事服務費"])
        diag = result["metadata"]["diagnostics"]
        self.assertEqual(diag["separator_rows"], 2)  # 全空白列不計
        self.assertEqual(diag["short_master_rows"], 1)
        self.assertEqual(diag["orphan_detail_rows"], 0)

    def test_quasi_identifier_and_title_redaction(self):
        result = parse_health_bank_html(self.build(), "門診資料")
        self.assertEqual(result["outpatient"][0]["card_seq"], "[SEQ_REDACTED]")
        dumped = json.dumps(result, ensure_ascii=False)
        for secret in FAKE_PII:
            self.assertNotIn(secret, dumped)
        self.assertIn("虛構醫院甲", dumped)  # 機構名稱屬於有用資料，保留

    def test_detail_rows_without_sub_header_use_default(self):
        rows = [OPD_HEAD, [unit(), "虛構醫院", "112/3/3", "112/3/3", "IC00012345", "J00", "感冒", "", "", "0", "100"], ["", "ORD-9", "診察費", "1"]]
        result = parse_health_bank_html(doc(table(rows)), "門診資料")
        self.assertEqual(len(result["outpatient"][0]["orders"]), 1)

    def test_orphan_detail_row_is_kept_and_flagged(self):
        rows = [OPD_HEAD, OPD_SUB, ["", "ORD-1", "診察費", "1"]]
        result = parse_health_bank_html(doc(table(rows)), "門診資料")
        self.assertTrue(result["outpatient"][0]["orphan"])
        self.assertEqual(result["metadata"]["diagnostics"]["orphan_detail_rows"], 1)


class TestMedications(unittest.TestCase):
    def build(self):
        full = [unit(), "虛構醫院甲", "112/10/12", "", "112/10/12", "112/10/12", "IC00012345", "E11", "糖尿病"]
        # 8 欄主列：缺「出院日期」（與 8 欄明細列同欄數，靠首格內容區分）
        short = [unit(2), "虛構醫院乙", "112/11/01", "112/11/01", "112/11/01", "IC00067890", "I10", "高血壓"]
        rows = [
            MED_HEAD,
            MED_SUB,
            full,
            ["", "A0001", "Metformin 500mg", "降血糖", "28", "112/10/12", "112/11/08", "56"],
            ["1", "A0002", "Glimepiride 2mg", "降血糖", "28", "112/10/12", "112/11/08", "28"],
            ["已列出本次所有藥品"],
            short,
            ["", "B0001", "Amlodipine 5mg", "降血壓", "30", "112/11/01", "112/11/30", "30"],
        ]
        return doc(title_table("用藥資料"), table(rows), NOTES_TABLE)

    def test_8_vs_9_col_masters(self):
        result = parse_health_bank_html(self.build(), "用藥資料")
        meds = result["medications"]
        self.assertEqual(len(meds), 2)
        first, second = meds
        self.assertEqual([d["drug_name"] for d in first["drugs"]], ["Metformin 500mg", "Glimepiride 2mg"])
        self.assertEqual(first["drugs"][0]["days"], 28)
        self.assertEqual(first["drugs"][0]["quantity"], 56)
        self.assertEqual(first["drugs"][0]["start_date_iso"], "2023-10-12")
        self.assertEqual(first["drugs"][0]["end_date_iso"], "2023-11-08")
        self.assertEqual(first["drugs"][0]["drug_class"], "降血糖")
        self.assertEqual(first["diagnosis_name"], "糖尿病")
        self.assertEqual(second["row_alignment"], "inferred")
        self.assertEqual(second["institution"], "虛構醫院乙")
        self.assertEqual(second["discharge_date"], "")
        self.assertEqual(second["claim_start_date_iso"], "2023-11-01")
        self.assertEqual(second["diagnosis_code"], "I10")
        self.assertEqual([d["drug_code"] for d in second["drugs"]], ["B0001"])
        self.assertEqual(result["metadata"]["diagnostics"]["short_master_rows"], 1)

    def test_sub_header_in_file_overrides_defaults(self):
        reordered = ["藥品名稱", "藥品醫囑代碼", "給藥日數"]
        rows = [MED_HEAD, reordered, [unit(), "虛構醫院", "112/1/1", "", "112/1/1", "112/1/1", "IC00012345", "E11", "糖尿病"], ["", "Drug X", "Z9", "7"]]
        result = parse_health_bank_html(doc(table(rows)), "用藥資料")
        drug = result["medications"][0]["drugs"][0]
        self.assertEqual((drug["drug_name"], drug["drug_code"], drug["days"]), ("Drug X", "Z9", 7))


class TestInpatientAndSurgery(unittest.TestCase):
    def test_inpatient_short_master_missing_first_cell(self):
        full = [unit(1), "虛構醫院甲", "112/5/1", "112/5/4", "112/5/1", "112/5/4", "IC00012345", "K35", "闌尾炎", "P01", "闌尾切除", "1000", "50000"]
        # 12 欄：第一格（業務組）缺漏，首格是機構名稱
        short = ["虛構醫院甲", "112/6/1", "112/6/3", "112/6/1", "112/6/3", "IC00067890", "K40", "疝氣", "P02", "疝氣修補", "800", "42000"]
        rows = [
            IPD_HEAD,
            IPD_SUB,
            full,
            ["", "1", "112/5/1", "112/5/1", "ORD-1", "病房費", "3"],
            ["", "2", "112/5/2", "112/5/2", "ORD-2", "護理費", "3"],
            ["註記單欄列"],
            short,
            ["", "1", "112/6/1", "112/6/1", "ORD-3", "病房費", "2"],
        ]
        result = parse_health_bank_html(doc(table(rows)), "住診資料")
        ipd = result["inpatient"]
        self.assertEqual(result["surgery"], [])
        self.assertEqual(len(ipd), 2)
        self.assertEqual(len(ipd[0]["orders"]), 2)
        self.assertEqual(ipd[0]["orders"][1]["order_name"], "護理費")
        self.assertEqual(ipd[0]["orders"][1]["quantity"], 3)
        self.assertEqual(ipd[0]["admission_date_iso"], "2023-05-01")
        self.assertEqual(ipd[0]["points"], 50000)
        self.assertEqual(ipd[1]["row_alignment"], "inferred")
        self.assertEqual(ipd[1]["service_unit"], "")
        self.assertEqual(ipd[1]["institution"], "虛構醫院甲")
        self.assertEqual(ipd[1]["admission_date_iso"], "2023-06-01")
        self.assertEqual(ipd[1]["procedure_name"], "疝氣修補")
        self.assertEqual(len(ipd[1]["orders"]), 1)

    def test_surgery_recognised_by_header(self):
        head = list(IPD_HEAD)
        head[2] = "就醫日期/入院日期"
        rows = [head, IPD_SUB, [unit(), "虛構醫院", "112/7/7", "112/7/8", "112/7/7", "112/7/8", "IC00012345", "S01", "擦傷", "P09", "清創", "0", "900"], ["", "1", "112/7/7", "112/7/7", "ORD-1", "清創費", "1"]]
        result = parse_health_bank_html(doc(table(rows)), "手術資料")
        self.assertEqual(result["inpatient"], [])
        self.assertEqual(len(result["surgery"]), 1)
        self.assertEqual(result["surgery"][0]["visit_date_iso"], "2023-07-07")
        self.assertEqual(len(result["surgery"][0]["orders"]), 1)


class TestOtherCategories(unittest.TestCase):
    def test_dental_vs_outpatient_by_hint(self):
        head = [
            "健保署服務單位", "醫事機構", "就醫日期", "健保卡就醫序號", "主診斷碼", "主診斷名稱",
            "主處置碼", "主處置名稱", "部分負擔金額", "健保支付點數", "虛構附加長表頭欄位",
        ]
        rows = [head, [unit(), "虛構牙醫診所", "113/2/2", "IC00012345", "K02", "齲齒", "89001C", "牙齒填補", "50", "400", "備註甲"]]
        result = parse_health_bank_html(doc(table(rows)), "牙醫門診資料")
        self.assertEqual(result["outpatient"], [])
        dental = result["dental"][0]
        self.assertEqual(dental["diagnosis_name"], "齲齒")
        self.assertEqual(dental["card_seq"], "[SEQ_REDACTED]")
        self.assertEqual(dental["extra"]["虛構附加長表頭欄位"], "備註甲")
        self.assertEqual(dental["visit_date_iso"], "2024-02-02")

    def test_vaccinations(self):
        rows = [["接種日期", "疫苗中文名稱", "醫事機構名稱"], ["110/9/1", "虛構流感疫苗", "虛構診所"]]
        result = parse_health_bank_html(doc(table(rows)), "預防接種資料")
        v = result["vaccinations"][0]
        self.assertEqual((v["vaccine_name"], v["institution"], v["vaccination_date_iso"]), ("虛構流感疫苗", "虛構診所", "2021-09-01"))

    def test_imaging_reports_are_anonymised_free_text(self):
        report = f"胸部 X 光：無明顯異常。<br>病患聯絡電話 0912345678，證號 B223456789，住 台北市中正區忠孝東路一段999號。"
        rows = [["影像或病理報告內容"], [report]]
        result = parse_health_bank_html(doc(title_table("影像或病理檢查資料"), table(rows)), "影像或病理檢查資料")
        self.assertEqual(len(result["imaging_reports"]), 1)
        text = result["imaging_reports"][0]["report"]
        self.assertIn("無明顯異常", text)
        self.assertIn("\n", text)  # <br> 換行保留
        dumped = json.dumps(result, ensure_ascii=False)
        for secret in ("0912345678", "B223456789", "忠孝東路"):
            self.assertNotIn(secret, dumped)

    def test_directives_and_generic_tables(self):
        directives = table([["健保卡註記日期", "註記項目"], ["112/4/4", "虛構意願註記"]])
        generic = table([
            ["檢測日期", "檢測結果", "姓名"],
            ["112/5/5", "陰性", "王大明"],
            ["112/5/6", "陰性 A123456789", "王大明"],
        ])
        kv = table([["姓名", "王大明"], ["身分證號", "A123456789"]])
        result = parse_health_bank_html(doc(kv, directives, generic), "糖尿病追蹤")
        self.assertEqual(result["directives"][0]["card_note_date_iso"], "2023-04-04")
        g = result["generic"]
        self.assertEqual(len(g), 1)  # key-value 個人資料表被丟棄，沒進 generic
        self.assertEqual(g[0]["source"], "糖尿病追蹤")
        self.assertEqual(g[0]["rows"][0]["檢測日期_iso"], "2023-05-05")
        self.assertEqual(g[0]["rows"][0]["姓名"], "[REDACTED]")
        self.assertIn("[ID_REDACTED]", g[0]["rows"][1]["檢測結果"])
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("王大明", dumped)
        self.assertNotIn("A123456789", dumped)

    def test_anonymize_catches_pii_after_cjk_fullwidth_and_labelled_ids(self):
        # 漢字緊接西元生日（舊的 \b 在這裡不成立，會漏遮）
        self.assertEqual(anonymize_text("出生1990-05-12就醫"), "出生[DATE_MASKED]就醫")
        # 全形英數身分證
        self.assertEqual(anonymize_text("Ａ１２３４５６７８９"), "[ID_REDACTED]")
        # 標籤後的病歷號
        self.assertEqual(anonymize_text("病歷號：12345678"), "病歷號：[RECORD_NO_REDACTED]")
        self.assertEqual(anonymize_text("病歷編號 AB-12"), "病歷編號 [RECORD_NO_REDACTED]")
        # 括號區碼、+886 手機、民國斜線生日
        self.assertEqual(anonymize_text("(02)2345-6789"), "[PHONE_REDACTED]")
        self.assertEqual(anonymize_text("+886-912-345-678"), "[PHONE_REDACTED]")
        self.assertEqual(anonymize_text("民國70/05/12"), "[BIRTHDATE_REDACTED]")
        # 不該誤殺：沒有 0 開頭的 9 位數字、一般檢驗數值
        self.assertEqual(anonymize_text("號碼9123456789"), "號碼9123456789")
        self.assertEqual(anonymize_text("血糖 5.6 mg/dL"), "血糖 5.6 mg/dL")

    def test_roc_year_boundaries_leap_and_empty(self):
        self.assertEqual(to_iso("99/12/31"), "2010-12-31")   # 民國 99 → 100 年交界
        self.assertEqual(to_iso("100/1/1"), "2011-01-01")
        self.assertEqual(to_iso("113/2/29"), "2024-02-29")   # 閏年
        self.assertIsNone(to_iso("112/2/29"))                # 非閏年
        self.assertIsNone(to_iso("100/2/29"))                # 2011 非閏年
        self.assertEqual(to_iso("89/2/29"), "2000-02-29")    # 2000 是閏年（世紀閏年）
        self.assertEqual(to_iso("1131231", compact_ok=True), "2024-12-31")
        self.assertIsNone(to_iso("1000229", compact_ok=True))
        self.assertIsNone(to_iso("112/10/12 24:00"))
        for empty in (None, "", "   "):
            self.assertIsNone(to_iso(empty))

    def test_key_value_personal_table_with_four_columns_is_dropped(self):
        # 四欄 key-value 個資表（標籤｜值｜標籤｜值）：舊版會把「值」當成欄名輸出
        kv = table([
            ["姓名", "王大明", "身分證字號", "A123456789"],
            ["出生日期", "70/05/12", "電話", "0912345678"],
        ])
        result = parse_health_bank_html(doc(kv))
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["generic"], [])
        for secret in ("王大明", "A123456789", "70/05/12", "0912345678"):
            self.assertNotIn(secret, dumped)

    def test_label_words_inside_normal_cells_do_not_drop_table(self):
        # 「出生」「通訊」出現在一般內容裡，不是 key-value 個資表
        for row in (["B型肝炎疫苗", "113/01/02", "出生24小時內接種"],
                    ["門診", "113/01/02", "通訊診療"]):
            normal = table([["項目", "日期", "備註"], row])
            result = parse_health_bank_html(doc(normal))
            self.assertEqual(len(result["generic"]), 1, row)

    def test_overflow_cells_in_table_with_pii_column_are_fully_masked(self):
        # 表格有「姓名」欄時，多出來的格子可能就是姓名（沒有標籤，文字遮罩抓不到）
        # 解析器以前 4 列中最寬的列為表頭，溢位列要放在第 5 列之後才會走到溢位處理
        rows = [["姓名", "項目", "日期"]] + [["某甲", "掛號", "113/01/02"]] * 3 + [["王大明", "掛號", "113/01/02", "陳小華"]]
        result = parse_health_bank_html(doc(table(rows)))
        self.assertEqual(len(result["generic"][0]["rows"]), 4)  # 確認有解析，不是整張略過
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("王大明", dumped)
        self.assertNotIn("陳小華", dumped)

    def test_overflow_cells_without_pii_column_keep_medical_values(self):
        rows = [["項目", "日期", "備註"]] + [["掛號", "113/01/02", "-"]] * 3 + [["HbA1c", "113/01/02", "6.8", "空腹"]]
        result = parse_health_bank_html(doc(table(rows)))
        self.assertEqual(result["generic"][0]["rows"][-1]["_overflow"], ["空腹"])

    def test_resident_certificate_number_needs_label(self):
        self.assertEqual(anonymize_text("居留證號：AB12345678"), "居留證號：[ID_REDACTED]")
        self.assertEqual(anonymize_text("統一證號 AB12345678"), "統一證號 [ID_REDACTED]")
        self.assertEqual(anonymize_text("AC04512345"), "AC04512345")  # 醫令碼不受影響

    def test_drug_codes_are_not_mistaken_for_phone_numbers(self):
        self.assertEqual(anonymize_text("AC04512345"), "AC04512345")
        self.assertEqual(anonymize_text("BC0123456789X"), "BC0123456789X")
        self.assertEqual(anonymize_text("電話 02-23456789"), "電話 [PHONE_REDACTED]")

    def test_nested_and_unclosed_markup(self):
        html = (
            "<table><tr><td>" + table([["接種日期", "疫苗中文名稱", "醫事機構名稱"], ["111/1/1", "疫苗甲", "診所乙"]])
            + "</td></tr></table>"
            "<table><tr><td>a<td>b<tr><td>c<td>d</table>"
        )
        result = parse_health_bank_html(html, "預防接種資料")
        self.assertEqual(len(result["vaccinations"]), 1)
        # 缺 </td></tr> 的第二張表：以第一列為表頭、其餘為資料列，進 generic
        self.assertEqual(result["generic"][0]["headers"], ["a", "b"])
        self.assertEqual(result["generic"][0]["rows"], [{"a": "c", "b": "d"}])


OPD_SEC_DX = ["次診斷碼1", "次診斷名稱1", "次診斷碼2", "次診斷名稱2", "次診斷碼3", "次診斷名稱3", "次診斷碼4", "次診斷名稱4", "次診斷碼5", "次診斷名稱5"]
OPD_SEC_PROC = ["次處置碼1", "次處置名稱1", "次處置碼2", "次處置名稱2", "", "", "", "", "", ""]


def blank(n):
    return [""] * n


class TestRealExportLayout(unittest.TestCase):
    """真實匯出的版面：主檔表頭第一格跨列，下面接次診斷／次處置子表頭；每筆主檔後面固定跟著
    同數量的次要列（沒資料時整列空白）。parser v2 把次要列當成新的一次就醫（2026-10-24 用真實匯出查到）。"""

    def opd_doc(self):
        rows = [
            OPD_HEAD, OPD_SEC_DX, OPD_SEC_PROC, OPD_SUB,
            [unit(), "虛構醫院甲", "112/10/12", "", "IC00012345", "E11.9", "第二型糖尿病", "", "", "50", "320"],
            ["", "I10", "本態性高血壓", "E78.5", "高血脂症", "", "", "", "", "", ""],  # 次診斷列
            blank(11),                                                                  # 次處置列（空）
            ["", "ORD-1", "血糖檢查", "1"],
            blank(11),                                                                  # 純空白間隔列
            [unit(), "虛構醫院乙", "112/11/05", "", "IC00067890", "J06.9", "急性上呼吸道感染", "", "", "0", "180"],
            blank(11),                                                                  # 次診斷列（空）
            ["", "P01", "虛構處置", "", "", "", "", "", "", "", ""],                       # 次處置列
            ["", "ORD-2", "診察費", "1"],
            # 就醫日期空白、只有交付調劑日期的主檔是真的就醫紀錄
            [unit(), "虛構醫院丙", "", "112/12/01", "IC00011111", "H04.1", "乾眼症", "", "", "0", "90"],
            blank(11), blank(11),
            ["", "ORD-3", "藥事服務費", "1"],
        ]
        return doc(title_table("門診資料"), table(rows), NOTES_TABLE)

    def test_secondary_rows_merge_into_their_visit(self):
        result = parse_health_bank_html(self.opd_doc(), "門診資料")
        opd = result["outpatient"]
        self.assertEqual(len(opd), 3)
        first, second, third = opd
        self.assertEqual(first["secondary_diagnoses"], [
            {"code": "I10", "name": "本態性高血壓"}, {"code": "E78.5", "name": "高血脂症"}])
        self.assertNotIn("secondary_procedures", first)
        self.assertNotIn("secondary_diagnoses", second)
        self.assertEqual(second["secondary_procedures"], [{"code": "P01", "name": "虛構處置"}])
        self.assertEqual([o["order_code"] for o in first["orders"]], ["ORD-1"])
        self.assertEqual([o["order_code"] for o in second["orders"]], ["ORD-2"])
        self.assertIsNone(third["visit_date_iso"])
        self.assertEqual(third["service_date_iso"], "2023-12-01")
        diag = result["metadata"]["diagnostics"]
        self.assertEqual(diag["short_master_rows"], 0)  # 子表頭不再被當成欄位短少的主檔
        self.assertEqual(diag["secondary_rows"], 2)
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("次診斷碼1", dumped)

    def test_inpatient_multi_row_secondary_layout(self):
        dx1 = [f"次診斷{p}{i}" for i in range(1, 7) for p in ("碼", "名稱")]
        dx2 = [f"次診斷{p}{i}" for i in range(7, 13) for p in ("碼", "名稱")]
        proc = ["次處置碼1", "次處置名稱1"] + [""] * 10
        master = [unit(), "虛構醫院甲", "112/5/1", "112/5/4", "112/5/1", "112/5/4", "IC00012345", "K80.3", "膽管結石", "P05", "取石術", "1000", "50000"]
        rows = [
            IPD_HEAD, dx1, dx2, proc, IPD_SUB,
            master,
            ["", "E11.9", "糖尿病"] + [""] * 10,
            ["", "Z85.6", "白血病個人史"] + [""] * 10,  # 第二列次診斷 → 第 7 個次診斷
            ["", "0F9", "虛構引流"] + [""] * 10,
            ["", "1", "112/5/1", "112/5/1", "ORD-1", "病房費", "3"],
        ]
        result = parse_health_bank_html(doc(table(rows)), "住診資料")
        ipd = result["inpatient"]
        self.assertEqual(len(ipd), 1)
        self.assertEqual([d["code"] for d in ipd[0]["secondary_diagnoses"]], ["E11.9", "Z85.6"])
        self.assertEqual(ipd[0]["secondary_procedures"], [{"code": "0F9", "name": "虛構引流"}])
        self.assertEqual(len(ipd[0]["orders"]), 1)

    def test_unexpected_secondary_row_is_not_a_new_visit(self):
        rows = [
            OPD_HEAD, OPD_SEC_DX, OPD_SUB,
            [unit(), "虛構醫院甲", "112/10/12", "", "IC00012345", "E11.9", "糖尿病", "", "", "50", "320"],
            ["", "I10", "高血壓", "", "", "", "", "", "", "", ""],
            ["", "ORD-1", "血糖檢查", "1"],
            ["", "Z99", "多出來的次要列", "", "", "", "", "", "", "", ""],
        ]
        result = parse_health_bank_html(doc(table(rows)), "門診資料")
        self.assertEqual(len(result["outpatient"]), 1)
        self.assertEqual(result["metadata"]["diagnostics"]["unmatched_rows"], 1)

    def test_surgery_detail_header(self):
        head = list(IPD_HEAD)
        head[2] = "就醫日期/入院日期"
        sub = ["手術日期", "醫囑代碼", "醫囑名稱", "診療部位", "醫囑數量"]
        rows = [head, sub,
                [unit(), "虛構醫院", "112/7/7", "", "", "", "IC00012345", "D23.9", "皮膚良性腫瘤", "0HB", "切除術", "260", "9910"],
                ["", "", "48001C", "淺部創傷處理", "", "1"],
                ["", "112/7/7", "62010C", "皮膚腫瘤切除", "右手", "1"]]
        result = parse_health_bank_html(doc(table(rows)), "手術資料")
        orders = result["surgery"][0]["orders"]
        self.assertEqual([o["order_code"] for o in orders], ["48001C", "62010C"])
        self.assertEqual(orders[1]["order_name"], "皮膚腫瘤切除")
        self.assertEqual(orders[1]["body_site"], "右手")
        self.assertEqual(orders[1]["surgery_date_iso"], "2023-07-07")

    def test_dental_secondary_rows_and_tooth_orders(self):
        head = ["健保署服務單位", "醫事機構", "就醫日期", "健保卡就醫序號", "主診斷碼", "主診斷名稱",
                "主處置碼", "主處置名稱", "部分負擔金額", "健保支付點數", ""]
        sub = ["醫囑代碼", "醫囑名稱", "牙位代碼", "牙位名稱", "醫囑總量"]
        rows = [head, OPD_SEC_DX, OPD_SEC_PROC, sub,
                [unit(), "虛構牙醫診所", "114/3/28", "IC00012345", "K05.4", "牙周病", "", "", "50", "1032", ""],
                blank(11), blank(11),
                ["", "91089C", "牙結石清除-全口", "FM", "全口", "1"]]
        result = parse_health_bank_html(doc(table(rows)), "牙醫門診資料")
        self.assertEqual(len(result["dental"]), 1)
        order = result["dental"][0]["orders"][0]
        self.assertEqual((order["order_code"], order["tooth_name"], order["quantity"]), ("91089C", "全口", 1))

    def test_imaging_two_row_header_pairs_and_dedupes(self):
        info = ["健保署服務單位", "醫事機構", "就醫日期/入院日期", "檢驗(查)日期", "資料上傳時間", "醫囑代碼", "醫囑名稱"]
        meta = [unit(), "虛構醫院", "113/01/26", "113/01/26", "113/01/28", "32001C", "胸腔檢查"]
        again = meta[:4] + ["113/02/16"] + meta[5:]
        rows = [info, ["影像或病理報告內容"],
                meta, ["Chest film: f/u nodules.<br>No active lesion."],
                again, ["Chest film: f／u nodules. No active lesion."],  # 重複上傳，空白與斜線不同
                meta[:5] + ["18001C", "心電圖"], ["Sinus rhythm"]]
        result = parse_health_bank_html(doc(table(rows)), "影像或病理檢查資料")
        reps = result["imaging_reports"]
        self.assertEqual(len(reps), 2)
        self.assertEqual((reps[0]["institution"], reps[0]["test_date_iso"], reps[0]["order_code"]),
                         ("虛構醫院", "2024-01-26", "32001C"))
        self.assertIn("No active lesion", reps[0]["report"])
        self.assertEqual(reps[1]["order_name"], "心電圖")
        self.assertEqual(result["metadata"]["diagnostics"]["duplicate_rows"], 1)

    def test_lab_reuploads_are_deduped_and_zero_reference_is_undetermined(self):
        row = [unit(), "虛構醫院", "115/3/24", "115/3/24", "115/3/25", "08026C", "凝血", "PT Control", "10.1", "sec", "[0.000][0.000]"]
        reupload = row[:4] + ["115/4/22"] + row[5:]
        hb = [unit(), "虛構醫院", "115/3/24", "115/3/24", "115/3/25", "09006C", "HbA1c", "HbA1c", "8.0", "%", "[4.0][5.6]"]
        result = parse_health_bank_html(doc(table([LAB_HEAD, row, reupload, hb])), "檢驗檢查結果")
        labs = result["labs"]
        self.assertEqual(len(labs), 2)
        self.assertEqual(labs[0]["flag"], "未判定")
        self.assertEqual(labs[1]["flag"], "偏高")
        self.assertEqual(result["metadata"]["diagnostics"]["duplicate_rows"], 1)

    def test_vaccinations_listed_in_two_files_count_once(self):
        vac = table([["接種日期", "疫苗中文名稱", "醫事機構名稱"], ["110/9/18", "虛構 COVID-19 疫苗", "虛構診所"]])
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "預防接種資料_1151023.html").write_text(doc(vac), encoding="utf-8")
            Path(tmp, "COVID-19疫苗接種／病毒檢測結果_1151023.html".replace("／", "／")).write_text(doc(vac), encoding="utf-8")
            result = hbp.parse_path(tmp)
        self.assertEqual(len(result["vaccinations"]), 1)
        self.assertEqual(result["metadata"]["diagnostics"]["duplicate_rows"], 1)


class TestEncoding(unittest.TestCase):
    def lab_doc(self, meta=None):
        return doc(
            table([LAB_HEAD, [unit(), "虛構示範教學醫院", "112/1/2", "112/1/2", "112/1/3 10:00", "C1", "N1", "鈉離子", "140", "mmol/L", "[135][145]"]]),
            meta=meta,
        )

    def test_big5_with_and_without_meta(self):
        for meta in ("big5", "Big5", None):
            data = self.lab_doc(meta).encode("cp950")
            with self.assertRaises(UnicodeDecodeError):
                data.decode("utf-8")
            text, enc = decode_html_bytes(data)
            self.assertEqual(enc, "cp950")
            result = parse_health_bank_html(text, "檢驗檢查結果")
            self.assertEqual(result["labs"][0]["item"], "鈉離子")
            self.assertEqual(result["labs"][0]["institution"], "虛構示範教學醫院")

    def test_utf8_and_bom(self):
        data = self.lab_doc("utf-8").encode("utf-8")
        self.assertEqual(decode_html_bytes(data)[1], "utf-8")
        text, enc = decode_html_bytes(b"\xef\xbb\xbf" + data)
        self.assertEqual(enc, "utf-8-sig")
        self.assertNotIn("﻿", text)

    def test_double_encoded_mojibake_is_repaired(self):
        good = self.lab_doc("utf-8")
        garbled = good.encode("utf-8").decode("latin-1").encode("utf-8")
        text, enc = decode_html_bytes(garbled)
        self.assertEqual(enc, "utf-8+repaired")
        self.assertEqual(text, good)

    def test_undecodable_bytes_are_flagged_not_silently_replaced(self):
        text, enc = decode_html_bytes(b"<p>\xff\xff\xff\xff</p>")
        self.assertTrue(enc.endswith("-replace"))

    def test_big5_file_through_parse_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "檢驗檢查結果_1151023.html"
            p.write_bytes(self.lab_doc("big5").encode("cp950"))
            result = parse_file(p)
            self.assertEqual(result["metadata"]["files"][0]["encoding"], "cp950")
            self.assertEqual(result["metadata"]["files"][0]["label"], "檢驗檢查結果")
            self.assertEqual(len(result["labs"]), 1)


class TestInputs(unittest.TestCase):
    def make_files(self, base: Path):
        (base / "檢驗檢查結果_1151023.html").write_text(
            doc(title_table(), table([LAB_HEAD, [unit(), "虛構醫院", "112/1/2", "112/1/2", "112/1/3 10:00", "C1", "N1", "Na", "150", "mmol/L", "[135][145]"]]), NOTES_TABLE),
            encoding="utf-8",
        )
        (base / "預防接種資料_1151023.html").write_text(
            doc(table([["接種日期", "疫苗中文名稱", "醫事機構名稱"], ["110/9/1", "虛構流感疫苗", "虛構診所"]])),
            encoding="utf-8",
        )
        (base / "readme.txt").write_text("ignored", encoding="utf-8")

    def test_folder_merges_every_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_files(Path(tmp))
            result = parse_file(tmp)
            self.assertEqual(len(result["labs"]), 1)
            self.assertEqual(result["labs"][0]["flag"], "偏高")
            self.assertEqual(len(result["vaccinations"]), 1)
            self.assertEqual(len(result["metadata"]["files"]), 2)

    def test_zip_parses_every_html_not_just_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            self.make_files(src)
            zpath = Path(tmp) / "export.zip"
            with zipfile.ZipFile(zpath, "w") as zf:
                for p in sorted(src.iterdir()):
                    zf.write(p, p.name)
                zf.writestr("__MACOSX/._junk.html", "junk")
            result = parse_file(zpath)
            self.assertEqual(len(result["labs"]), 1)
            self.assertEqual(len(result["vaccinations"]), 1)

    def encrypted_zip(self, path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.html", "<html></html>")
        data = bytearray(buf.getvalue())
        local = data.find(b"PK\x03\x04")
        central = data.find(b"PK\x01\x02")
        data[local + 6] |= 0x01  # general purpose bit 0 = encrypted
        data[central + 8] |= 0x01
        path.write_bytes(bytes(data))

    def test_encrypted_zip_is_refused_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "enc.zip"
            self.encrypted_zip(z)
            with self.assertRaises(hbp.EncryptedArchiveError) as cm:
                parse_file(z)
            self.assertIn("加密", str(cm.exception))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = hbp.main([str(z), str(Path(tmp) / "out")])
            self.assertEqual(rc, 2)
            self.assertIn("加密", err.getvalue())
            self.assertFalse((Path(tmp) / "out").exists())

    def test_bad_zip_and_empty_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.zip"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                parse_file(bad)
            empty = Path(tmp) / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                parse_file(empty)
            with self.assertRaises(FileNotFoundError):
                parse_file(Path(tmp) / "missing.html")


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "src"
        self.src.mkdir()
        TestInputs().make_files(self.src)
        (self.src / "門診資料_1151023.html").write_text(TestOutpatient().build(), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hbp.main([str(a) for a in args])
        return rc, out.getvalue(), err.getvalue()

    def test_stale_category_output_from_previous_run_is_removed(self):
        out_dir = self.tmp / "out"
        out_dir.mkdir()
        (out_dir / "medications.json").write_text("[\"上一輪的舊資料\"]", encoding="utf-8")
        (out_dir / "my-notes.txt").write_text("使用者自己的檔案", encoding="utf-8")
        rc, stdout, _ = self.run_main(self.src, out_dir)
        self.assertEqual(rc, 0, stdout)
        self.assertFalse((out_dir / "medications.json").exists())  # 本次沒有用藥資料
        self.assertTrue((out_dir / "my-notes.txt").exists())  # 不是本工具的檔案，不刪

    def test_writes_one_json_per_category_and_summary(self):
        out_dir = self.tmp / "out"
        rc, stdout, _ = self.run_main(self.src, out_dir)
        self.assertEqual(rc, 0, stdout)
        names = sorted(p.name for p in out_dir.iterdir())
        self.assertEqual(names, ["labs.json", "outpatient.json", "summary.json", "vaccinations.json"])
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["counts"]["labs"], 1)
        self.assertEqual(summary["counts"]["outpatient"], 2)
        self.assertEqual(summary["counts"]["medications"], 0)
        self.assertEqual(summary["detail_counts"]["outpatient_orders"], 3)
        self.assertEqual(summary["date_ranges"]["outpatient"], {"min": "2023-10-12", "max": "2023-11-05"})
        self.assertEqual(summary["date_ranges"]["labs"], {"min": "2023-01-02", "max": "2023-01-02"})
        self.assertEqual(summary["date_ranges"]["vaccinations"], {"min": "2021-09-01", "max": "2021-09-01"})
        self.assertTrue(summary["anonymized"])
        everything = "".join(p.read_text(encoding="utf-8") for p in out_dir.iterdir())
        for secret in FAKE_PII:
            self.assertNotIn(secret, everything)
        outpatient = json.loads((out_dir / "outpatient.json").read_text(encoding="utf-8"))
        self.assertEqual(len(outpatient[0]["orders"]), 2)

    def test_refuses_out_dir_inside_git_working_tree(self):
        repo = self.tmp / "fake_repo"
        (repo / ".git").mkdir(parents=True)
        out_dir = repo / "sub" / "out"
        rc, _, err = self.run_main(self.src, out_dir)
        self.assertEqual(rc, 2)
        self.assertIn("git", err)
        self.assertFalse(out_dir.exists())
        self.assertFalse((repo / "sub").exists())

    def test_git_worktree_file_marker_also_counts(self):
        repo = self.tmp / "worktree"
        repo.mkdir()
        (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
        rc, _, _ = self.run_main(self.src, repo / "out")
        self.assertEqual(rc, 2)

    def test_allow_in_repo_flag(self):
        repo = self.tmp / "fake_repo"
        (repo / ".git").mkdir(parents=True)
        out_dir = repo / "out"
        rc, _, _ = self.run_main(self.src, out_dir, "--allow-in-repo")
        self.assertEqual(rc, 0)
        self.assertTrue((out_dir / "summary.json").exists())

    def test_out_dir_that_is_a_file_is_rejected(self):
        f = self.tmp / "out.json"
        f.write_text("x", encoding="utf-8")
        rc, _, err = self.run_main(self.src, f)
        self.assertEqual(rc, 2)
        self.assertIn("資料夾", err)

    def test_real_subprocess_exit_codes(self):
        out_dir = self.tmp / "sub_out"
        ok = subprocess.run(
            [sys.executable, "-I", str(TOOL), str(self.src), str(out_dir)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue((out_dir / "summary.json").exists())
        missing = subprocess.run(
            [sys.executable, "-I", str(TOOL), str(self.tmp / "nope.html"), str(self.tmp / "o2")],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
