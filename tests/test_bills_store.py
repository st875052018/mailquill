from mailquill.bill_due import BillDue
from mailquill.bills_store import append_bills, read_bills


def _bill(bank="HSBC", period="2026-08", amount_due="19711", min_due="2233", msg_id="m1"):
    return BillDue(bank=bank, period=period, amount_due=amount_due, min_due=min_due,
                   source_msg_id=msg_id)


def test_read_bills_missing_file_returns_empty(tmp_path):
    assert read_bills(str(tmp_path / "bills.csv")) == []


def test_append_bills_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "bills.csv")
    append_bills(path, [_bill()])
    bills = read_bills(path)
    assert len(bills) == 1
    assert bills[0] == _bill()


def test_append_bills_with_empty_list_does_not_create_file(tmp_path):
    path = str(tmp_path / "bills.csv")
    append_bills(path, [])
    assert read_bills(path) == []


def test_append_bills_same_period_overwrites_not_duplicates(tmp_path):
    path = str(tmp_path / "bills.csv")
    append_bills(path, [_bill(amount_due="19711")])
    append_bills(path, [_bill(amount_due="20000", msg_id="m2")])
    bills = read_bills(path)
    assert len(bills) == 1
    assert bills[0].amount_due == "20000"
    assert bills[0].source_msg_id == "m2"


def test_append_bills_different_periods_both_kept(tmp_path):
    path = str(tmp_path / "bills.csv")
    append_bills(path, [_bill(period="2026-07"), _bill(period="2026-08")])
    bills = read_bills(path)
    assert {b.period for b in bills} == {"2026-07", "2026-08"}


def test_append_bills_different_banks_both_kept(tmp_path):
    path = str(tmp_path / "bills.csv")
    append_bills(path, [_bill(bank="HSBC"), _bill(bank="CTBC")])
    bills = read_bills(path)
    assert {b.bank for b in bills} == {"HSBC", "CTBC"}
