import sqlite3

from mailquill.schema import Transaction, make_txn_id, FIELDS
from mailquill.store import (
    read_transactions, append_transactions, rebuild_sqlite, reconcile,
    read_seen_ids, write_seen_ids,
)


def _txn(merchant, amount="100.00"):
    tid = make_txn_id("Cathay", "1234", "2026-06-01", amount, merchant)
    return Transaction(
        txn_id=tid, date="2026-06-01", post_date="", amount=amount,
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1="Uncategorized", category_l2="", bank="Cathay",
        account_last4="1234", source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def test_read_missing_file_returns_empty(tmp_path):
    assert read_transactions(str(tmp_path / "none.csv")) == []


def test_read_seen_ids_missing_file_returns_empty_set(tmp_path):
    assert read_seen_ids(str(tmp_path / "none.txt")) == set()


def test_write_then_read_seen_ids_roundtrip(tmp_path):
    path = str(tmp_path / "seen.txt")
    write_seen_ids(path, {"m1", "m2", "m3"})
    assert read_seen_ids(path) == {"m1", "m2", "m3"}


def test_write_seen_ids_overwrites_previous_contents(tmp_path):
    path = str(tmp_path / "seen.txt")
    write_seen_ids(path, {"m1"})
    write_seen_ids(path, {"m2"})
    assert read_seen_ids(path) == {"m2"}


def test_append_then_read_roundtrip(tmp_path):
    path = str(tmp_path / "t.csv")
    res = append_transactions(path, [_txn("全聯"), _txn("家樂福")])
    assert (res.added, res.skipped) == (2, 0)
    rows = read_transactions(path)
    assert {r.merchant_raw for r in rows} == {"全聯", "家樂福"}


def test_append_dedups_against_existing(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("全聯")])
    res = append_transactions(path, [_txn("全聯"), _txn("家樂福")])
    assert (res.added, res.skipped) == (1, 1)
    assert len(read_transactions(path)) == 2


def test_append_dedups_within_batch(tmp_path):
    path = str(tmp_path / "t.csv")
    res = append_transactions(path, [_txn("全聯"), _txn("全聯")])
    assert (res.added, res.skipped) == (1, 1)


def test_header_written_once(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("全聯")])
    append_transactions(path, [_txn("家樂福")])
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert lines[0].startswith("txn_id,")
    assert sum(1 for ln in lines if ln.startswith("txn_id,")) == 1


def test_rebuild_sqlite_loads_all_rows(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯", "100.00"), _txn("家樂福", "250.50")])
    n = rebuild_sqlite(csv_path, db_path)
    assert n == 2
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT SUM(amount) FROM transactions").fetchone()[0]
        assert abs(total - 350.50) < 1e-9
        cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        assert cols == FIELDS
    finally:
        conn.close()


def test_reconcile_merges_daily_notice_into_matching_statement_row(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    email_txn = _txn("", amount="100.00")  # 每日通知：商店名稱空白
    pdf_txn = Transaction(
        txn_id=make_txn_id("Cathay", "1234", "2026-06-01", "100.00", "全聯"),
        date="2026-06-01", post_date="", amount="100.00", currency="TWD",
        merchant_raw="全聯", merchant_norm="全聯", category_l1="Uncategorized", category_l2="",
        bank="Cathay", account_last4="1234", source_type="pdf",
        source_msg_id="stmt1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )
    append_transactions(csv_path, [email_txn, pdf_txn])
    rebuild_sqlite(csv_path, db_path)

    result = reconcile(csv_path, db_path)
    assert (result.merged, result.ambiguous) == (1, 0)

    rows = read_transactions(csv_path)
    assert len(rows) == 1
    assert rows[0].merchant_raw == "全聯"
    assert rows[0].source_type == "pdf"

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_reconcile_no_matches_leaves_csv_untouched(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯")])
    rebuild_sqlite(csv_path, db_path)

    result = reconcile(csv_path, db_path)
    assert (result.merged, result.ambiguous) == (0, 0)
    assert len(read_transactions(csv_path)) == 1


def test_rebuild_sqlite_is_idempotent(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯", "100.00")])
    rebuild_sqlite(csv_path, db_path)
    n = rebuild_sqlite(csv_path, db_path)
    assert n == 1
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_rebuild_links_foreign_fee_and_categorizes_as_fee(tmp_path):
    from mailquill.store import rebuild
    csv_path = str(tmp_path / "t.csv")
    cats = tmp_path / "categories.yaml"
    cats.write_text('rules:\n  - {keyword: "MOBILE", l1: "Transport", l2: "Suica"}\n', encoding="utf-8")
    append_transactions(csv_path, [_txn("MOBILE SUICA APPLE V", "3998"),
                                   _txn("國外交易手續費 -MOBILE", "60")])
    rebuild(csv_path, str(tmp_path / "t.db"), str(cats))
    fee = next(t for t in read_transactions(csv_path) if t.merchant_raw.startswith("國外"))
    assert (fee.category_l1, fee.category_l2) == ("Other", "Fee")
    assert fee.merchant_norm == "國外手續費 · MOBILE SUICA APPLE V"


def test_rekey_csv_rows_uses_occurrence_index_not_row_position(tmp_path):
    from dataclasses import replace
    from mailquill.store import rekey_csv_rows, write_transactions_atomic
    path = str(tmp_path / "t.csv")

    def csv_txn(merchant, seq):  # 舊算法：seq = 該列在檔案中的位置
        t = _txn(merchant)
        return replace(t, source_type="csv", bank="Amex", account_last4="9",
                       txn_id=make_txn_id("Amex", "9", "2026-06-01", "100.00", merchant, seq))
    pdf_row = _txn("PDFSHOP")
    write_transactions_atomic(path, [csv_txn("A", 0), csv_txn("B", 1), csv_txn("A", 2), pdf_row])

    assert rekey_csv_rows(path) == 3  # B 與第二個 A 的 id 變了；第一個 A 只是補上 seq
    rows = read_transactions(path)
    ids = {(r.merchant_raw, i): r.txn_id for i, r in enumerate(rows)}
    assert ids[("A", 0)] == make_txn_id("Amex", "9", "2026-06-01", "100.00", "A", 0)
    assert ids[("B", 1)] == make_txn_id("Amex", "9", "2026-06-01", "100.00", "B", 0)
    assert ids[("A", 2)] == make_txn_id("Amex", "9", "2026-06-01", "100.00", "A", 1)
    assert [r.seq for r in rows[:3]] == ["0", "0", "1"]   # seq 一併存進 CSV，migrate-ids 才認得
    assert rows[3].txn_id == pdf_row.txn_id and rows[3].seq == ""   # 非 csv 來源不動
    assert rekey_csv_rows(path) == 0                  # 冪等


def test_rekey_txns_is_public_and_pure():
    from dataclasses import replace
    from mailquill.store import rekey_txns
    a = replace(_txn("A"), source_type="csv")
    out, changed = rekey_txns([a, replace(a), _txn("PDF")])
    assert changed == 2                                  # 第一個 A 補 seq、第二個 A 改 id
    assert [t.seq for t in out[:2]] == ["0", "1"]
    assert out[0].txn_id != out[1].txn_id
    assert out[2].seq == ""                              # 非 csv 來源不動


def test_append_survives_file_without_trailing_newline(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("A")])
    raw = open(path, encoding="utf-8", newline="").read().rstrip("\r\n")
    open(path, "w", encoding="utf-8", newline="").write(raw)   # 模擬結尾被截掉換行
    res = append_transactions(path, [_txn("B")])
    assert res.added == 1
    assert [t.merchant_raw for t in read_transactions(path)] == ["A", "B"]


def test_append_leaves_file_untouched_when_nothing_new(tmp_path):
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("A")])
    before = open(path, "rb").read()
    res = append_transactions(path, [_txn("A")])
    assert (res.added, res.skipped) == (0, 1)
    assert open(path, "rb").read() == before


def test_append_failure_keeps_original_file(tmp_path, monkeypatch):
    import mailquill.store as store_mod
    path = str(tmp_path / "t.csv")
    append_transactions(path, [_txn("A")])
    before = open(path, "rb").read()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(store_mod.os, "replace", boom)
    try:
        append_transactions(path, [_txn("B")])
    except OSError:
        pass
    assert open(path, "rb").read() == before
    assert [p.name for p in tmp_path.iterdir()] == ["t.csv"]   # 暫存檔已清掉


def test_read_registry_parses_all_three_line_kinds_in_one_pass(tmp_path):
    from mailquill.store import read_registry
    path = str(tmp_path / "seen.txt")
    write_seen_ids(path, {"a", "b"}, retry={"c"}, unparsed={"d": "fp1"})
    reg = read_registry(path)
    assert (reg.seen, reg.retry, reg.unparsed) == ({"a", "b"}, {"c"}, {"d": "fp1"})


def test_read_registry_missing_file_is_empty(tmp_path):
    from mailquill.store import read_registry
    reg = read_registry(str(tmp_path / "none.txt"))
    assert (reg.seen, reg.retry, reg.unparsed) == (set(), set(), {})


def test_rebuild_sqlite_failure_keeps_previous_db(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("A")])
    rebuild_sqlite(csv_path, db_path)
    bad = _txn("B")
    bad.amount = "not-a-number"      # float() 會丟 ValueError
    try:
        rebuild_sqlite(csv_path, db_path, txns=[bad])
    except ValueError:
        pass
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    finally:
        conn.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["t.csv", "t.db"]
