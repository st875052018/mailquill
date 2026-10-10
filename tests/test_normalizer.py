from mailquill.raw_txn import RawTxn
from mailquill.normalizer import normalize_amount, normalize_date, normalize
from mailquill.schema import make_txn_id


def test_normalize_amount_strips_separators_and_symbols():
    assert normalize_amount("1,200") == "1200"
    assert normalize_amount("$1,200.50") == "1200.50"
    assert normalize_amount("NT$ 1,200") == "1200"
    assert normalize_amount("1200元") == "1200"
    assert normalize_amount("-50.00") == "-50.00"
    assert normalize_amount("") == ""
    assert normalize_amount("無金額") == ""


def test_normalize_date_unifies_separators():
    assert normalize_date(" 2026/06/01 ") == "2026-06-01"
    assert normalize_date("2026.06.01") == "2026-06-01"
    assert normalize_date("2026-06-01") == "2026-06-01"


def test_normalize_builds_transaction():
    raw = RawTxn(bank="ExampleBank", date="2026/06/01", amount="1,200",
                 merchant_raw=" PXMART ", account_last4="1234")
    txn = normalize(raw, msg_id="m1", source_type="pdf",
                    raw_ref="raw/m1", imported_at="2026-06-24T10:00:00")
    assert txn.date == "2026-06-01"
    assert txn.amount == "1200"
    assert txn.merchant_raw == " PXMART "
    assert txn.merchant_norm == "PXMART"
    assert txn.category_l1 == "Uncategorized"
    assert txn.category_l2 == ""
    assert txn.bank == "ExampleBank"
    assert txn.account_last4 == "1234"
    assert txn.currency == "TWD"
    assert txn.source_type == "pdf"
    assert txn.source_msg_id == "m1"
    assert txn.raw_ref == "raw/m1"
    assert txn.imported_at == "2026-06-24T10:00:00"
    assert txn.txn_id == make_txn_id("ExampleBank", "1234", "2026-06-01", "1200", " PXMART ")


def test_normalize_empty_amount_is_unique_per_seq():
    raw = RawTxn(bank="B", date="2026-06-01", amount="無", merchant_raw="商家X")
    t0 = normalize(raw, "m1", "email_body", "m1", "2026-06-24T00:00:00", seq=0)
    t1 = normalize(raw, "m1", "email_body", "m1", "2026-06-24T00:00:00", seq=1)
    assert t0.amount == "" and t1.amount == ""
    assert t0.txn_id != t1.txn_id  # 不會互相覆蓋
