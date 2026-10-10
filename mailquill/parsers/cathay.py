"""國泰世華（Cathay United Bank）信用卡 parser：月結單 + 每日消費彙整通知。

## 月結單（PDF，需先由 pdf_unlocker 解密）
明細行格式：
    消費日 入帳起息日 交易說明 新臺幣金額 卡號末4 [行動卡末4] 國別 幣別 [外幣金額 折算日]
例：  05/23 05/26 某商店 350 1234 TW TWD

日期為 MM/DD（無年份），由帳單期別「信用卡帳單 115年6月」補上民國→西元年份；
若某筆月份大於帳單月份（如 1 月帳單出現 12 月消費），年份自動減一年。
沒有卡號末4的行（繳款、上期帳單總額等）會被略過——只取真正的刷卡消費。

## 每日消費彙整通知（HTML 信件內文，主旨「國泰世華銀行消費彙整通知」）
月結單每月才寄一次；這封信授權後最遲兩個工作日內寄達，可做到接近即時的入帳，
故額外支援。信件本文沒有 text/plain 版本，只有 HTML，靠 `html_td_cells` 攤平
成 cell 序列後，用兩組固定表頭字串比對取得資料列（見 `_AUTH_HEADER`/
`_AMOUNT_HEADER`），兩組列依出現順序配對成一筆交易。

跟月結單的已知落差（暫不處理，等之後對帳/合併時再議）：
- 「商店名稱」只有本行簽約特約商店才會顯示，否則是空字串，需等月結單補上
  真正店名——同一筆消費在這裡與月結單會有不同的 merchant_raw，因此
  `make_txn_id` 也會不同，兩邊各自算一筆，不會自動去重。
- 金額是授權金額，外幣消費的實際請款金額可能因匯率認列時點而與月結單略有出入。

## 本期應繳總額／最低應繳（月結單 PDF）
月結單 PDF 是真文字（非圖片），彙總頁有乾淨的「本期應繳總額 47,338」單行
可直接比對；最低應繳沒有同樣乾淨的單行，改抓超商繳費聯上「$4,734元」這種
「金額緊接在最低應繳金額欄位前」的樣式（整份文件只會出現一次，見
`_MIN_DUE`）。每日消費彙整通知信沒有這兩個欄位，`parse_bill` 對它一律回
None（`_PERIOD_RE`/`_TOTAL_DUE` 比對不到）。
"""
from __future__ import annotations

import re

from mailquill.bill_due import BillDue
from mailquill.gmail_message import EmailMessage
from mailquill.raw_txn import RawTxn
from mailquill.parsers.base import Parser
from mailquill.parsers._util import roc_to_ad, md_to_date, html_td_cells

# 帳單期別：信用卡帳單 115年6月
_PERIOD_RE = re.compile(r"信用卡帳單\s*(\d{2,3})\s*年\s*(\d{1,2})\s*月")
# 明細行開頭：消費日 MM/DD 入帳起息日 MM/DD 其餘
_TXN_RE = re.compile(r"^(\d{2})/(\d{2})\s+(\d{2})/(\d{2})\s+(.+)$")
_FOUR = re.compile(r"\d{4}")
_AMOUNT = re.compile(r"-?[\d,]+")

# 每日消費彙整通知：卡號後4碼： 3070
_ACCOUNT_RE = re.compile(r"卡號後4碼[：:]\s*(\d{4})")
_AUTH_HEADER = ["卡別", "行動卡號後4碼", "授權日期", "授權時間", "消費地區"]
_AMOUNT_HEADER = ["消費金額", "商店名稱", "消費類別", "備註"]

_TOTAL_DUE = re.compile(r"本期應繳總額\s*([\d,]+)")
_MIN_DUE = re.compile(r"\$([\d,]+)元")


class CathayParser(Parser):
    bank = "Cathay"

    def matches(self, msg: EmailMessage) -> bool:
        return "cathaybk.com.tw" in msg.sender

    def parse(self, msg: EmailMessage, pdf_texts: list[str]) -> list[RawTxn]:
        blocks = [msg.body_text] + list(pdf_texts)
        period = _PERIOD_RE.search("\n".join(blocks))
        if not period:
            return self._parse_daily_notice(msg.body_text)
        stmt_year = roc_to_ad(int(period.group(1)))
        stmt_month = int(period.group(2))

        txns: list[RawTxn] = []
        for block in blocks:
            for line in block.splitlines():
                m = _TXN_RE.match(line.strip())
                if not m:
                    continue
                tmm, tdd, pmm, pdd, rest = m.groups()
                tokens = rest.split()
                # 第一個剛好四位數字的 token 視為卡號末四碼（金額 ≥1000 帶千分位逗號，
                # <1000 為 ≤3 位，皆不會是純四位數，故不會誤判）
                idx = next((i for i, t in enumerate(tokens)
                            if _FOUR.fullmatch(t)), None)
                if idx is None or idx == 0:
                    continue
                amount = tokens[idx - 1]
                if not _AMOUNT.fullmatch(amount):
                    continue
                merchant = " ".join(tokens[:idx - 1]).strip()
                if not merchant:
                    continue
                txns.append(RawTxn(
                    bank=self.bank,
                    date=md_to_date(tmm, tdd, stmt_year, stmt_month),
                    post_date=md_to_date(pmm, pdd, stmt_year, stmt_month),
                    amount=amount,
                    merchant_raw=merchant,
                    account_last4=tokens[idx],
                    currency="TWD",
                ))
        return txns

    def parse_bill(self, msg: EmailMessage, pdf_texts: list[str]) -> BillDue | None:
        text = "\n".join(pdf_texts)
        period = _PERIOD_RE.search("\n".join([msg.subject or "", text]))
        m_total = _TOTAL_DUE.search(text)
        if not period or not m_total:
            return None
        stmt_year = roc_to_ad(int(period.group(1)))
        stmt_month = int(period.group(2))
        m_min = _MIN_DUE.search(text)
        return BillDue(
            bank=self.bank,
            period=f"{stmt_year}-{stmt_month:02d}",
            amount_due=m_total.group(1).replace(",", ""),
            min_due=m_min.group(1).replace(",", "") if m_min else "",
            source_msg_id=msg.msg_id,
        )

    def _parse_daily_notice(self, body_html: str) -> list[RawTxn]:
        account_match = _ACCOUNT_RE.search(re.sub(r"<[^>]+>", " ", body_html))
        account_last4 = account_match.group(1) if account_match else ""

        cells = html_td_cells(body_html)
        auth_rows = _extract_rows(cells, _AUTH_HEADER)
        amount_rows = _extract_rows(cells, _AMOUNT_HEADER)

        txns: list[RawTxn] = []
        for (_card_type, _mobile_last4, date_raw, _time_raw, _region), \
                (amount_raw, merchant, category, _remark) in zip(auth_rows, amount_rows):
            txns.append(RawTxn(
                bank=self.bank,
                date=date_raw,
                amount=amount_raw,
                merchant_raw=merchant,
                account_last4=account_last4,
                currency="TWD",
                source_category=category,
            ))
        return txns


def _extract_rows(cells: list[str], header: list[str]) -> list[list[str]]:
    """在 cell 序列裡找出所有等於 header 的片段，回傳緊接其後同長度的資料列。"""
    n = len(header)
    rows = []
    i = 0
    while i <= len(cells) - n:
        if cells[i:i + n] == header:
            data = cells[i + n:i + 2 * n]
            if len(data) == n:
                rows.append(data)
            i += 2 * n
        else:
            i += 1
    return rows
