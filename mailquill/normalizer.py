"""把 RawTxn 清洗、組成統一 schema 的 Transaction。"""
from __future__ import annotations

import re

from mailquill.raw_txn import RawTxn
from mailquill.schema import Transaction, make_txn_id

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def normalize_amount(raw: str) -> str:
    m = _AMOUNT_RE.search(raw or "")
    if not m:
        return ""
    return m.group(0).replace(",", "")


def normalize_date(raw: str) -> str:
    return (raw or "").strip().replace("/", "-").replace(".", "-")


def normalize(raw: RawTxn, msg_id: str, source_type: str,
              raw_ref: str, imported_at: str, *, seq: int = 0) -> Transaction:
    date = normalize_date(raw.date)
    post_date = normalize_date(raw.post_date)
    amount = normalize_amount(raw.amount)
    return Transaction(
        txn_id=make_txn_id(raw.bank, raw.account_last4, date, amount,
                           raw.merchant_raw, seq),
        date=date,
        post_date=post_date,
        amount=amount,
        currency=raw.currency,
        merchant_raw=raw.merchant_raw,
        merchant_norm=raw.merchant_raw.strip(),
        category_l1="Uncategorized",
        category_l2="",
        bank=raw.bank,
        account_last4=raw.account_last4,
        source_type=source_type,
        source_msg_id=msg_id,
        raw_ref=raw_ref,
        imported_at=imported_at,
        seq=str(seq),
    )
