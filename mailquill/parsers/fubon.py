"""台北富邦銀行（Taipei Fubon）信用卡電子帳單 parser。

明細欄序：消費日期 消費說明 入帳日期 [外幣折算日/幣別 外幣金額/消費地] 台幣金額
例：  115/01/22 某商店股份有限公司 115/01/26 TWD 350
      115/02/13 掛失費 123456******7890 200   （費用行，無入帳日）

特點：
- 兩個完整民國日期「不相鄰」：消費日期在行首，入帳日期夾在描述之後。
- 卡號末四碼在卡別標題行「…正卡末４碼9999」（含全形數字）。
- 台幣金額是行末數字。描述取「消費日期」之後、「入帳日期」之前的字串；
  若無入帳日(如掛失費)則剝除行末的遮罩卡號/幣別等。
- 只計卡別標題行之後的消費；卡別前的「自動扣繳/悠遊卡轉置」屬繳款調整，略過。

房租轉帳通知信（同一寄件網域，另一種信件）：
- 主旨「台北富邦行動銀行臺幣轉帳交易「成功」通知」，內文只有 HTML（無 text/plain），
  金額/手續費標籤在 <th>、值在相鄰 <td>，其餘欄位是「標籤+值」同一個 <td>，
  例如用 `html_td_cells` 攤平後依序看到：
    '交易時間 2026/06/30 18:11:50'
    '轉出帳號 七賢分行 816800****6040 即時餘額 $73,892'
    'TWD 35,000'                                        （緊接著的轉出金額值）
    '轉入帳號 008(某銀行)-00001234****5678 某某人'
    'TWD 0'                                              （緊接著的手續費值）
- 只認 config.yaml 的 `fubon_rent_dest_signature` 指定的單一收款帳戶（例：
  ["008(華南銀行)", "1234"]，每個片段都要出現在信件原始 HTML 才算命中，不受
  標籤拆分影響），避免把自己帳戶互轉或轉給其他人的紀錄也當成交易；沒有設定
  就停用這個分支。符合就直接回傳一筆房租交易，不進入信用卡帳單解析。

本期應繳總額／最低應繳／帳單年月（月結單 PDF）：
PDF 是真文字（不像 HSBC/CTBC 需要 OCR 或猜順序），彙總頁固定有三行乾淨
可直接比對的原文：
    本期應繳總額 2,369元
    帳單年月 信用額度 國內預借現金額度 帳單結帳日 繳款截止日 …
    115/07 150,000 15,000 115/07/24 115/08/09 …
    本期 □繳交最低應繳金額 NT$800元
見 `_TOTAL_DUE`/`_MIN_DUE`/`_PERIOD`。房租轉帳通知信沒有這些欄位，
`parse_bill` 對它一律回 None（三個 pattern 都比對不到）。
"""
from __future__ import annotations

import re

from mailquill.bill_due import BillDue
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_date_to_iso, roc_to_ad, html_td_cells

_TOTAL_DUE = re.compile(r"本期應繳總額\s*([\d,]+)元")
_MIN_DUE = re.compile(r"最低應繳金額\s*NT\$([\d,]+)元")
_PERIOD = re.compile(r"帳單年月[^\n]*\n\s*(\d{3})/(\d{2})")

_TXN_DATE = re.compile(r"^(\d{3})/(\d{2})/(\d{2})\s+(.+)$")
_ROC = re.compile(r"(\d{3})/(\d{2})/(\d{2})")
_CARD = re.compile(r"末.?碼\s*(\d{4})")          # 「正卡末４碼9999」
_AMOUNT = re.compile(r"-?[\d,]+")
_MASKED = re.compile(r"[\d*]+\*+[\d*]+")          # 遮罩卡號 123456******7890
_TAIL = re.compile(r"[A-Z]{2,3}|\d+\.\d{2}")      # 幣別/消費地/外幣金額

_RENT_TIME = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_MASKED_TAIL = re.compile(r"\*+(\d{4})")          # 遮罩帳號末四碼 816800****6040


class FubonParser(Parser):
    bank = "Fubon"

    def __init__(self, rent_dest_signature: tuple[str, ...] = ()):
        self._rent_dest_signature = tuple(rent_dest_signature)

    def matches(self, msg: EmailMessage) -> bool:
        return "taipeifubon.com.tw" in msg.sender

    def _parse_rent_transfer(self, msg: EmailMessage) -> list[RawTxn]:
        cells = html_td_cells(msg.body_text)
        src_idx = next((i for i, c in enumerate(cells) if c.startswith("轉出帳號")), None)
        time_cell = next((c for c in cells if c.startswith("交易時間")), None)
        if src_idx is None or time_cell is None or src_idx + 1 >= len(cells):
            return []
        time_m = _RENT_TIME.search(time_cell)
        amount_m = _AMOUNT.search(cells[src_idx + 1])
        if not time_m or not amount_m:
            return []
        src_m = _MASKED_TAIL.search(cells[src_idx])
        y, m, d = time_m.groups()
        return [RawTxn(
            bank=self.bank,
            date=f"{y}/{m}/{d}",
            amount=amount_m.group(0),
            merchant_raw="轉帳房租",
            account_last4=src_m.group(1) if src_m else "",
            currency="TWD",
        )]

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        sig = self._rent_dest_signature
        if sig and all(s in msg.body_text for s in sig):
            return self._parse_rent_transfer(msg)

        blocks = [msg.body_text] + list(pdf_texts)
        txns: list[RawTxn] = []
        last4 = ""
        for block in blocks:
            for line in block.splitlines():
                s = line.strip()
                m = _TXN_DATE.match(s)
                if not m:
                    card = _CARD.search(s)
                    if card:
                        last4 = card.group(1)
                    continue
                if not last4:        # 卡別標題行之前的繳款調整，略過
                    continue
                cy, cm, cd, rest = m.groups()
                tokens = rest.split()
                if not tokens or not _AMOUNT.fullmatch(tokens[-1]):
                    continue
                amount = tokens[-1]
                middle = tokens[:-1]

                post = ""
                desc_tokens = middle
                for i, t in enumerate(middle):
                    d = _ROC.fullmatch(t)
                    if d:
                        post = roc_date_to_iso(d.group(1), d.group(2), d.group(3))
                        desc_tokens = middle[:i]
                        break
                else:
                    while desc_tokens and (_MASKED.fullmatch(desc_tokens[-1])
                                           or _TAIL.fullmatch(desc_tokens[-1])):
                        desc_tokens = desc_tokens[:-1]

                merchant = " ".join(desc_tokens).strip()
                if not merchant:
                    continue
                txns.append(RawTxn(
                    bank=self.bank,
                    date=roc_date_to_iso(cy, cm, cd),       # 消費日期
                    post_date=post,                          # 入帳日期
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=last4,
                    currency="TWD",
                ))
        return txns

    def parse_bill(self, msg: EmailMessage, pdf_texts: list[str]) -> BillDue | None:
        text = "\n".join(pdf_texts)
        m_total = _TOTAL_DUE.search(text)
        period = _PERIOD.search(text)
        if not m_total or not period:
            return None
        m_min = _MIN_DUE.search(text)
        year = roc_to_ad(int(period.group(1)))
        month = int(period.group(2))
        return BillDue(
            bank=self.bank,
            period=f"{year}-{month:02d}",
            amount_due=m_total.group(1).replace(",", ""),
            min_due=m_min.group(1).replace(",", "") if m_min else "",
            source_msg_id=msg.msg_id,
        )
