"""Amex（American Express）信用卡消費明細 parser。

Amex 沒有像其他銀行一樣寄結構化電子帳單到 Gmail，只能到官網
Statements & Activity → Download 手動匯出 CSV（`mailquill ingest` 會自動
辨識 `.csv` 檔並用這裡的邏輯解析，PDF 走原本的 parser registry）。

CSV 每列一筆交易，關鍵欄位：Date（MM/DD/YYYY）、Description（商家，
固定寬度格式所以中間有大量空白）、Account #（如 "-73006"，前面的 "-"
只是遮罩符號不是負號）、Amount（正數＝消費，負數＝退款/信用）、
Category（Amex 自己分的類別，如 "Travel-Lodging"）。

`Category` 為空的列是「AUTOPAY/MOBILE PAYMENT - THANK YOU」之類的
繳款/自動扣款列（把錢從銀行帳戶轉去繳卡費，不是消費也不是收入），
略過不匯入；有 Category 的負數列（退款、信用調整）則保留，
交由 mailquill 既有的正/負金額慣例處理（負數＝deposit）。

原始 Category 字串會存進 RawTxn.source_category（純暫存，不落地存檔），
給 `amex_autocat.py` 用來自動產生 categories.yaml 的 keyword 規則——
大量一次性美國商家逐筆手寫規則不划算，改用 Amex 自己的分類當 fallback。
"""
from __future__ import annotations

import csv
import io

from mailquill.raw_txn import RawTxn

BANK = "Amex"

# Amex Category → mailquill (l1, l2)。沿用現有 categories.yaml 的 l1 分類。
# 沒列在這裡的 Category 維持現狀（掉回「Uncategorized」，不會比現在更差）。
AMEX_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "Travel-Lodging": ("Entertainment", "Lodging"),
    "Travel-Airline": ("Entertainment", "Travel"),
    "Travel-Travel Agencies": ("Entertainment", "Travel"),
    "Travel-Vehicle Rental": ("Transport", "CarRental"),
    "Restaurant-Restaurant": ("Food", "Dining"),
    "Restaurant-Bar & Café": ("Food", "Dining"),
    "Restaurant-Fast Food": ("Food", "Dining"),
    "Merchandise & Supplies-Groceries": ("Food", "Groceries"),
    "Merchandise & Supplies-Clothing Stores": ("Shopping", "Apparel"),
    "Merchandise & Supplies-Internet Purchase": ("Shopping", "Online"),
    "Merchandise & Supplies-Computer Supplies": ("Shopping", "Electronics"),
    "Merchandise & Supplies-Electronics": ("Shopping", "Electronics"),
    "Merchandise & Supplies-General Retail": ("Shopping", "Department"),
    "Merchandise & Supplies-Wholesale Stores": ("Shopping", "Department"),
    "Merchandise & Supplies-Department Stores": ("Shopping", "Department"),
    "Merchandise & Supplies-Florists & Garden": ("Shopping", "Department"),
    "Transportation-Fuel": ("Transport", "Gas"),
    "Transportation-Taxis & Coach": ("Transport", "Taxi"),
    "Transportation-Parking Charges": ("Transport", "Parking"),
    "Other-Charities": ("Donation", "Donation"),
    "Other-Education": ("Education", "School"),
    "Other-Government Services": ("Other", "GovFee"),
    "Communication-Mobile Phone": ("Housing", "Telecom"),
    "Fees & Adjustments-Fees & Adjustments": ("Other", "Fee"),
}


def _to_iso_date(mmddyyyy: str) -> str:
    mm, dd, yyyy = mmddyyyy.strip().split("/")
    return f"{yyyy}-{mm}-{dd}"


def parse_amex_csv(text: str) -> list[RawTxn]:
    reader = csv.DictReader(io.StringIO(text))
    out: list[RawTxn] = []
    for row in reader:
        date = (row.get("Date") or "").strip()
        amount = (row.get("Amount") or "").strip()
        if not date or not amount:
            continue
        category = (row.get("Category") or "").strip()
        if not category:
            continue  # 繳款/自動扣款列，非消費
        account_last4 = (row.get("Account #") or "").lstrip("-").strip()
        merchant = " ".join((row.get("Description") or "").split())
        out.append(RawTxn(
            bank=BANK,
            date=_to_iso_date(date),
            amount=amount,
            merchant_raw=merchant,
            currency="USD",
            account_last4=account_last4,
            source_category=category,
        ))
    return out
