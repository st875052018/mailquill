"""中國信託（CTBC）信用卡電子帳單 parser（Gmail PDF，需先由 pdf_unlocker 解密）。

信件主旨「中國信託信用卡電子帳單 <民國年月>」，寄件者 estats.ctbcbank.com，
每月一封，附件 PDF 密碼為身分證字號。明細行格式：
    消費日(民國) 入帳起息日(民國) [商家] 金額 卡號末四碼 消費地
例：  115/07/15 115/07/20 eTag BJQ-3199 15 9646 TW
      115/07/16 115/07/20 190 9646 TW
      115/07/28 115/07/30 -1,476 9646 TW

兩個日期都是完整民國日期（YYY/MM/DD），不像 Cathay/SinoPac 只有 MM/DD，
故不需另外從信件主旨/內文找「帳單期別」推算年份。

只匹配「開頭兩個完整民國日期」的行，藉此跳過總表／繳款彙總列（如
「115/08/17 4,252 7,915 0 4,252 1,476 +6,439」「115/08/05 -4,252」）——
那些只有一個日期，不符合錨定格式。

⚠️ 商家名稱在此 PDF 裡多數是圖片（非可選取文字），pdfplumber 抽不出來
（只有少數英數字商家，如 eTag 電子標籤，殘留文字）。跟 HSBC 一樣，改由
`hsbc_ocr_text.extract_pdf_text_ocr`（`CTBC_LAYOUT`）逐列裁圖 OCR，把辨識
結果接在該列「消費地」之後，這裡再取回當 merchant_raw：
    115/07/16 115/07/20 190 9646 TW 連支＊統一超商
本來就有真文字商家的列（eTag）維持原值、忽略 OCR 結果，讓既有 txn_id
穩定不變。OCR 非 100% 準確（常混入雜訊字元），交給分類規則或手動修正；
完全辨識不出的列 merchant_raw 仍為空字串。官網手動匯出 CSV
（`ctbc_csv.py`）商家欄位完整，是更乾淨的來源。

⚠️ 因為 merchant_raw 常為空，這裡解出的交易與 CSV 手動匯入解出的同一筆
交易，merchant_raw 不同 → `make_txn_id` 算出的 txn_id 不同 → 不會自動去
重。若兩種來源都匯入同一期帳單，會重複計入該期消費，需自行擇一為主（例如
只用其中一種來源，或匯入後手動核對刪除重複列）。

## 本期應繳總額／最低應繳
彙總頁的中文標籤跟商家名稱一樣是圖片，`extract_pdf_text` 抽不到「應繳總額」
這幾個字，但兩個金額本身是真文字，緊跟在超商繳費條碼數字前面（版型固定，
跟 HSBC 附件 barcode HTML 的樣式異曲同工，只是這裡是同一份 PDF 內嵌），
例如：
    $ 6,439
    150918000006439( )
    501231C02( )
    $ 1,000
    1509BX000001000( )
兩個 `$金額` 依固定順序出現（應繳總額在前、最低應繳在後——標籤沒了但順序
沒變，且應繳總額必定 ≥ 最低應繳，見 `_BARCODE_AMOUNTS` 用法），少於兩筆或
金額大小顛倒就視為版型跑掉、回 None，不硬猜。帳單年月從信件主旨「中國信託
信用卡電子帳單 11508」的 5 碼（民國年3碼+月2碼）解出。
"""
from __future__ import annotations

import re

from mailquill.bill_due import BillDue
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_date_to_iso, roc_to_ad

# 錨定行：消費日(民國) 入帳起息日(民國) 其餘
_ANCHOR = re.compile(r"^(\d{3})/(\d{2})/(\d{2})\s+(\d{3})/(\d{2})/(\d{2})\s+(.+)$")
# 其餘 = [商家] 金額 卡號末四碼 消費地 [OCR商家]
# （商家可空——圖片型商家名抽不到文字，改看 OCR 接在消費地後面的結果）
_TAIL = re.compile(
    r"^(?:(?P<merchant>.*)\s+)?(?P<amount>-?[\d,]+)\s+(?P<last4>\d{4})\s+(?P<region>[A-Z]{2,3})"
    r"(?:\s+(?P<ocr>.+))?$"
)

_SUBJECT_PERIOD = re.compile(r"電子帳單\s*(\d{3})(\d{2})\b")
_BARCODE_AMOUNTS = re.compile(r"\$\s*([\d,]+)\s*\n\s*[\dA-Za-z]{8,20}\(")


class CtbcParser(Parser):
    bank = "CTBC"

    def matches(self, msg: EmailMessage) -> bool:
        return "estats.ctbcbank.com" in msg.sender

    def extract_pdf_text(self, pdf_bytes: bytes) -> str:
        import mailquill.hsbc_ocr_text as ocr
        return ocr.extract_pdf_text_ocr(pdf_bytes, layout=ocr.CTBC_LAYOUT)

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        txns: list[RawTxn] = []
        for block in blocks:
            for line in block.splitlines():
                anchor = _ANCHOR.match(line.strip())
                if not anchor:
                    continue
                cy, cm, cd, py, pm, pd, rest = anchor.groups()
                tail = _TAIL.match(rest.strip())
                if not tail:
                    continue
                merchant = (tail.group("merchant") or tail.group("ocr") or "").strip()
                txns.append(RawTxn(
                    bank=self.bank,
                    date=roc_date_to_iso(cy, cm, cd),
                    post_date=roc_date_to_iso(py, pm, pd),
                    amount=tail.group("amount"),
                    merchant_raw=merchant,
                    account_last4=tail.group("last4"),
                    currency="TWD",
                ))
        return txns

    def parse_bill(self, msg: EmailMessage, pdf_texts: list[str]) -> BillDue | None:
        period = _SUBJECT_PERIOD.search(msg.subject or "")
        if not period:
            return None
        amounts = _BARCODE_AMOUNTS.findall("\n".join(pdf_texts))
        if len(amounts) < 2:
            return None
        total, minimum = amounts[0].replace(",", ""), amounts[1].replace(",", "")
        if int(total) < int(minimum):
            return None
        year = roc_to_ad(int(period.group(1)))
        month = int(period.group(2))
        return BillDue(
            bank=self.bank,
            period=f"{year}-{month:02d}",
            amount_due=total,
            min_due=minimum,
            source_msg_id=msg.msg_id,
        )
