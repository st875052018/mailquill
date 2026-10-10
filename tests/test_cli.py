import csv
import os
import sqlite3
import textwrap

import yaml

import pytest

from mailquill.schema import Transaction
from mailquill.store import append_transactions, read_transactions
from mailquill.cli import rebuild, main


def _rules_file(tmp_path):
    p = tmp_path / "categories.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - {keyword: "全聯", l1: "Food", l2: "Groceries"}
    """), encoding="utf-8")
    return str(p)


def _txn(merchant, l1="Uncategorized", l2=""):
    return Transaction(
        txn_id=merchant, date="2026-06-01", post_date="", amount="100.00",
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1=l1, category_l2=l2, bank="Cathay", account_last4="1234",
        source_type="email_body", source_msg_id="m1", raw_ref="",
        imported_at="2026-06-24T00:00:00",
    )


def test_rebuild_recategorizes_and_writes_back(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心"), _txn("未知商家")])
    n = rebuild(csv_path, db_path, _rules_file(tmp_path))
    assert n == 2

    rows = {r.merchant_raw: r for r in read_transactions(csv_path)}
    assert (rows["全聯福利中心"].category_l1, rows["全聯福利中心"].category_l2) == ("Food", "Groceries")
    assert rows["未知商家"].category_l1 == "Uncategorized"

    conn = sqlite3.connect(db_path)
    try:
        got = conn.execute(
            "SELECT category_l1 FROM transactions WHERE merchant_raw='全聯福利中心'"
        ).fetchone()[0]
        assert got == "Food"
    finally:
        conn.close()


def test_main_rebuild_subcommand(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心")])
    rc = main([
        "rebuild", "--csv", csv_path, "--db", db_path,
        "--categories", _rules_file(tmp_path),
    ])
    assert rc == 0
    assert read_transactions(csv_path)[0].category_l1 == "Food"


def test_main_reconcile_subcommand(tmp_path):
    from mailquill.schema import Transaction, make_txn_id
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    email_txn = _txn("")  # 每日通知：商店名稱空白，account_last4=1234, date=2026-06-01, amount=100.00
    pdf_txn = Transaction(
        txn_id=make_txn_id("Cathay", "1234", "2026-06-01", "100.00", "全聯福利中心"),
        date="2026-06-01", post_date="", amount="100.00", currency="TWD",
        merchant_raw="全聯福利中心", merchant_norm="全聯福利中心",
        category_l1="Uncategorized", category_l2="", bank="Cathay", account_last4="1234",
        source_type="pdf", source_msg_id="stmt1", raw_ref="",
        imported_at="2026-06-24T00:00:00",
    )
    append_transactions(csv_path, [email_txn, pdf_txn])

    rc = main(["reconcile", "--csv", csv_path, "--db", db_path])
    assert rc == 0
    rows = read_transactions(csv_path)
    assert len(rows) == 1
    assert rows[0].merchant_raw == "全聯福利中心"


def test_rebuild_atomic_rewrite_failure_leaves_csv_intact(tmp_path, monkeypatch):
    """
    Regression guard: if rebuild() fails during categorization OR during the
    CSV write, the original CSV must be left byte-identical and no .tmp files
    may remain in the directory.

    Two sub-cases are exercised:

    (a) Failure before the CSV is touched: patch apply_categories to raise.
    (b) Failure mid-write: patch csv.DictWriter.writerow to raise after the
        temp file has been opened but before os.replace is called.
    """
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, [_txn("全聯福利中心"), _txn("未知商家")])
    original_bytes = open(csv_path, "rb").read()

    def _no_tmp_files():
        return [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]

    # --- sub-case (a): failure before CSV is touched ---
    import mailquill.store as store_mod
    monkeypatch.setattr(store_mod, "apply_categories", lambda t, rules: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        rebuild(csv_path, db_path, _rules_file(tmp_path))

    assert open(csv_path, "rb").read() == original_bytes, "CSV mutated despite pre-write failure"
    assert _no_tmp_files() == [], "Leftover .tmp after pre-write failure"

    # Restore for sub-case (b)
    monkeypatch.undo()

    # --- sub-case (b): failure during write (after temp file opened) ---
    original_writerow = csv.DictWriter.writerow

    call_count = [0]

    def _failing_writerow(self, row):
        call_count[0] += 1
        if call_count[0] >= 1:
            raise RuntimeError("write boom")
        return original_writerow(self, row)

    monkeypatch.setattr(csv.DictWriter, "writerow", _failing_writerow)

    with pytest.raises(RuntimeError, match="write boom"):
        rebuild(csv_path, db_path, _rules_file(tmp_path))

    assert open(csv_path, "rb").read() == original_bytes, "CSV mutated despite mid-write failure"
    assert _no_tmp_files() == [], "Leftover .tmp after mid-write failure"


def _config_file(tmp_path, **overrides):
    """寫一份最小 config.yaml，回傳路徑。"""
    data = {"label": "銀行"}
    data.update(overrides)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(p)


def test_rebuild_defaults_come_from_config(tmp_path):
    """不帶 --csv/--db/--categories 時，rebuild 應改讀 config.yaml 的路徑。

    Regression guard：舊版 rebuild 只用 argparse 硬編預設值（mailquill.db），
    與 report 讀的 config.db_path 不一致，會出現「rebuild 成功但報表沒更新」。
    """
    csv_path = str(tmp_path / "from_config.csv")
    db_path = str(tmp_path / "from_config.db")
    append_transactions(csv_path, [_txn("全聯福利中心")])
    cfg = _config_file(
        tmp_path, csv_path=csv_path, db_path=db_path,
        categories_path=_rules_file(tmp_path),
    )

    rc = main(["rebuild", "--config", cfg])
    assert rc == 0
    assert read_transactions(csv_path)[0].category_l1 == "Food"
    assert os.path.exists(db_path), "rebuild 沒寫到 config.yaml 指定的 db_path"


def test_rebuild_explicit_flags_override_config(tmp_path):
    """顯式給的 CLI 參數優先於 config.yaml。"""
    cli_csv = str(tmp_path / "cli.csv")
    cli_db = str(tmp_path / "cli.db")
    append_transactions(cli_csv, [_txn("全聯福利中心")])
    cfg = _config_file(
        tmp_path,
        csv_path=str(tmp_path / "ignored.csv"),
        db_path=str(tmp_path / "ignored.db"),
        categories_path=_rules_file(tmp_path),
    )

    rc = main(["rebuild", "--config", cfg, "--csv", cli_csv, "--db", cli_db])
    assert rc == 0
    assert os.path.exists(cli_db)
    assert not os.path.exists(str(tmp_path / "ignored.db"))
    assert read_transactions(cli_csv)[0].category_l1 == "Food"


def test_rebuild_without_config_file_falls_back_to_defaults(tmp_path, monkeypatch):
    """config.yaml 不存在時不應炸掉，退回硬編預設值（相對於 cwd）。"""
    monkeypatch.chdir(tmp_path)
    append_transactions("transactions.csv", [_txn("全聯福利中心")])
    (tmp_path / "categories.yaml").write_text(
        'rules:\n  - {keyword: "全聯", l1: "Food", l2: "Groceries"}\n', encoding="utf-8"
    )

    rc = main(["rebuild"])
    assert rc == 0
    assert os.path.exists("mailquill.db")
    assert read_transactions("transactions.csv")[0].category_l1 == "Food"
