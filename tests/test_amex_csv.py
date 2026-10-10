"""Amex CSV parser 測試。樣本為去識別化的合成資料，非真實帳單。"""
from mailquill.amex_csv import parse_amex_csv

HEADER = (
    "Date,Description,Card Member,Account #,Amount,Extended Details,"
    "Appears On Your Statement As,Address,City/State,Zip Code,Country,"
    "Reference,Category\n"
)


def _row(date, desc, account, amount, category):
    return f'{date},{desc},JANE DOE,{account},{amount},"",{desc},,,,,,{category}\n'


def test_parses_purchase_row():
    text = HEADER + _row("07/27/2026", "TEST MERCHANT      SEATTLE  WA",
                         "-73006", "15.50", "Travel-Airline")
    txns = parse_amex_csv(text)
    assert len(txns) == 1
    t = txns[0]
    assert t.bank == "Amex"
    assert t.date == "2026-07-27"
    assert t.amount == "15.50"
    assert t.currency == "USD"
    assert t.account_last4 == "73006"
    assert t.merchant_raw == "TEST MERCHANT SEATTLE WA"  # 內部多重空白被壓縮
    assert t.source_category == "Travel-Airline"


def test_skips_payment_row_with_empty_category():
    text = HEADER + _row("07/11/2026", "AUTOPAY PAYMENT - THANK YOU",
                         "-73006", "-213.10", "")
    txns = parse_amex_csv(text)
    assert txns == []


def test_keeps_refund_row_with_category():
    text = HEADER + _row("05/21/2026", "TEST STORE          CABAZON CA",
                         "-73006", "-25.00", "Merchandise & Supplies-Clothing Stores")
    txns = parse_amex_csv(text)
    assert len(txns) == 1
    assert txns[0].amount == "-25.00"


def test_ignores_unrelated_csv_without_matching_header():
    text = "foo,bar\n1,2\n"
    assert parse_amex_csv(text) == []
