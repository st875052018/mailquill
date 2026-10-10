from mailquill.schema import FIELDS, Transaction, make_txn_id


def test_fields_order_is_fixed():
    """欄位順序即 CSV 欄位順序，只能往後追加，不能插入或重排。"""
    assert FIELDS == [
        "txn_id", "date", "post_date", "amount", "currency",
        "merchant_raw", "merchant_norm", "category_l1", "category_l2",
        "bank", "account_last4", "source_type", "source_msg_id",
        "raw_ref", "imported_at", "seq",
    ]


def test_make_txn_id_is_deterministic_and_short():
    a = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    b = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    assert a == b
    assert len(a) == 16
    assert a.isalnum()


def test_make_txn_id_differs_on_amount():
    a = make_txn_id("Cathay", "1234", "2026-06-01", "1200.00", "全聯福利中心")
    b = make_txn_id("Cathay", "1234", "2026-06-01", "1300.00", "全聯福利中心")
    assert a != b


def test_to_row_and_from_row_roundtrip():
    txn = Transaction(
        txn_id="abc", date="2026-06-01", post_date="2026-06-02",
        amount="1200.00", currency="TWD", merchant_raw="全聯",
        merchant_norm="全聯福利中心", category_l1="Food", category_l2="Groceries",
        bank="Cathay", account_last4="1234", source_type="email_body",
        source_msg_id="msg-1", raw_ref="raw/msg-1.html", imported_at="2026-06-24T10:00:00",
    )
    row = txn.to_row()
    assert list(row.keys()) == FIELDS
    assert Transaction.from_row(row) == txn
