"""parser 共用小工具：民國年/日期轉換、HTML 信件表格抽取。"""
from __future__ import annotations

import html
import re

_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_td_cells(raw_html: str) -> list[str]:
    """把 HTML 內所有 <td>...</td> 依文件順序攤平成純文字清單（去標籤、去實體、去多餘空白）。

    不追蹤 <tr>/<table> 巢狀關係——只取文件順序上的 cell 序列。表格化資料
    （如銀行通知信的明細表）本來就是逐列依序輸出，用序列比對已足夠找出目標欄位，
    不需要完整還原 DOM 結構。
    """
    cells = []
    for m in _TD_RE.finditer(raw_html):
        inner = _TAG_RE.sub(" ", m.group(1))
        inner = html.unescape(inner)
        cells.append(_WS_RE.sub(" ", inner).strip())
    return cells


def roc_to_ad(roc_year: int) -> int:
    """民國年 → 西元年（115 → 2026）。"""
    return roc_year + 1911


def md_to_date(mm: str, dd: str, stmt_year: int, stmt_month: int) -> str:
    """MM/DD（無年份）+ 帳單年/月 → YYYY-MM-DD；月份大於帳單月視為前一年（跨年）。"""
    year = stmt_year - 1 if int(mm) > stmt_month else stmt_year
    return f"{year}-{mm}-{dd}"


def roc_date_to_iso(roc_year: str, mm: str, dd: str) -> str:
    """完整民國日期（115/05/25）→ 西元 ISO（2026-05-25）。"""
    return f"{roc_to_ad(int(roc_year))}-{mm}-{dd}"
