#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健保快易通「健康存摺」離線脫敏與結構化解析工具（Health Bank Anonymizer & Parser）。

只用 Python stdlib，完全離線。輸入可為資料夾（所有 HTML）、單一 HTML、或 ZIP（逐一解析
內含的每個 HTML）。解析由表頭驅動：依「第一列表頭的欄位集合」辨識類別，不單靠檔名。

輸出（每個類別一個 JSON 檔 + summary.json）：

    labs              檢驗檢查結果（含 flag：偏高／偏低／正常／未判定）
    outpatient        門診資料   [{..., orders: [...]}]
    medications       用藥資料   [{..., drugs:  [...]}]
    inpatient         住診資料   [{..., orders: [...]}]
    surgery           手術資料   [{..., orders: [...]}]
    dental            牙醫門診資料
    vaccinations      預防接種資料
    imaging_reports   影像或病理檢查報告（自由文字，經 anonymize_text）
    directives        器捐或安寧緩和醫療意願註記
    generic           其他表格（表頭列 + 資料列）

脫敏原則：姓名、身分證字號、生日、電話、地址、病歷號一律遮蔽；健保卡就醫序號遮蔽；
個人資料標題表／註記表整張丟棄。醫事機構名稱、診斷名稱、藥品名稱屬於有用資料，保留。

CLI：python tools/health_bank_parser.py <folder|file|zip> <out_dir> [--allow-in-repo]
真實資料不得落入 git 倉庫：out_dir 位於 git 工作樹內時拒絕執行（除非加 --allow-in-repo）。
"""

from __future__ import annotations

import argparse
import codecs
import itertools
import json
import math
import re
import sys
import unicodedata
import zipfile
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

PARSER_VERSION = 3

CATEGORIES: Tuple[str, ...] = (
    "labs",
    "outpatient",
    "medications",
    "inpatient",
    "surgery",
    "dental",
    "vaccinations",
    "imaging_reports",
    "directives",
    "generic",
)

RULES_APPLIED = [
    "name",
    "id_number",
    "birth_date",
    "phone",
    "address",
    "medical_record_no",
    "card_visit_sequence",
    "patient_title_rows_dropped",
]

# 檔名（去掉尾端民國日期）可辨識的類別標籤；只當作 hint 與來源標示，不含個資。
KNOWN_LABELS = (
    "檢驗檢查結果",
    "門診資料",
    "用藥資料",
    "住診資料",
    "手術資料",
    "牙醫門診資料",
    "預防接種資料",
    "影像或病理檢查資料",
    "器捐或安寧緩和醫療意願",
    "糖尿病追蹤",
    "COVID-19疫苗接種／病毒檢測結果",
)

MAX_MEMBER_BYTES = 200 * 1024 * 1024

UNDETERMINED = "未判定"
FLAG_HIGH = "偏高"
FLAG_LOW = "偏低"
FLAG_NORMAL = "正常"


class EncryptedArchiveError(ValueError):
    """ZIP 內含加密成員，無法離線解析。"""


# --------------------------------------------------------------------------- #
# HTML 表格抽取
# --------------------------------------------------------------------------- #


class _TableState:
    __slots__ = ("rows", "row", "cell")

    def __init__(self) -> None:
        self.rows: List[List[str]] = []
        self.row: Optional[List[str]] = None
        self.cell: Optional[List[str]] = None


def _cell_text(pieces: Sequence[str]) -> str:
    """合併儲存格片段；保留換行（<br>/<p>），每行內空白壓縮。"""
    text = "".join(pieces).replace("\xa0", " ").replace("　", " ")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


class TableDataExtractor(HTMLParser):
    """輕量 HTML 表格抽取器：支援巢狀表格、缺漏的 </td></tr>、<br> 換行。"""

    _BLOCK_BREAK = {"br", "p", "div", "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._stack: List[_TableState] = []
        self._skip = 0

    def _close_cell(self, st: _TableState) -> None:
        if st.cell is not None:
            if st.row is None:
                st.row = []
            st.row.append(_cell_text(st.cell))
            st.cell = None

    def _close_row(self, st: _TableState) -> None:
        self._close_cell(st)
        if st.row is not None:
            st.rows.append(st.row)
            st.row = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._stack.append(_TableState())
        elif not self._stack:
            return
        elif tag == "tr":
            st = self._stack[-1]
            self._close_row(st)
            st.row = []
        elif tag in ("td", "th"):
            st = self._stack[-1]
            self._close_cell(st)
            if st.row is None:
                st.row = []
            st.cell = []
        elif tag in self._BLOCK_BREAK and self._stack[-1].cell is not None:
            self._stack[-1].cell.append("\n")

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in ("script", "style"):
            return
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._skip or not self._stack:
            return
        cell = self._stack[-1].cell
        if cell is not None:
            cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif not self._stack:
            return
        elif tag in ("td", "th"):
            self._close_cell(self._stack[-1])
        elif tag == "tr":
            self._close_row(self._stack[-1])
        elif tag == "table":
            st = self._stack.pop()
            self._close_row(st)
            self.tables.append(st.rows)
        elif tag in self._BLOCK_BREAK and self._stack[-1].cell is not None:
            self._stack[-1].cell.append("\n")

    def finish(self) -> List[List[List[str]]]:
        self.close()
        while self._stack:
            st = self._stack.pop()
            self._close_row(st)
            self.tables.append(st.rows)
        return self.tables


# --------------------------------------------------------------------------- #
# 編碼偵測
# --------------------------------------------------------------------------- #

_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)
_MARKERS = ("醫", "日期", "健保", "檢", "診", "藥", "資料", "名稱", "註", "機構", "接種", "影像", "結果")
_BIG5_FAMILY = {"big5", "big5hkscs", "cp950", "big5-hkscs"}


def _marker_count(text: str) -> int:
    return sum(text.count(m) for m in _MARKERS)


def _declared_charset(data: bytes) -> Optional[str]:
    m = _CHARSET_RE.search(data[:4096])
    if not m:
        return None
    try:
        return codecs.lookup(m.group(1).decode("ascii", "ignore")).name
    except LookupError:
        return None


def _repair_mojibake(text: str) -> Optional[str]:
    """UTF-8 位元組被誤當單位元組／Big5 解碼後又存成 UTF-8 的亂碼，嘗試還原。"""
    if text.isascii():
        return None
    base = _marker_count(text)
    for enc in ("cp950", "latin-1", "cp1252", "big5hkscs"):
        try:
            fixed = text.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if _marker_count(fixed) > base:
            return fixed
    return None


def decode_html_bytes(data: bytes) -> Tuple[str, str]:
    """回傳 (文字, 使用的編碼)。嚴格解碼，不使用 errors="replace" 吞掉亂碼。

    順序：BOM → UTF-8（嚴格；非 ASCII 文字能通過嚴格 UTF-8 幾乎必為 UTF-8）→
    <meta charset> 宣告（Big5 系統一以 cp950 / big5hkscs 嘗試）→ cp950 → big5hkscs →
    最後手段 cp950 replace（編碼名稱帶 -replace 以便檢查）。
    """
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if not text.isascii() and _marker_count(text) == 0:
            repaired = _repair_mojibake(text)
            if repaired is not None:
                return repaired, "utf-8+repaired"
        return text, "utf-8"

    declared = _declared_charset(data)
    candidates: List[str] = []
    if declared and declared != "utf-8":
        if declared in _BIG5_FAMILY:
            candidates += ["cp950", "big5hkscs"]
        else:
            candidates.append(declared)
    for enc in ("cp950", "big5hkscs"):
        if enc not in candidates:
            candidates.append(enc)
    for enc in candidates:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("cp950", errors="replace"), "cp950-replace"


# --------------------------------------------------------------------------- #
# 脫敏
# --------------------------------------------------------------------------- #


# 全形英數字 → 半形（一對一，字串長度不變）
_FULLWIDTH_ALNUM = {
    **{0xFF10 + i: 0x30 + i for i in range(10)},
    **{0xFF21 + i: 0x41 + i for i in range(26)},
    **{0xFF41 + i: 0x61 + i for i in range(26)},
    0xFF0D: 0x2D,
}


def anonymize_text(text: str, addresses: bool = True) -> str:
    """全面脫敏個人敏感識別資訊 (PII)。

    addresses=False 用於醫事機構欄位：地址規則的尾端字元類別涵蓋所有漢字，會把
    「○○縣○○鄉衛生所」這類機構名整段吃掉，所以機構欄位略過地址規則。
    """
    if not text:
        return text

    # 0. 全形英數字與連字號先轉半形（長度不變），否則「Ａ１２３４５６７８９」之類寫法會躲過下列規則
    text = text.translate(_FULLWIDTH_ALNUM)

    # 1. 台灣身分證字號 (1碼大寫英文字母 + 1碼性別碼1/2/8/9 + 8碼數字)；
    text = re.sub(r"[A-Z][1289]\d{8}", "[ID_REDACTED]", text)
    #    舊式外僑居留證（2 碼英文 + 8 碼數字）與醫令碼（如 AC04512345）格式相同，只在有標籤時遮
    text = re.sub(
        r"((?:居留證|居留證號|統一證號|居留證統一證號)(?:碼)?\s*[:：#]?\s*)[A-Z]{2}\d{8}",
        r"\1[ID_REDACTED]",
        text,
    )

    # 2. 手機與室內電話（前面緊接英數字的是藥品／醫囑代碼的一部分，例如 AC0xxxxxxx，不是電話）
    text = re.sub(r"(?<![A-Za-z0-9])(?:\+886[-\s]?0?|0)9\d{2}[-\s]?\d{3}[-\s]?\d{3}", "[PHONE_REDACTED]", text)
    text = re.sub(r"(?<![A-Za-z0-9])\(0\d{1,2}\)\s?\d{3,4}[-\s]?\d{3,4}", "[PHONE_REDACTED]", text)
    text = re.sub(r"(?<![A-Za-z0-9])0\d{1,2}[-\s]?\d{6,8}", "[PHONE_REDACTED]", text)
    #    區碼與號碼分組寫法（02-2345-6789、049 234 5678）、+886 市話：一定要有分隔符，避免吃到連續數字代碼
    text = re.sub(r"(?<![A-Za-z0-9])0\d{1,2}[-\s]\d{3,4}[-\s]\d{3,4}(?![0-9])", "[PHONE_REDACTED]", text)
    text = re.sub(r"\+886[-\s]?\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4}(?![0-9])", "[PHONE_REDACTED]", text)

    # 3. 生日 (民國或西元年格式)
    text = re.sub(
        r"民國\s*\d{2,3}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日",
        "[BIRTHDATE_REDACTED]",
        text,
    )
    text = re.sub(
        r"民國\s*\d{2,3}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}",
        "[BIRTHDATE_REDACTED]",
        text,
    )
    # 不用 \b：Python 的 \b 把漢字也當成文字字元，「出生1990-05-12」前面沒有邊界而漏遮。
    text = re.sub(
        r"(?<![0-9])(19\d{2}|20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?![0-9])",
        "[DATE_MASKED]",
        text,
    )

    # 4. 病歷號 (MRN-xxxx，或「病歷號／病歷編號／病歷號碼」標籤後的代碼)
    text = re.sub(r"MRN-[A-Za-z0-9\-]+", "[RECORD_NO_REDACTED]", text)
    text = re.sub(
        r"(病歷(?:號碼|編號|號)\s*[:：#]?\s*)[A-Za-z0-9][A-Za-z0-9\-]*",
        r"\1[RECORD_NO_REDACTED]",
        text,
    )

    # 5. 地址特徵 (含縣/市/區/鄉/鎮/路/街/段/號)
    if addresses:
        text = re.sub(
            r"[一-龥]{2,3}(縣|市)[一-龥]{1,4}(區|鄉|鎮|市)[一-龥0-9\-段巷弄號]+",
            "[ADDRESS_REDACTED]",
            text,
        )

    return text


# --------------------------------------------------------------------------- #
# 日期與數值
# --------------------------------------------------------------------------- #

_DATE_RE = re.compile(
    r"^(\d{2,4})[/.\-](\d{1,2})[/.\-](\d{1,2})"
    r"(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$",
    re.ASCII,
)
_COMPACT_DATE_RE = re.compile(r"^(\d{7}|\d{8})$", re.ASCII)
_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$", re.ASCII)
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$", re.ASCII)


def to_iso(value: Optional[str], compact_ok: bool = False) -> Optional[str]:
    """民國 112/10/12 → 2023-10-12；西元 4 位年份照原樣標準化；含時間則回 ISO 日期時間。

    無法解析或日期不存在時回傳 None。compact_ok 允許 7 碼民國／8 碼西元緊縮格式。
    """
    s = unicodedata.normalize("NFKC", value or "").strip()
    if not s:
        return None
    hh = mm = ss = None
    m = _DATE_RE.match(s)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        year = int(y)
        if year < 1000:
            year += 1911
        month, day = int(mo), int(d)
    elif compact_ok and _COMPACT_DATE_RE.match(s):
        if len(s) == 7:
            year, month, day = int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])
        else:
            year, month, day = int(s[:4]), int(s[4:6]), int(s[6:8])
    else:
        return None
    if not 1900 <= year <= 2200:
        return None
    try:
        iso = date(year, month, day).isoformat()
    except ValueError:
        return None
    if hh is None:
        return iso
    if int(hh) > 23 or int(mm) > 59 or (ss is not None and int(ss) > 59):
        return None
    return f"{iso}T{int(hh):02d}:{mm}" + (f":{ss}" if ss is not None else "")


def to_number(text: Optional[str]) -> Optional[float]:
    """嚴格數值解析：整數樣式回 int，其餘回 float；非數值、NaN、Inf 回 None。"""
    s = unicodedata.normalize("NFKC", text or "").strip()
    if _THOUSANDS_RE.match(s):
        s = s.replace(",", "")
    if not _NUM_RE.match(s):
        return None
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        num = float(s)
    except ValueError:
        return None
    return num if math.isfinite(num) else None


# --------------------------------------------------------------------------- #
# 檢驗結果旗標
# --------------------------------------------------------------------------- #

_REF_RE = re.compile(r"\[([^\[\]]*)\]\s*\[([^\[\]]*)\]")


def parse_reference(reference: Optional[str]) -> Optional[Tuple[str, str]]:
    """`[3.5][5.5]` → ("3.5", "5.5")；`[Negative][]` → ("Negative", "")；格式不符回 None。"""
    s = unicodedata.normalize("NFKC", reference or "")
    m = _REF_RE.search(s)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def lab_flag(value_text: Optional[str], reference_text: Optional[str]) -> Tuple[str, Optional[float], Optional[float]]:
    """回傳 (flag, ref_low, ref_high)。

    只有「結果值為數值」且「出現的上下界皆為數值」才判定；允許單邊（低界或高界為空）。
    任何非數值界線（`[-][]`、`[Negative][]`）、缺參考值、界線顛倒、結果值非數值
    （`<0.5`、`Positive`）一律 未判定，絕不預設為正常。
    """
    pair = parse_reference(reference_text)
    if pair is None:
        return UNDETERMINED, None, None
    low_s, high_s = pair
    low = to_number(low_s) if low_s else None
    high = to_number(high_s) if high_s else None
    if (low_s and low is None) or (high_s and high is None):
        return UNDETERMINED, None, None
    if low is None and high is None:
        return UNDETERMINED, None, None
    if low == 0 and high == 0:
        # `[0][0]`（例如凝血檢查的對照值）代表沒有參考值，不是「範圍 0～0」。
        return UNDETERMINED, None, None
    if low is not None and high is not None and low > high:
        return UNDETERMINED, low, high
    value = to_number(value_text)
    if value is None:
        return UNDETERMINED, low, high
    if low is not None and value < low:
        return FLAG_LOW, low, high
    if high is not None and value > high:
        return FLAG_HIGH, low, high
    return FLAG_NORMAL, low, high


# --------------------------------------------------------------------------- #
# 欄位辨識
# --------------------------------------------------------------------------- #

_PII_LABELS = (
    "姓名", "身分證", "身份證", "出生", "生日", "電話", "手機", "地址", "住址", "戶籍",
    "病歷號", "病歷編號", "信箱", "email", "e-mail",
)

# key-value 個資表的「標籤格」：整格就是標籤（可帶前後綴與冒號），避免「出生24小時內接種」「電話諮詢費」誤判
_KV_PII_LABEL_CELL = re.compile(
    r"^(聯絡|行動|住家|通訊|戶籍|居住)?"
    r"(姓名|身分證|身份證|出生|生日|電話|手機|地址|住址|戶籍|病歷號|病歷編號|信箱|email|e-mail)"
    r"(日期|年月日|字號|號碼|號|地址)?[:：]?$"
)


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _norm_header(text: str) -> str:
    return unicodedata.normalize("NFKC", "".join((text or "").split()))


# (比對方式, 表頭片段, 輸出欄位名)；順序有意義（較具體者在前）。
_FIELD_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("in", "健保署服務單位", "service_unit"),
    ("in", "醫事機構", "institution"),
    ("in", "就醫序號", "card_seq"),
    ("in", "交付調劑", "service_date"),
    ("in", "檢驗(查)日期", "test_date"),
    ("in", "資料上傳時間", "uploaded_at"),
    ("in", "就醫日期/入院日期", "visit_date"),
    ("in", "就醫(調劑)/入院日期", "visit_date"),
    ("in", "入院日期", "admission_date"),
    ("in", "出院日期", "discharge_date"),
    ("in", "就醫日期", "visit_date"),
    ("in", "申報起日", "claim_start_date"),
    ("in", "申報迄日", "claim_end_date"),
    ("in", "疾病分類碼", "diagnosis_code"),
    ("in", "診斷碼", "diagnosis_code"),
    ("in", "疾病分類名稱", "diagnosis_name"),
    ("in", "診斷名稱", "diagnosis_name"),
    ("in", "處置碼", "procedure_code"),
    ("in", "處置名稱", "procedure_name"),
    ("in", "部分負擔", "copay"),
    ("in", "支付點數", "points"),
    ("in", "藥品醫囑代碼", "drug_code"),
    ("in", "藥品名稱", "drug_name"),
    ("in", "藥品藥理分類", "drug_class"),
    ("in", "給藥日數", "days"),
    ("in", "執行起日", "start_date"),
    ("in", "執行迄日", "end_date"),
    ("in", "手術日期", "surgery_date"),
    ("in", "診療部位", "body_site"),
    ("in", "牙位代碼", "tooth_code"),
    ("in", "牙位名稱", "tooth_name"),
    ("in", "醫囑序號", "order_seq"),
    ("in", "醫囑代碼", "order_code"),
    ("in", "醫囑名稱", "order_name"),
    ("in", "醫囑總量", "quantity"),
    ("in", "醫囑數量", "quantity"),
    ("in", "檢驗項目名稱", "item"),
    ("eq", "結果值", "value"),
    ("eq", "單位", "unit"),
    ("eq", "參考值", "reference"),
    ("in", "接種日期", "vaccination_date"),
    ("in", "疫苗中文名稱", "vaccine_name"),
    ("in", "健保卡註記日期", "card_note_date"),
    ("in", "影像或病理報告內容", "report"),
    ("in", "判定", "reported_flag"),
    ("in", "標記", "reported_flag"),
)

_NUMERIC_KEYS = {"copay", "points", "days", "quantity"}


def field_key(header_norm: str) -> Optional[str]:
    for mode, pattern, key in _FIELD_RULES:
        if (mode == "eq" and header_norm == pattern) or (mode == "in" and pattern in header_norm):
            return key
    return None


def column_kind(header_norm: str) -> str:
    """依表頭決定儲存格清理方式：pii / seq / date / org / text。"""
    if any(label in header_norm for label in _PII_LABELS):
        return "pii"
    if "就醫序號" in header_norm:
        return "seq"
    if "日期" in header_norm or "時間" in header_norm or header_norm.endswith(("起日", "迄日")):
        return "date"
    if any(w in header_norm for w in ("機構", "醫院", "院所", "服務單位")):
        return "org"
    return "text"


def clean_unaligned(cells: List[str], headers_norm: List[str]) -> List[str]:
    """對不上欄位的儲存格（溢位、無法對齊的列）。

    表格有個資欄位（姓名、身分證…）時，對不上的格子可能正是那一欄的值，而沒有標籤的純姓名
    文字遮罩抓不到，所以整格遮掉；表格沒有個資欄位時，只做一般文字遮罩，保留醫療數據。
    """
    if any(column_kind(h) == "pii" for h in headers_norm):
        return [clean_cell(c, "pii") for c in cells]
    return [clean_cell(c, "text") for c in cells]


def clean_cell(value: str, kind: str = "text") -> str:
    """依欄位性質脫敏單一儲存格。"""
    if kind == "pii":
        return "[REDACTED]" if _flat(value) else ""
    if kind == "seq":
        return "[SEQ_REDACTED]" if _flat(value) else ""
    if kind == "report":
        return anonymize_text(value.strip())
    text = _flat(value)
    if kind == "date":
        if to_iso(text, compact_ok=True) is not None:
            return text
        return anonymize_text(text, addresses=False)
    if kind == "org":
        return anonymize_text(text, addresses=False)
    return anonymize_text(text)


def _unique_key(existing: Dict[str, Any], key: str) -> str:
    if key not in existing:
        return key
    n = 2
    while f"{key}_{n}" in existing:
        n += 1
    return f"{key}_{n}"


def _build_fields(headers_flat: Sequence[str], headers_norm: Sequence[str], cells: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    extra: Dict[str, Any] = {}
    for h_flat, h_norm, raw in zip(headers_flat, headers_norm, cells):
        kind = column_kind(h_norm)
        key = field_key(h_norm)
        cleaned = clean_cell(raw, "report" if key == "report" else kind)
        if key is None:
            ek = _unique_key(extra, anonymize_text(h_flat, addresses=False) or "col")
            extra[ek] = cleaned
            if kind == "date":
                extra[ek + "_iso"] = to_iso(cleaned, compact_ok=True)
            continue
        key = _unique_key(out, key)
        value: Any = cleaned
        if key in _NUMERIC_KEYS:
            num = to_number(cleaned)
            value = num if num is not None else cleaned
        out[key] = value
        if kind == "date":
            out[key + "_iso"] = to_iso(cleaned, compact_ok=True)
    if extra:
        out["extra"] = extra
    return out


# --------------------------------------------------------------------------- #
# 類別辨識
# --------------------------------------------------------------------------- #


def classify_headers(headers_norm: Sequence[str], hint: str = "") -> Optional[str]:
    """依表頭欄位集合辨識類別；hint（檔名類別標籤）只用來分辨門診／牙醫門診。"""
    hs = [h for h in headers_norm if h and len(h) <= 40]

    def has(sub: str) -> bool:
        return any(sub in h for h in hs)

    if has("檢驗項目名稱") and has("結果值"):
        return "labs"
    if has("影像或病理報告內容"):
        return "imaging_reports"
    if has("疾病分類碼") and has("處置碼"):
        return "surgery" if has("就醫日期") else "inpatient"
    if has("疾病分類碼"):
        return "medications"
    if has("主診斷碼"):
        if "牙醫" in hint:
            return "dental"
        if has("交付調劑") or "門診" in hint:
            return "outpatient"
        return "dental"
    if has("疫苗中文名稱") and has("接種日期"):
        return "vaccinations"
    if has("健保卡註記日期"):
        return "directives"
    return None


_HIERARCHICAL = {
    "outpatient": "orders",
    "medications": "drugs",
    "inpatient": "orders",
    "surgery": "orders",
    "dental": "orders",
}

# 觀察到的明細子表頭（實際檔案若有子表頭列，會以檔案內容覆寫）。
_DEFAULT_DETAIL_HEADERS: Dict[str, Tuple[str, ...]] = {
    "outpatient": ("醫囑代碼", "醫囑名稱", "醫囑總量"),
    "medications": ("藥品醫囑代碼", "藥品名稱", "藥品藥理分類", "給藥日數", "執行起日", "執行迄日", "醫囑總量"),
    "inpatient": ("醫囑序號", "執行起日", "執行迄日", "醫囑代碼", "醫囑名稱", "醫囑數量"),
    "surgery": ("手術日期", "醫囑代碼", "醫囑名稱", "診療部位", "醫囑數量"),
    "dental": ("醫囑代碼", "醫囑名稱", "牙位代碼", "牙位名稱", "醫囑總量"),
}

_DETAIL_KEYS = {
    "drug_code", "drug_name", "drug_class", "days", "start_date", "end_date",
    "order_seq", "order_code", "order_name", "quantity",
    "surgery_date", "body_site", "tooth_code", "tooth_name",
}

# 主檔表頭的第一格跨好幾列（rowspan），底下接「次診斷碼N／次診斷名稱N」「次處置碼N／次處置名稱N」
# 子表頭列。資料裡每一筆主檔後面固定跟著同樣數量的次要列（沒有資料時整列空白），順序與子表頭相同。
_SECONDARY_RE = re.compile(r"^次(診斷|處置)(碼|名稱)(\d+)$")


def _secondary_layout(norm_row: Sequence[str]) -> Optional[List[Optional[Tuple[str, str, int]]]]:
    """若整列是次診斷／次處置子表頭，回傳每格的 (dx|proc, code|name, 序號)，空格為 None。"""
    if not any(norm_row):
        return None
    slots: List[Optional[Tuple[str, str, int]]] = []
    for cell in norm_row:
        if not cell:
            slots.append(None)
            continue
        m = _SECONDARY_RE.match(cell)
        if not m:
            return None
        slots.append(("dx" if m.group(1) == "診斷" else "proc", "code" if m.group(2) == "碼" else "name", int(m.group(3))))
    return slots


def _add_secondary(rec: Dict[str, Any], layout: Sequence[Optional[Tuple[str, str, int]]], cells: Sequence[str]) -> None:
    found: Dict[Tuple[str, int], Dict[str, str]] = {}
    for slot, raw in zip(layout, cells):
        if slot is None:
            continue
        value = clean_cell(raw, "text")
        if value:
            kind, part, idx = slot
            found.setdefault((kind, idx), {})[part] = value
    for (kind, _idx), item in sorted(found.items()):
        key = "secondary_diagnoses" if kind == "dx" else "secondary_procedures"
        rec.setdefault(key, []).append({"code": item.get("code", ""), "name": item.get("name", "")})

_LEAD_INDEX_RE = re.compile(r"^\d{1,4}$")


def _as_detail_header(row: Sequence[str]) -> Optional[Tuple[List[str], List[str]]]:
    """若整列是明細子表頭（前置空白格除外的每格都是已知明細欄位），回傳 (flat, norm)。"""
    cells = [c for c in row]
    while cells and not _flat(cells[0]):
        cells = cells[1:]
    if len(cells) < 3 or any(not _flat(c) for c in cells):
        return None
    norm = [_norm_header(c) for c in cells]
    if not all(len(h) <= 20 and field_key(h) in _DETAIL_KEYS for h in norm):
        return None
    return [_flat(c) for c in cells], norm


# --------------------------------------------------------------------------- #
# 表格處理
# --------------------------------------------------------------------------- #

_DIAG_KEYS = (
    "secondary_rows",
    "duplicate_rows",
    "separator_rows",
    "repeated_header_rows",
    "short_master_rows",
    "orphan_detail_rows",
    "unmatched_rows",
    "irregular_rows",
    "ignored_tables",
)

_DATE_KEYS = {
    "labs": ("test_date_iso", "visit_date_iso"),
    "outpatient": ("visit_date_iso",),
    "medications": ("visit_date_iso",),
    "inpatient": ("admission_date_iso",),
    "surgery": ("visit_date_iso", "admission_date_iso"),
    "dental": ("visit_date_iso",),
    "vaccinations": ("vaccination_date_iso",),
    "imaging_reports": ("test_date_iso", "visit_date_iso"),
    "directives": ("card_note_date_iso",),
}


def _is_date_header(h: str) -> bool:
    return column_kind(h) == "date"


def _is_numeric_header(h: str) -> bool:
    return field_key(h) in ("copay", "points")


def _score_alignment(headers_norm: Sequence[str], aligned: Sequence[str]) -> int:
    score = 0
    for h, v in zip(headers_norm, aligned):
        v = v.strip()
        if not v:
            continue
        is_date = to_iso(v) is not None
        if _is_date_header(h):
            score += 1 if is_date else -1
        elif is_date:
            score -= 1
        if _is_numeric_header(h):
            score += 1 if to_number(v) is not None else -1
    return score


def _looks_like_unit(first: str, known_units: set) -> bool:
    first = _flat(first)
    if first in known_units or first.endswith("組"):
        return True
    return not known_units  # 沒有樣本可比對時不強制


def _align_short_master(
    cells: Sequence[str],
    headers_norm: Sequence[str],
    known_units: set,
    blank_counts: Sequence[int],
    full_count: int,
) -> List[str]:
    """短少 1-2 格的主列：推論缺少哪幾格。

    打分依序為：(1) 日期／數值欄位的內容吻合度；(2) 缺格落在「完整主列中常為空白」的欄位
    （例如門診的出院日期）；(3) 同分取靠後的位置。若表頭第一欄是「健保署服務單位」而
    第一格不像業務組名稱（不在已見過的名稱集合、也不以「組」結尾），判定缺的是第一格。
    """
    h_len, n = len(headers_norm), len(cells)
    k = h_len - n
    forced: set = set()
    if "服務單位" in headers_norm[0] and not _looks_like_unit(cells[0], known_units):
        forced = {0}
    best: Optional[Tuple[Tuple[int, float, int], Tuple[int, ...]]] = None
    for gap in itertools.combinations(range(h_len), k):
        if forced and not forced <= set(gap):
            continue
        it = iter(cells)
        aligned = ["" if i in gap else next(it) for i in range(h_len)]
        blank_prior = sum(blank_counts[i] / full_count for i in gap) if full_count else 0.0
        key = (_score_alignment(headers_norm, aligned), blank_prior, sum(gap))
        if best is None or key > best[0]:
            best = (key, gap)
    gap = best[1] if best else tuple(range(k))
    it = iter(cells)
    return ["" if i in gap else next(it) for i in range(h_len)]


def _parse_hierarchical(
    category: str, rows: List[List[str]], header_idx: int, diag: Dict[str, int]
) -> List[Dict[str, Any]]:
    detail_key = _HIERARCHICAL[category]
    head = rows[header_idx]
    headers_flat = [_flat(c) for c in head]
    headers_norm = [_norm_header(c) for c in head]
    h_len = len(headers_norm)

    default = _DEFAULT_DETAIL_HEADERS.get(category)
    detail_flat: Optional[List[str]] = list(default) if default else None
    detail_norm: Optional[List[str]] = [_norm_header(c) for c in default] if default else None

    records: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    known_units: set = set()
    blank_counts = [0] * h_len
    full_count = 0
    secondary_layouts: List[List[Optional[Tuple[str, str, int]]]] = []
    pending = 0  # 目前主檔還剩幾列次診斷／次處置列

    for row in rows[header_idx + 1:]:
        n = len(row)
        norm_row = [_norm_header(c) for c in row]
        if not any(norm_row):
            # 整列空白：多半是某筆主檔「沒有資料的次要列」，佔掉一個位置
            if pending:
                pending -= 1
            continue
        if norm_row == headers_norm:
            diag["repeated_header_rows"] += 1
            continue
        layout = _secondary_layout(norm_row)
        if layout is not None:
            secondary_layouts.append(layout)
            continue
        if pending and current is not None and n == h_len and not _flat(row[0]):
            _add_secondary(current, secondary_layouts[len(secondary_layouts) - pending], row[1:])
            pending -= 1
            diag["secondary_rows"] += 1
            continue
        if secondary_layouts and n == h_len and not _flat(row[0]) and not any(to_iso(_flat(c)) for c in row):
            # 次要列出現在預期位置之外：不能當成新的一次就醫
            diag["unmatched_rows"] += 1
            if current is not None:
                current.setdefault("extra_rows", []).append(clean_unaligned(row, headers_norm))
            continue
        sub = _as_detail_header(row)
        if sub is not None:
            detail_flat, detail_norm = sub
            continue
        if n == 1 and h_len > 1:
            diag["separator_rows"] += 1
            continue

        # 明細列：欄數 = 子表頭欄數 + 前置空白／序號格，或剛好等於子表頭欄數（且不可能是主列）
        if detail_norm is not None and n != h_len:
            d = len(detail_norm)
            cells: Optional[List[str]] = None
            if n == d + 1 and (not _flat(row[0]) or _LEAD_INDEX_RE.match(_flat(row[0]))):
                cells = list(row[1:])
            elif n == d and n < h_len - 2:
                cells = list(row)
            if cells is not None:
                if current is None:
                    current = {"orphan": True, detail_key: []}
                    records.append(current)
                    diag["orphan_detail_rows"] += 1
                current[detail_key].append(_build_fields(detail_flat, detail_norm, cells))
                pending = 0
                continue

        if n == h_len:
            rec = _build_fields(headers_flat, headers_norm, row)
            if _flat(row[0]):
                known_units.add(_flat(row[0]))
            full_count += 1
            for i, c in enumerate(row):
                if not _flat(c):
                    blank_counts[i] += 1
        elif h_len - 2 <= n < h_len:
            aligned = _align_short_master(row, headers_norm, known_units, blank_counts, full_count)
            rec = _build_fields(headers_flat, headers_norm, aligned)
            rec["row_alignment"] = "inferred"
            diag["short_master_rows"] += 1
        else:
            diag["unmatched_rows"] += 1
            if current is not None:
                current.setdefault("extra_rows", []).append(clean_unaligned(row, headers_norm))
            continue
        rec[detail_key] = []
        records.append(rec)
        current = rec
        pending = len(secondary_layouts)
    return records


def _parse_flat(
    category: str, rows: List[List[str]], header_idx: int, diag: Dict[str, int]
) -> List[Dict[str, Any]]:
    head = rows[header_idx]
    headers_flat = [_flat(c) for c in head]
    headers_norm = [_norm_header(c) for c in head]
    h_len = len(headers_norm)
    idx_value = next((i for i, h in enumerate(headers_norm) if field_key(h) == "value"), None)
    idx_ref = next((i for i, h in enumerate(headers_norm) if field_key(h) == "reference"), None)

    records: List[Dict[str, Any]] = []
    for row in rows[header_idx + 1:]:
        n = len(row)
        if [_norm_header(c) for c in row] == headers_norm:
            diag["repeated_header_rows"] += 1
            continue
        if n == 1 and h_len > 1:
            diag["separator_rows"] += 1
            continue
        cells = list(row)
        overflow: List[str] = []
        if n < h_len:
            cells += [""] * (h_len - n)
            diag["irregular_rows"] += 1
        elif n > h_len:
            overflow = cells[h_len:]
            cells = cells[:h_len]
            diag["irregular_rows"] += 1
        rec = _build_fields(headers_flat, headers_norm, cells)
        if overflow:
            rec.setdefault("extra", {})["overflow"] = clean_unaligned(overflow, headers_norm)
        if category == "labs":
            raw_value = _flat(cells[idx_value]) if idx_value is not None else ""
            raw_ref = _flat(cells[idx_ref]) if idx_ref is not None else ""
            if not rec.get("item") and not raw_value:
                diag["unmatched_rows"] += 1
                continue
            flag, low, high = lab_flag(raw_value, raw_ref)
            num = to_number(raw_value)
            rec["value_raw"] = rec.get("value", "")
            rec["value"] = float(num) if num is not None else rec["value_raw"]
            safe_ref = re.fullmatch(r"(?:\[[^\[\]]{0,40}\])+", unicodedata.normalize("NFKC", raw_ref))
            rec["reference"] = raw_ref if safe_ref else clean_cell(raw_ref, "text")
            rec["ref_low"] = low
            rec["ref_high"] = high
            rec["flag"] = flag
        elif category == "imaging_reports" and "report" not in rec:
            continue
        records.append(rec)
    return records


def _has_pii_label(header_norm: str) -> bool:
    return any(label in header_norm for label in _PII_LABELS)


def _is_key_value_pii_table(rows: List[List[str]]) -> bool:
    """多欄的「標籤｜值｜標籤｜值」個人資料表：除了表頭列以外，還有別的列出現個資標籤。

    一般欄式表格的個資標籤只會出現在表頭列（該欄會被整欄遮蔽）；標籤出現在資料列代表這是
    key-value 排版，值（姓名、生日…）若被當成表頭欄名或資料輸出就會外洩，整張丟棄。
    """
    window = rows[:4]
    width = max(len(r) for r in window)
    header = next(r for r in window if len(r) == width)
    for row in rows:
        if row is header:
            continue
        for i, cell in enumerate(row[:-1]):
            norm = _norm_header(cell).lower().replace(" ", "")
            if _KV_PII_LABEL_CELL.match(norm) and (row[i + 1] or "").strip():
                return True
    return False


def _parse_generic(rows: List[List[str]], label: str, diag: Dict[str, int]) -> Optional[Dict[str, Any]]:
    window = rows[:4]
    width = max(len(r) for r in window)
    header_idx = next((i for i, r in enumerate(window) if len(r) == width), 0)
    if width < 2 or len(rows) - header_idx < 2:
        diag["ignored_tables"] += 1
        return None
    head = rows[header_idx]
    headers_flat = [_flat(c) or f"col{i + 1}" for i, c in enumerate(head)]
    headers_norm = [_norm_header(c) for c in headers_flat]
    keys: List[str] = []
    seen: Dict[str, Any] = {}
    for h in headers_flat:
        k = _unique_key(seen, anonymize_text(h, addresses=False))
        seen[k] = None
        keys.append(k)
    out_rows: List[Dict[str, Any]] = []
    for row in rows[header_idx + 1:]:
        if len(row) == 1 and width > 1:
            diag["separator_rows"] += 1
            continue
        rec: Dict[str, Any] = {}
        for i, raw in enumerate(row[:width]):
            kind = column_kind(headers_norm[i])
            cleaned = clean_cell(raw, kind)
            rec[keys[i]] = cleaned
            if kind == "date":
                rec[keys[i] + "_iso"] = to_iso(cleaned, compact_ok=True)
        if len(row) > width:
            rec["_overflow"] = clean_unaligned(row[width:], headers_norm)
        out_rows.append(rec)
    return {"source": label or "unknown", "headers": keys, "rows": out_rows}


def _parse_imaging(rows: List[List[str]], header_idx: int, diag: Dict[str, int]) -> List[Dict[str, Any]]:
    """影像或病理報告：表頭有兩列（檢查資訊列＋「影像或病理報告內容」），資料也是兩列一組。"""
    info = rows[header_idx - 1] if header_idx > 0 and len(rows[header_idx - 1]) > 1 else None
    report_flat = [_flat(c) for c in rows[header_idx]]
    report_norm = [_norm_header(c) for c in rows[header_idx]]
    info_flat = [_flat(c) for c in info] if info else []
    info_norm = [_norm_header(c) for c in info] if info else []
    records: List[Dict[str, Any]] = []
    pending: Optional[Dict[str, Any]] = None
    for row in rows[header_idx + 1:]:
        norm_row = [_norm_header(c) for c in row]
        if norm_row in (report_norm, info_norm):
            diag["repeated_header_rows"] += 1
            continue
        if info and len(row) == len(info):
            if pending is not None:
                records.append(pending)  # 資訊列後面沒有內文
            pending = _build_fields(info_flat, info_norm, row)
            continue
        if len(row) == len(report_norm):
            rec = pending if pending is not None else {}
            rec.update(_build_fields(report_flat, report_norm, row))
            records.append(rec)
            pending = None
            continue
        diag["irregular_rows"] += 1
    if pending is not None:
        records.append(pending)
    return records


def _process_table(rows: List[List[str]], label: str, result: Dict[str, Any], diag: Dict[str, int]) -> None:
    all_rows = rows
    rows = [r for r in rows if any(_flat(c) for c in r)]
    if not rows:
        return
    category: Optional[str] = None
    header_idx = 0
    for i in range(min(3, len(rows))):
        cat = classify_headers([_norm_header(c) for c in rows[i]], label)
        if cat:
            category, header_idx = cat, i
            break
    if category is None:
        first = _flat(rows[0][0])
        if len(rows) < 2 or first.startswith("註"):
            diag["ignored_tables"] += 1  # 標題表（可能含個資）或註記表
            return
        if max(len(r) for r in rows) <= 3 and any(_has_pii_label(_norm_header(r[0])) for r in rows):
            diag["ignored_tables"] += 1  # 姓名／身分證等 key-value 個人資料表
            return
        if _is_key_value_pii_table(rows):
            diag["ignored_tables"] += 1
            return
        generic = _parse_generic(rows, label, diag)
        if generic is not None:
            result["generic"].append(generic)
        return
    if category in _HIERARCHICAL:
        # 空白列要保留：它們佔住「次診斷／次處置」的位置，丟掉就分不出後面的列屬於哪一種
        head = rows[header_idx]
        full_idx = next(i for i, r in enumerate(all_rows) if r is head)
        result[category].extend(_parse_hierarchical(category, all_rows, full_idx, diag))
    elif category == "imaging_reports":
        result[category].extend(_parse_imaging(rows, header_idx, diag))
    else:
        result[category].extend(_parse_flat(category, rows, header_idx, diag))


def _empty_result() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "metadata": {
            "anonymized": True,
            "parser_version": PARSER_VERSION,
            "rules_applied": list(RULES_APPLIED),
            "diagnostics": {k: 0 for k in _DIAG_KEYS},
            "files": [],
        }
    }
    for c in CATEGORIES:
        result[c] = []
    return result


def source_label(stem: str) -> str:
    """檔名（去尾端民國日期）若為已知類別標籤則回傳，否則 'unknown'（避免檔名帶入個資）。"""
    base = re.sub(r"_\d{6,8}$", "", unicodedata.normalize("NFKC", stem or "").strip())
    for known in KNOWN_LABELS:
        if unicodedata.normalize("NFKC", known) == base:
            return known
    return "unknown"


def _parse_html_into(result: Dict[str, Any], html: str, label: str, encoding: str = "") -> None:
    extractor = TableDataExtractor()
    extractor.feed(html)
    tables = extractor.finish()
    diag = result["metadata"]["diagnostics"]
    before = {c: len(result[c]) for c in CATEGORIES}
    for rows in tables:
        _process_table(rows, label, result, diag)
    result["metadata"]["files"].append(
        {
            "label": label or "unknown",
            "encoding": encoding,
            "tables": len(tables),
            "records": {c: len(result[c]) - before[c] for c in CATEGORIES if len(result[c]) - before[c]},
        }
    )


def _dedupe(result: Dict[str, Any]) -> None:
    """去掉重複上傳的同一筆資料，保留第一筆，數量記在 diagnostics.duplicate_rows。

    - labs：同檢驗日、同院、同項目、同單位、同原始數值（重新上傳時只差上傳時間，
      偶爾同一結果掛在兩個醫囑下）。
    - imaging_reports：同檢查日、同醫囑代碼、同內文（忽略空白與標點差異）。
    - vaccinations：「預防接種」與「COVID-19」兩個檔案會重複列出同一劑。
    """
    diag = result["metadata"]["diagnostics"]
    keys = {
        "labs": lambda r: (r.get("test_date"), r.get("institution"), r.get("item"), r.get("unit"), r.get("value_raw")),
        "imaging_reports": lambda r: (
            r.get("test_date") or r.get("visit_date"),
            r.get("order_code"),
            re.sub(r"\W", "", str(r.get("report", ""))).lower(),
        ),
        "vaccinations": lambda r: (r.get("vaccination_date"), r.get("vaccine_name"), r.get("institution")),
    }
    for cat, key in keys.items():
        seen: set = set()
        kept: List[Dict[str, Any]] = []
        for rec in result[cat]:
            k = key(rec)
            if k in seen:
                diag["duplicate_rows"] += 1
                continue
            seen.add(k)
            kept.append(rec)
        result[cat] = kept


def parse_health_bank_html(html_content: str, label: str = "") -> Dict[str, Any]:
    """解析一份健康存摺 HTML 文字，回傳脫敏後的結構化資料。"""
    result = _empty_result()
    _parse_html_into(result, html_content, label if label in KNOWN_LABELS else source_label(label))
    _dedupe(result)
    return result


# --------------------------------------------------------------------------- #
# 輸入來源：資料夾 / HTML / ZIP
# --------------------------------------------------------------------------- #

_HTML_SUFFIXES = (".html", ".htm")


def _iter_sources(path: Path) -> Iterator[Tuple[str, bytes]]:
    """逐一產生 (檔名stem, bytes)。"""
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in _HTML_SUFFIXES)
        if not files:
            raise ValueError("資料夾內未找到任何 HTML 檔案")
        for p in files:
            yield p.stem, p.read_bytes()
    elif path.suffix.lower() == ".zip":
        try:
            zf = zipfile.ZipFile(path, "r")
        except zipfile.BadZipFile as e:
            raise ValueError(f"不是有效的 ZIP 檔案：{e}") from None
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if any(i.flag_bits & 0x1 for i in infos):
                raise EncryptedArchiveError(
                    "ZIP 壓縮檔已加密（含密碼保護的成員），本工具不處理密碼；"
                    "請先自行解壓縮，再以資料夾方式輸入。"
                )
            htmls = [
                i for i in infos
                if i.filename.lower().endswith(_HTML_SUFFIXES)
                and "__MACOSX/" not in i.filename
                and not Path(i.filename).name.startswith("._")
            ]
            if not htmls:
                raise ValueError("ZIP 壓縮檔內未找到任何 HTML 檔案")
            for info in sorted(htmls, key=lambda i: i.filename):
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("ZIP 內有過大的 HTML 成員，拒絕解析")
                with zf.open(info) as fh:
                    yield Path(info.filename).stem, fh.read()
    else:
        yield path.stem, path.read_bytes()


def parse_path(file_path: Any) -> Dict[str, Any]:
    """解析資料夾、單一 HTML 或 ZIP，合併所有 HTML 的結果。"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到指定檔案: {file_path}")
    result = _empty_result()
    for stem, data in _iter_sources(path):
        text, encoding = decode_html_bytes(data)
        _parse_html_into(result, text, source_label(stem), encoding)
    _dedupe(result)
    return result


parse_file = parse_path  # 舊名稱相容


# --------------------------------------------------------------------------- #
# 摘要與輸出
# --------------------------------------------------------------------------- #


def build_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    counts = {c: len(result[c]) for c in CATEGORIES}
    detail = {
        "outpatient_orders": sum(len(r.get("orders", [])) for r in result["outpatient"]),
        "medication_drugs": sum(len(r.get("drugs", [])) for r in result["medications"]),
        "inpatient_orders": sum(len(r.get("orders", [])) for r in result["inpatient"]),
        "surgery_orders": sum(len(r.get("orders", [])) for r in result["surgery"]),
        "generic_rows": sum(len(t["rows"]) for t in result["generic"]),
    }
    ranges: Dict[str, Dict[str, str]] = {}
    for cat, keys in _DATE_KEYS.items():
        values = []
        for rec in result[cat]:
            for key in keys:
                v = rec.get(key)
                if v:
                    values.append(v[:10])
                    break
        if values:
            ranges[cat] = {"min": min(values), "max": max(values)}
    meta = result["metadata"]
    return {
        "anonymized": True,
        "parser_version": PARSER_VERSION,
        "counts": counts,
        "detail_counts": detail,
        "date_ranges": ranges,
        "diagnostics": meta["diagnostics"],
        "files": meta["files"],
    }


def find_git_root(path: Any) -> Optional[Path]:
    """自 path 往上找 .git（目錄或 worktree 檔案）；找不到回 None。"""
    p = Path(path).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def write_outputs(result: Dict[str, Any], out_dir: Any) -> List[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    def dump(name: str, payload: Any) -> None:
        target = out / name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(target)

    for cat in CATEGORIES:
        if result[cat]:
            dump(f"{cat}.json", result[cat])
        else:
            # 這次沒有此類資料：移除上一輪留下的同名輸出，否則會被誤認為本次結果。
            # 只動本工具自己產生的固定檔名，資料夾裡的其他檔案不碰。
            (out / f"{cat}.json").unlink(missing_ok=True)
    dump("summary.json", build_summary(result))
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower() != "utf-8":
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        prog="health_bank_parser.py",
        description="健康存摺離線脫敏與結構化解析：每個類別輸出一個 JSON，另有 summary.json。",
    )
    ap.add_argument("input", help="資料夾、單一 HTML 檔，或 ZIP 檔")
    ap.add_argument("out_dir", help="輸出資料夾（不可位於 git 工作樹內）")
    ap.add_argument(
        "--allow-in-repo",
        action="store_true",
        help="允許 out_dir 位於 git 工作樹內（預設拒絕，避免真實資料進入倉庫）",
    )
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    if out_dir.exists() and not out_dir.is_dir():
        print(f"錯誤：out_dir 必須是資料夾：{out_dir}", file=sys.stderr)
        return 2
    root = find_git_root(out_dir)
    if root is not None and not args.allow_in_repo:
        print(
            f"拒絕輸出：out_dir 位於 git 工作樹內（{root}）。真實健康資料不得放進倉庫；"
            "請改用倉庫外的資料夾，或明確加上 --allow-in-repo。",
            file=sys.stderr,
        )
        return 2

    try:
        result = parse_path(args.input)
    except (OSError, ValueError) as e:  # FileNotFoundError 是 OSError；EncryptedArchiveError 是 ValueError
        print(f"錯誤：{e}", file=sys.stderr)
        return 2

    try:
        written = write_outputs(result, out_dir)
    except OSError as e:
        print(f"錯誤：無法寫入輸出資料夾 {out_dir}：{e}", file=sys.stderr)
        return 2
    summary = build_summary(result)
    print(f"脫敏解析完成：已寫入 {len(written)} 個檔案至 {out_dir}")
    for cat, n in summary["counts"].items():
        if n:
            print(f"  {cat}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
