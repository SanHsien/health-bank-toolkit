"""2026-11-21 覆核找到的漏洞回歸測試（見 REVIEW.md）。

所有值都是虛構的格式樣本，不對應任何真實個人。
"""

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import health_bank_parser as hbp  # noqa: E402
from tools.health_bank_parser import anonymize_text, parse_health_bank_html  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "fake_health_bank.html"


def run_main(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = hbp.main([str(a) for a in args])
    return rc, out.getvalue(), err.getvalue()


class TestLabelledFreeTextPii(unittest.TestCase):
    """自由文字（影像報告、generic 表、對不上欄位的格子）裡帶標籤的個資。"""

    def test_labelled_roc_birth_date_without_minguo_prefix_is_masked(self):
        for text, secret in (
            ("出生日期：85/05/12", "85/05/12"),
            ("生日 085.05.12", "085.05.12"),
            ("出生年月日:85-5-12", "85-5-12"),
            ("生日：0850512", "0850512"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(secret, anonymize_text(text))

    def test_exam_dates_without_birth_label_are_kept(self):
        self.assertEqual(anonymize_text("檢查日期 112/10/12"), "檢查日期 112/10/12")

    def test_labelled_address_without_district_is_masked(self):
        for text, secret in (
            ("地址：台北市忠孝東路四段100號", "忠孝東路"),
            ("通訊地址:高雄市中山二路5號3樓", "中山二路"),
            ("住址 臺南市府前路一段1號", "府前路"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(secret, anonymize_text(text))

    def test_labelled_id_with_separators_is_masked(self):
        for text in ("身分證：A 123 456 789", "身分證字號:A-123-456-789"):
            with self.subTest(text=text):
                self.assertNotIn("456", anonymize_text(text))

    def test_mobile_with_dot_separators_is_masked(self):
        self.assertNotIn("345", anonymize_text("聯絡 0912.345.678"))

    def test_lab_reference_brackets_do_not_bypass_masking(self):
        html = (
            "<table><tr><th>醫事機構</th><th>檢驗(查)日期</th><th>檢驗項目名稱</th>"
            "<th>結果值</th><th>單位</th><th>參考值</th></tr>"
            "<tr><td>虛構醫院</td><td>112/10/12</td><td>HbA1c</td><td>6.8</td><td>%</td>"
            "<td>[A123456789][0912345678]</td></tr></table>"
        )
        ref = parse_health_bank_html(html)["labs"][0]["reference"]
        self.assertNotIn("A123456789", ref)
        self.assertNotIn("0912345678", ref)

    def test_plain_numeric_reference_stays_verbatim(self):
        html = (
            "<table><tr><th>醫事機構</th><th>檢驗(查)日期</th><th>檢驗項目名稱</th>"
            "<th>結果值</th><th>單位</th><th>參考值</th></tr>"
            "<tr><td>虛構醫院</td><td>112/10/12</td><td>K</td><td>4</td><td>mmol/L</td>"
            "<td>[3.5][5.5]</td></tr></table>"
        )
        self.assertEqual(parse_health_bank_html(html)["labs"][0]["reference"], "[3.5][5.5]")


class TestMutationGaps(unittest.TestCase):
    """2026-11-21 mutation 測試時沒有任何測試會紅的兩個性質。"""

    def test_new_format_resident_certificate_gender_digits_8_and_9_are_masked(self):
        for fake in ("A800000001", "Z912345678"):
            with self.subTest(fake=fake):
                self.assertEqual(anonymize_text(f"證號 {fake}"), "證號 [ID_REDACTED]")

    def test_unknown_file_name_never_reaches_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "王小明的檢驗.html").write_bytes(FIXTURE.read_bytes())
            out = Path(tmp) / "out"
            rc, stdout, err = run_main(src, out)
            self.assertEqual(rc, 0, err)
            everything = stdout + err + "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
            self.assertNotIn("王小明", everything)
            self.assertIn('"label": "unknown"', (out / "summary.json").read_text(encoding="utf-8"))


class TestCorruptZip(unittest.TestCase):
    def corrupt_zip(self, path: Path) -> None:
        data = FIXTURE.read_bytes() * 20
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("檢驗檢查結果_1131012.html", data)
        raw = bytearray(path.read_bytes())
        start = raw.find(b"PK\x03\x04")
        name_len = int.from_bytes(raw[start + 26:start + 28], "little")
        extra_len = int.from_bytes(raw[start + 28:start + 30], "little")
        body = start + 30 + name_len + extra_len
        for k in range(body + 50, body + 80):
            raw[k] ^= 0x55
        path.write_bytes(bytes(raw))

    def test_corrupt_member_is_a_clean_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "corrupt.zip"
            self.corrupt_zip(z)
            with self.assertRaises(ValueError):
                hbp.parse_path(z)

    def test_cli_reports_corrupt_zip_with_exit_2_and_no_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "corrupt.zip"
            self.corrupt_zip(z)
            rc, _, err = run_main(z, Path(tmp) / "out")
            self.assertEqual(rc, 2)
            self.assertIn("ZIP", err)
            self.assertNotIn("Traceback", err)
            self.assertFalse((Path(tmp) / "out").exists())


class TestOutputs(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX 權限位元；Windows 依父資料夾 ACL")
    def test_output_files_are_owner_only_on_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            rc, _, _ = run_main(FIXTURE, out)
            self.assertEqual(rc, 0)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode) & 0o077, 0)
            for p in out.iterdir():
                with self.subTest(file=p.name):
                    self.assertEqual(stat.S_IMODE(p.stat().st_mode) & 0o077, 0)

    def test_warns_when_nothing_was_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.html"
            empty.write_bytes(b"")
            rc, _, err = run_main(empty, Path(tmp) / "out")
            self.assertEqual(rc, 0)
            self.assertIn("沒有解析出任何資料", err)

    def test_no_warning_when_records_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _, err = run_main(FIXTURE, Path(tmp) / "out")
            self.assertEqual(rc, 0)
            self.assertNotIn("沒有解析出任何資料", err)


if __name__ == "__main__":
    unittest.main()
