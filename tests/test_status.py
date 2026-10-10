"""`mailquill status`：各銀行最新帳單／交易同步到哪裡。合成資料，不打網路。"""
from mailquill.bill_due import BillDue
from mailquill.bills_store import append_bills
from mailquill.schema import Transaction, make_txn_id
from mailquill.status import bank_statuses, format_status
from mailquill.store import append_transactions


def _txn(bank, date, source_type="pdf", last4="1234", imported_at="2026-09-20T10:00:00"):
    return Transaction(
        txn_id=make_txn_id(bank, last4, date, "100.00", f"{bank}{date}{source_type}"),
        date=date, post_date="", amount="100.00", currency="TWD",
        merchant_raw="x", merchant_norm="x", category_l1="Food", category_l2="",
        bank=bank, account_last4=last4, source_type=source_type, source_msg_id="m",
        raw_ref="", imported_at=imported_at,
    )


def _bill(bank, period):
    return BillDue(bank=bank, period=period, amount_due="100", min_due="", source_msg_id="m")


TXNS = [
    _txn("Cathay", "2026-09-14"),
    _txn("Cathay", "2026-09-27", source_type="email_body", imported_at="2026-09-30T22:00:00"),
    _txn("Fubon", "2026-07-20"),
    _txn("Fubon", "2026-09-29", source_type="email_body"),
    _txn("Amex", "2026-07-27", source_type="csv"),
]
BILLS = [_bill("Cathay", "2026-08"), _bill("Cathay", "2026-09"), _bill("Fubon", "2026-08")]


def _by_bank(statuses):
    return {s.bank: s for s in statuses}


def test_bank_statuses_uses_latest_bill_period_and_splits_sources():
    s = _by_bank(bank_statuses(TXNS, BILLS, today="2026-09-30"))
    cathay = s["Cathay"]
    assert cathay.statement_month == "2026-09" and not cathay.estimated
    assert cathay.statement_last_date == "2026-09-14"
    assert cathay.notice_last_date == "2026-09-27"
    assert cathay.last_imported == "2026-09-30T22:00:00"
    assert cathay.months_behind == 0


def test_bank_statuses_flags_bank_whose_bill_for_this_month_is_missing():
    fubon = _by_bank(bank_statuses(TXNS, BILLS, today="2026-09-30"))["Fubon"]
    assert fubon.statement_month == "2026-08"
    assert fubon.months_behind == 1
    assert fubon.notice_last_date == "2026-09-29"   # 每日通知有進來，但月結單沒有


def test_bank_statuses_estimates_from_statement_txns_when_no_bill_data():
    amex = _by_bank(bank_statuses(TXNS, BILLS, today="2026-09-30"))["Amex"]
    assert amex.statement_month == "2026-07" and amex.estimated
    assert amex.months_behind == 2
    assert amex.notice_last_date is None


def test_bank_statuses_bank_with_only_notices_has_no_statement():
    s = bank_statuses([_txn("Sinopac", "2026-09-01", source_type="email_body")], [],
                      today="2026-09-30")
    assert s[0].statement_month is None and s[0].months_behind is None


def test_format_status_shows_marks_and_hint_when_behind():
    out = format_status(bank_statuses(TXNS, BILLS, today="2026-09-30"), today="2026-09-30")
    lines = out.splitlines()
    cathay = next(l for l in lines if l.startswith("Cathay"))
    fubon = next(l for l in lines if l.startswith("Fubon"))
    amex = next(l for l in lines if l.startswith("Amex"))
    assert "✅" in cathay
    assert "⚠️" in fubon
    assert "❌" in amex and "2026-07*" in amex
    assert "mailquill ingest" in out   # 有落後時提示怎麼補


def test_format_status_no_hint_when_all_up_to_date():
    txns = [_txn("Cathay", "2026-09-14")]
    out = format_status(bank_statuses(txns, [_bill("Cathay", "2026-09")], today="2026-09-30"),
                        today="2026-09-30")
    assert "mailquill ingest" not in out
