"""滙豐(台灣) 信用卡電子帳單 parser（如「旅人無限卡」）。

商家名稱整欄是內嵌小圖（無 ToUnicode 對映，無法直接抽字），`extract_pdf_text`
覆寫成呼叫 `mailquill.hsbc_ocr_text.extract_pdf_text_ocr`，逐列裁圖 OCR 後把
商家名稱接在該列文字後面，`parse` 收到的仍是一般文字、可直接用正則比對。

明細分兩種列（消費日 入帳起息日 開頭，其後版型不同）：
    海外原始消費：消費日 入帳起息日 國別(3碼) 幣別(3碼) 外幣金額 折算日 臺幣金額 [OCR商家]
        例：07/19 07/22 JPN JPY 2,112.00 07/19 422 SEVEN-ELEVEN
    國外交易手續費：消費日 入帳起息日 臺幣金額 國外交易手續費[OCR商家]
        例：07/19 07/22 6 國外交易手續費SEVEN-ELEVEN
    （兩者金額加總＝當期「消費/新增款項/…小計」，手續費視為獨立一筆消費）
其餘列（還款/轉帳、頁首頁尾、其他標籤文字）兩種正則都比對不到，自然略過，
不用另外寫「繳」關鍵字排除。

商家名稱來自 OCR，非 100% 準確——海外消費列商家欄常混入國旗小圖示被誤判
成的雜訊字元（如開頭多出 "APE7844" 之類前綴），這是已知限制，不在此清洗
（清不乾淨還可能誤刪真商家名），交給分類規則或手動修正；OCR 完全辨識不出
文字時商家留空，回退機制與 Cathay 空白商家一致（落 Uncategorized 待手動）。

年份取自信件主旨／內文／PDF 的帳單期別（民國「115年08月」、民國全日期或
西元），月份大於帳單月者視為前一年（跨年）；無期別線索則回空清單。

本期應繳總額／最低應繳不在這裡抓（PDF 彙總頁的中文標籤也是內嵌小圖，
OCR 不到）——改由 `parse_bill` 讀同一封信附帶的超商繳費條碼 HTML
（`eBarCodeFullPay.html`／`eBarCodeMinPay.html`，純文字、格式固定），
見 `mailquill/hsbc_bill.py`。
"""
from __future__ import annotations

import re

from mailquill.bill_due import BillDue
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_to_ad, md_to_date

_FEE = re.compile(
    r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(-?[\d,]+)\s+國外交易手續費(.*)$"
)
_DETAIL = re.compile(
    r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+[A-Z]{3}\s+[A-Z]{3}\s+[\d,]+\.\d{2}\s+"
    r"\d{2}/\d{2}\s+(-?[\d,]+)(?:\s+(.*))?$"
)

_ROC_PERIOD = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月")
_ROC_FULL = re.compile(r"(\d{3})/(\d{2})/(\d{2})")
_AD_PERIOD = re.compile(r"(20\d{2})\s*[/年-]\s*(\d{1,2})")


class HSBCParser(Parser):
    bank = "HSBC"

    def matches(self, msg: EmailMessage) -> bool:
        return "hsbc.com.tw" in msg.sender

    def extract_pdf_text(self, pdf_bytes: bytes) -> str:
        from mailquill.hsbc_ocr_text import extract_pdf_text_ocr
        return extract_pdf_text_ocr(pdf_bytes)

    def parse_bill(self, msg: EmailMessage, pdf_texts: list[str]) -> BillDue | None:
        from mailquill.hsbc_bill import extract_hsbc_bill_due
        return extract_hsbc_bill_due(msg)

    def _period(self, text: str) -> tuple[int, int] | None:
        m = _ROC_PERIOD.search(text)
        if m:
            return roc_to_ad(int(m.group(1))), int(m.group(2))
        m = _ROC_FULL.search(text)
        if m:
            return roc_to_ad(int(m.group(1))), int(m.group(2))
        m = _AD_PERIOD.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        period = self._period("\n".join([msg.subject or ""] + blocks))
        if not period:
            return []
        stmt_year, stmt_month = period

        txns: list[RawTxn] = []
        for block in blocks:
            for line in block.splitlines():
                s = line.strip()
                m = _DETAIL.match(s)
                if m:
                    cmm, cdd, pmm, pdd, amount, merchant = m.groups()
                    txns.append(RawTxn(
                        bank=self.bank,
                        date=md_to_date(cmm, cdd, stmt_year, stmt_month),
                        post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),
                        amount=amount,
                        merchant_raw=(merchant or "").strip(),
                        currency="TWD",
                    ))
                    continue
                m = _FEE.match(s)
                if m:
                    cmm, cdd, pmm, pdd, amount, merchant = m.groups()
                    txns.append(RawTxn(
                        bank=self.bank,
                        date=md_to_date(cmm, cdd, stmt_year, stmt_month),
                        post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),
                        amount=amount,
                        merchant_raw=f"國外交易手續費{merchant.strip()}".strip(),
                        currency="TWD",
                    ))
        return txns
