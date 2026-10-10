"""Parser 基底類別。各家銀行繼承並實作 matches / parse。"""
from __future__ import annotations

from mailquill.bill_due import BillDue
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn


class Parser:
    bank: str = ""

    def matches(self, msg: EmailMessage) -> bool:
        raise NotImplementedError

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        """回傳此封信的原始交易。

        作者契約：`msg.body_text` 一定是 `str`（可能為空字串，不會是 None）；
        `pdf_texts` 是已解密 PDF 逐頁文字以換行串接後的字串清單；金額/日期可直接
        回傳原始字串，後續由 normalizer 清洗，無需在此正規化。
        """
        raise NotImplementedError

    def parse_bill(self, msg: EmailMessage, pdf_texts: list[str]) -> BillDue | None:
        """回傳這封信（或這份手動匯入的 PDF）附帶的帳單應繳金額摘要（見
        `BillDue`），非帳單信、或該銀行的信件/PDF 本身沒有這類彙總金額可抓
        就回傳 None。`pdf_texts` 跟 `parse` 收到的是同一份（呼叫端只解密一
        次），大多數銀行的應繳總額/最低應繳都印在 PDF 彙總頁，不在信件本文。
        預設不支援；Cathay／Fubon／CTBC 從 PDF 彙總頁抓，HSBC 改讀信件附帶
        的超商繳費條碼 HTML（見 `hsbc_bill.extract_hsbc_bill_due`，不用 `pdf_texts`）。
        """
        return None

    def extract_pdf_text(self, pdf_bytes: bytes) -> str:
        """從已解密的 PDF bytes 抽出 `parse` 要吃的文字。

        預設走純文字抽取；商家名稱整欄是圖片、需要 OCR 才能補回的銀行
        （如 HSBC）覆寫這個方法即可，`parse` 本身不用管抽取方式。
        """
        from mailquill.pdf_text import extract_pdf_text as _extract
        return _extract(pdf_bytes)
