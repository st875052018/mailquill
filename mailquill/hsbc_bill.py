"""從 HSBC 電子帳單信附帶的超商繳費條碼 HTML（`eBarCodeFullPay.html`／
`eBarCodeMinPay.html`）抓「應繳總額」／「最低應繳」／帳單付款月份。

這兩個附件是純文字 HTML，不像 PDF 帳單彙總頁（`hsbc_ocr_text.py` 開頭
說明過：中文標籤同樣是內嵌小圖，`extract_words` 抽不到）需要 OCR，格式
固定、100% 可靠：
    <div class="amount">應繳總額：19,711</div>          ← FullPay
    <div class="amount">最低應繳：2,233</div>              ← MinPay
    ... 付款月份 2026年08月 ...                            ← 兩個檔案都有

沒有 FullPay 附件（非帳單信，或本期無應繳金額不會產生條碼）就回傳 None，
不當成解析失敗；MinPay 附件缺漏或抓不到「最低應繳」只讓 `min_due` 留空，
不影響 `amount_due`。
"""
from __future__ import annotations

import re

from mailquill.bill_due import BillDue
from mailquill.gmail_message import Attachment, EmailMessage

_AMOUNT_DUE = re.compile(r"應繳總額[：:]\s*([\d,]+)")
_MIN_DUE = re.compile(r"最低應繳[：:]\s*([\d,]+)")
_PERIOD = re.compile(r"付款月份\s*(\d{4})年(\d{1,2})月")


def _find_html(attachments: list[Attachment], needle: str) -> str | None:
    for att in attachments:
        if needle.lower() in att.filename.lower():
            return att.data.decode("utf-8", errors="replace")
    return None


def extract_hsbc_bill_due(msg: EmailMessage) -> BillDue | None:
    full_html = _find_html(msg.attachments, "FullPay")
    if full_html is None:
        return None
    m_amount = _AMOUNT_DUE.search(full_html)
    m_period = _PERIOD.search(full_html)
    if not m_amount or not m_period:
        return None

    min_due = ""
    min_html = _find_html(msg.attachments, "MinPay")
    if min_html:
        m_min = _MIN_DUE.search(min_html)
        if m_min:
            min_due = m_min.group(1).replace(",", "")

    year, month = m_period.groups()
    return BillDue(
        bank="HSBC",
        period=f"{year}-{int(month):02d}",
        amount_due=m_amount.group(1).replace(",", ""),
        min_due=min_due,
        source_msg_id=msg.msg_id,
    )
