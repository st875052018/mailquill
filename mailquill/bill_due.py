"""帳單「本期應繳/最低應繳」——跟逐筆消費(`RawTxn`)分開的帳單層級摘要，
一筆代表一家銀行某個帳單月份。由各 parser 的 `parse_bill` 擷取（Cathay／
Fubon／CTBC 讀 PDF 彙總頁，HSBC 見 `mailquill/hsbc_bill.py`）；沒有彙總
金額欄位的來源回傳 None，不強制每個 parser 都要有。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BillDue:
    bank: str
    period: str          # 帳單/付款月份 "YYYY-MM"
    amount_due: str       # 應繳總額（純數字字串，不含千分位逗號）
    min_due: str           # 最低應繳；抓不到就留空字串
    source_msg_id: str
