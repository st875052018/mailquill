"""CTBC CSV parser 測試。樣本為去識別化的合成資料，非真實帳單。"""
from mailquill.ctbc_csv import parse_ctbc_csv

PREAMBLE = "帳單明細查詢\n資料時間：2026/08/17 23:01:13,單位：元\n\n"
HEADER = "消費日,入帳起息日,摘要,幣別,消費地金額,外幣折算日,消費地,末四碼,新臺幣金額\n"


def _text(*rows: str) -> str:
    return PREAMBLE + HEADER + "".join(rows)


def test_parses_purchase_row():
    text = _text('2026/06/18,2026/06/22,連支＊統一超商,,,,TW,9646,75\n')
    txns = parse_ctbc_csv(text)
    assert len(txns) == 1
    t = txns[0]
    assert t.bank == "CTBC"
    assert t.date == "2026-06-18"
    assert t.post_date == "2026-06-22"
    assert t.amount == "75"
    assert t.currency == "TWD"
    assert t.account_last4 == "9646"
    assert t.merchant_raw == "連支＊統一超商"


def test_parses_foreign_currency_row_using_twd_settlement_amount():
    text = _text(
        '2026/05/19,2026/05/22,TEST HOTEL,USD,100.00,2026/05/20,US,1518,"3,162"\n'
    )
    txns = parse_ctbc_csv(text)
    assert len(txns) == 1
    t = txns[0]
    assert t.amount == "3,162"
    assert t.currency == "TWD"
    assert t.account_last4 == "1518"


def test_skips_bank_autodebit_payment_row():
    text = _text('2026/07/06,2026/07/07,本行扣繳　　　　　　　,,,,,,"-4,983"\n')
    assert parse_ctbc_csv(text) == []


def test_skips_cash_deposit_payment_row():
    text = _text('2026/06/03,2026/06/04,現金存款繳　　　　　　,,,,,,"-53,100"\n')
    assert parse_ctbc_csv(text) == []


def test_keeps_cashback_credit_row_with_account_last4():
    text = _text('2026/06/16,2026/06/17,現金回饋帳單抵用,,,,,7428,-517\n')
    txns = parse_ctbc_csv(text)
    assert len(txns) == 1
    assert txns[0].amount == "-517"
    assert txns[0].account_last4 == "7428"


def test_keeps_late_fee_row_without_account_last4():
    text = _text('2026/04/17,2026/04/17,逾期還款違約金,,,,,,300\n')
    txns = parse_ctbc_csv(text)
    assert len(txns) == 1
    assert txns[0].amount == "300"
    assert txns[0].account_last4 == ""


def test_ignores_unrelated_csv_without_matching_header():
    text = "foo,bar\n1,2\n"
    assert parse_ctbc_csv(text) == []
