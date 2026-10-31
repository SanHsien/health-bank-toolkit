#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
確定性虛構表格脫敏回歸測試。

【虛構資料 / SYNTHETIC FICTIONAL DATA ONLY】
本檔所有姓名、身分證字號、電話、生日、地址、病歷號、就醫序號與電子郵件均為純虛構捏造（公認假值），
不對應任何真實個人或醫療機構。測試以固定隨機種子產生大量虛構表格，斷言已知會被遮蔽的個資在輸出 JSON
中絕不以明文出現，並驗證對 338c495 之前的歷史漏遮版本具備實質擋力。
只使用 Python 標準庫。
"""

import json
import random
import subprocess
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import health_bank_parser as hbp  # noqa: E402

# 固定隨機種子，確保每次測試完全確定（deterministic）
RANDOM_SEED = 20261026

# 純虛構公認假值資料池
FAKE_NAMES = (
    "王大明", "張小美", "李志豪", "陳美華", "林小明", "黃阿花", "吳阿明", "蔡小英"
)
FAKE_IDS_UPPER = (
    "A123456789", "B298765432", "C123456789", "D223456789", "H199999999"
)
FAKE_IDS_LOWER = (
    "a123456789", "b298765432", "z198765432"
)
FAKE_PHONES = (
    "0912-345-678", "0912345678", "02-2345-6789", "02 2345 6789",
    "04-2345-6789", "049-234-5678", "+886 2 2345 6789", "+886-2-2345-6789",
    "(02) 2345-6789"
)
FAKE_DATES_ROC = (
    "民國75年08月15日", "民國80年1月1日", "民國 70 年 05 月 12 日", "民國80/01/01"
)
FAKE_DATES_YMD = (
    "1990年5月12日", "2001 年 12 月 3 日", "1985-08-15"
)
FAKE_ADDRESSES = (
    "台北市中正區忠孝東路一段999號", "台中市西區民生路888號",
    "高雄市新興區中正三路777號", "新北市板橋區縣民大道二段99號",
    "中正里5鄰"
)
FAKE_MRNS = (
    "MRN-987654321", "MRN-11223344"
)
FAKE_SEQS = (
    "SEQ0001", "SEQ0042", "SEQ9999"
)
FAKE_EMAILS = (
    "fake.patient@example.invalid", "test.user@example.com", "sample@domain.invalid"
)

SAFE_HEADERS = ("科別", "次數", "備註", "項目", "說明", "代碼")
SAFE_VALUES = ("正常", "定期追蹤", "常規檢查", "無異常", "第一綜合醫院", "內科", "健保示範診所", "門診")

PII_COLUMN_SPECS = (
    ("姓名", FAKE_NAMES),
    ("身分證字號", FAKE_IDS_UPPER),
    ("電話", FAKE_PHONES),
    ("手機", FAKE_PHONES),
    ("生日", FAKE_DATES_ROC),
    ("地址", FAKE_ADDRESSES),
    ("病歷號", FAKE_MRNS),
    ("Email", FAKE_EMAILS),
    ("EMAIL", FAKE_EMAILS),
    ("E-mail", FAKE_EMAILS),
    ("e-mail", FAKE_EMAILS),
)


def _make_table_html(rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


def _make_doc(*tables):
    return "<html><head></head><body>" + "".join(tables) + "</body></html>"


def generate_synthetic_test_cases(seed=RANDOM_SEED, count=60):
    """以固定隨機種子產生確定性虛構表格測試案例。

    回傳列表，每項為 (html, expected_masked_values, description)。
    """
    rng = random.Random(seed)
    cases = []

    # 1. 大小寫混寫與全大寫 Email 表頭欄位（針對 338c495 之前的漏洞）
    for email_hdr in ("Email", "EMAIL", "E-mail", "e-mail"):
        em = rng.choice(FAKE_EMAILS)
        rows = [
            [email_hdr, "科別", "次數", "備註"],
            [em, "內科", "3", "常規追蹤"],
        ]
        cases.append((_make_doc(_make_table_html(rows)), [em], f"Email header '{email_hdr}' masking"))

    # 2. 個資標籤欄後緊接就醫序號／日期／機構欄位（針對 338c495 之前的序列欄位脫敏降級漏洞）
    for pii_label, pool in (("姓名", FAKE_NAMES), ("身分證", FAKE_IDS_UPPER), ("電話", FAKE_PHONES)):
        seq = rng.choice(FAKE_SEQS)
        pii_val = rng.choice(pool)
        rows = [
            [pii_label, "就醫序號", "次數", "備註"],
            [pii_val, seq, "2", "無異常"],
        ]
        cases.append((_make_doc(_make_table_html(rows)), [pii_val, seq], f"Seq column after '{pii_label}'"))

    # 3. 表頭位置的 Key-Value 個資列（針對 8553280 鍵名洩漏漏洞）
    for _ in range(5):
        nm = rng.choice(FAKE_NAMES)
        bd = rng.choice(FAKE_DATES_ROC)
        rows = [
            ["姓名", nm, "生日", bd],
            ["科別", "內科", "狀態", "正常"],
        ]
        cases.append((_make_doc(_make_table_html(rows)), [nm, bd], "KV PII row in header position"))

    # 4. 自由文字與病歷報告中的電話分組、小寫身分證與西元年月日（針對 6976361, b952290）
    for _ in range(5):
        ph = rng.choice(("02-2345-6789", "02 2345 6789", "+886 2 2345 6789"))
        low_id = rng.choice(FAKE_IDS_LOWER)
        ymd = rng.choice(FAKE_DATES_YMD)
        text = f"病歷摘要：身分證 {low_id}，聯絡電話 {ph}，生於 {ymd}，檢查正常。"
        rows = [
            ["醫事機構", "就醫日期", "影像或病理報告內容"],
            ["示範醫院", "112/10/12", text],
        ]
        cases.append((_make_doc(_make_table_html(rows)), [ph, low_id, ymd], "Report text embedded PII"))

    # 5. 隨機多欄、多列通用表格（固定種子生成，混合 PII 欄位與一般欄位）
    for i in range(count):
        n_cols = rng.randint(2, 6)
        cols = []
        is_pii = []
        for _ in range(n_cols):
            if rng.random() < 0.5:
                hdr, pool = rng.choice(PII_COLUMN_SPECS)
                cols.append((hdr, pool))
                is_pii.append(True)
            else:
                hdr = rng.choice(SAFE_HEADERS)
                cols.append((hdr, SAFE_VALUES))
                is_pii.append(False)

        header_row = [c[0] for c in cols]
        n_rows = rng.randint(1, 4)
        rows = [header_row]
        masked_values = []
        for _ in range(n_rows):
            r = []
            for col_idx, (_, pool) in enumerate(cols):
                val = rng.choice(pool)
                r.append(val)
                if is_pii[col_idx]:
                    masked_values.append(val)
            rows.append(r)

        cases.append((_make_doc(_make_table_html(rows)), masked_values, f"Random generic table #{i+1}"))

    return cases


class TestDeterministicSyntheticTables(unittest.TestCase):
    """確定性虛構表格脫敏驗證。"""

    @classmethod
    def setUpClass(cls):
        cls.test_cases = generate_synthetic_test_cases(seed=RANDOM_SEED, count=60)

    def test_synthetic_tables_redact_all_known_pii_values(self):
        """斷言所有已知應被遮蔽的虛構個資在輸出 JSON 中絕不以明文出現。"""
        for html, expected_masked, desc in self.test_cases:
            with self.subTest(desc=desc):
                result = hbp.parse_health_bank_html(html)
                out = json.dumps(result, ensure_ascii=False)
                for pii in expected_masked:
                    self.assertNotIn(
                        pii,
                        out,
                        f"個資明文洩漏：在測試 '{desc}' 中發現未脫敏數值 '{pii}'",
                    )

    def test_synthetic_tables_catch_pre_338c495_regressions(self):
        """證明本測試套件在 338c495 之前的歷史版本上具備實質擋力（至少抓出一條失敗）。"""
        try:
            raw_code = subprocess.check_output(
                ["git", "-C", str(ROOT), "show", "338c495^:tools/health_bank_parser.py"],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8")
        except Exception:
            self.skipTest("本地無完整 git 歷史或無法執行 git show，略過歷史版本擋力對比")

        mod_before = types.ModuleType("hbp_before")
        exec(raw_code, mod_before.__dict__)

        failed_count = 0
        for html, expected_masked, _ in self.test_cases:
            res_before = mod_before.parse_health_bank_html(html)
            out_before = json.dumps(res_before, ensure_ascii=False)
            for pii in expected_masked:
                if pii in out_before:
                    failed_count += 1

        self.assertGreater(
            failed_count,
            0,
            "本測試套件在 338c495 之前的歷史版本上未檢測出任何失敗，未能證明具備回歸擋力",
        )


if __name__ == "__main__":
    unittest.main()
