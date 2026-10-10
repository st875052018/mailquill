"""永豐銀行（SinoPac / DAWHO 大戶）信用卡電子帳單 parser。

明細欄序（表頭橫跨數行）：
    消費日 入帳起息日 卡號末四碼 帳單說明 臺幣金額 [外幣折算日 外幣金額] [年百分率 分期未到期金額]
例：  05/06 05/22 9999 某產物保險股份有限公司 2,180
      05/16 05/19 9999 A- SOME NOODLE BANGKOK TH 166 05/16 THB170.00
      05/16 05/20 9999 SOME SHOP 國外交易服務費 4

特點：
- 每筆錨定行 = 「消費日 入帳起息日 卡號末四(4碼)」開頭；臺幣金額一定在錨定行。
- 臺幣金額 = 剝掉行尾外幣欄（如「05/15 THB472.00」）後的最後一個數字。
- 海外長商家名常被 PDF 抽字拆到相鄰行；錨定行本身沒有商家文字時，
  取「前一行碎片」補回（best-effort，金額一律正確、永不漏筆）。
- 商家前綴的「A- 」為帳務標記，一律剝除。
- 繳款行（如「永豐自扣已入帳，謝謝！」）第三欄非 4 碼卡號，不符錨定格式而略過。
- 年份取自信件主旨／內文／PDF 的帳單期別（民國「115年6月」、民國全日期或西元）；
  月份大於帳單月者視為前一年（跨年）。若完全無期別線索則回空清單（不臆測年份）。
"""
from __future__ import annotations

import re

from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_to_ad, md_to_date

_ANCHOR = re.compile(r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(\d{4})\s+(.*)$")
_FX_TAIL = re.compile(r"\s*\d{2}/\d{2}\s+[A-Z]{3}[\d,.]+\s*$")   # 行尾「05/15 THB472.00」
_NUM = re.compile(r"-?[\d,]+")
_A_PREFIX = re.compile(r"^A-\s*")

# 期別／年份錨點（優先序：民國期別 → 民國全日期 → 西元年月）
_ROC_PERIOD = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月")
_ROC_FULL = re.compile(r"(\d{3})/(\d{2})/(\d{2})")
_AD_PERIOD = re.compile(r"(20\d{2})\s*[/年-]\s*(\d{1,2})")


def _clean_merchant(s: str) -> str:
    return _A_PREFIX.sub("", s).strip()


class SinoPacParser(Parser):
    bank = "SinoPac"

    def matches(self, msg: EmailMessage) -> bool:
        return "sinopac.com" in msg.sender     # 涵蓋 banksinopac.com.tw

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
            return []                          # 無期別線索 → 不臆測年份
        stmt_year, stmt_month = period

        txns: list[RawTxn] = []
        pending = ""                           # 上一行非錨定碎片（可能是被拆開的商家名）
        for block in blocks:
            for line in block.splitlines():
                s = line.strip()
                m = _ANCHOR.match(s)
                if not m:
                    if s:
                        pending = s
                    continue
                cmm, cdd, pmm, pdd, last4, rest = m.groups()
                body = _FX_TAIL.sub("", rest)          # 剝行尾外幣欄
                nums = _NUM.findall(body)
                if not nums:
                    pending = ""
                    continue
                amount = nums[-1]
                merchant = _clean_merchant(body[:body.rfind(amount)])
                if not merchant:                       # 商家被拆到前一行
                    merchant = _clean_merchant(pending)
                pending = ""
                txns.append(RawTxn(
                    bank=self.bank,
                    date=md_to_date(cmm, cdd, stmt_year, stmt_month),       # 消費日
                    post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),  # 入帳起息日
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns
