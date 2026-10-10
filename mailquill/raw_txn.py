"""parser 的輸出：正規化前的原始交易。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawTxn:
    bank: str
    date: str
    amount: str
    merchant_raw: str
    currency: str = "TWD"
    post_date: str = ""
    account_last4: str = ""
    source_category: str = ""  # 來源自帶的分類（如 Amex CSV 的 Category），純暫存不落地
