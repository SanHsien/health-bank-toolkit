"""公開內容的隱私與免責契約：範例、文件、範本不得出現個資格式，且保留醫療免責聲明。

tests/fixtures/ 是刻意放入虛構個資、用來驗證遮罩的測試樣本，不在掃描範圍。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED_DIRS = ("assets", "docs", "templates", "notes")
TW_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][1289]\d{8}(?![0-9])")
MOBILE = re.compile(r"(?<![0-9])09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?![0-9])")


def published_files():
    for d in PUBLISHED_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and p.suffix in {".md", ".html", ".json", ".txt"}:
                yield p


class PublishedContentTest(unittest.TestCase):
    def test_no_id_or_mobile_number_formats(self):
        files = list(published_files())
        self.assertGreater(len(files), 0)
        for p in files:
            text = p.read_text(encoding="utf-8")
            with self.subTest(file=str(p.relative_to(ROOT))):
                self.assertEqual(TW_ID.findall(text), [])
                self.assertEqual(MOBILE.findall(text), [])

    def test_medical_disclaimers_are_kept(self):
        dashboard = (ROOT / "assets/health-bank-dashboard/index.html").read_text(encoding="utf-8")
        self.assertIn("免責", dashboard)
        for rel in ("templates/health-bank-dashboard-prompt.md", "templates/health-report-pdf-prompt.md"):
            with self.subTest(file=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertTrue("免責" in text or "不是醫師" in text, rel)

    def test_patterns_catch_real_formats(self):
        self.assertEqual(TW_ID.findall("身分證 A123456789"), ["A123456789"])
        self.assertEqual(MOBILE.findall("電話 0912-345-678"), ["0912-345-678"])
        self.assertEqual(TW_ID.findall("醫令碼 AC04512345"), [])  # 醫令碼不是身分證


if __name__ == "__main__":
    unittest.main()
