"""`mailquill migrate-ids` 的 CLI 行為：預設 dry-run、--apply 才寫檔並備份。"""
import csv
import glob
import os

import mailquill.cli as cli
from mailquill.config import Config
from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, count_legacy_id_rows, read_transactions
from mailquill.migrate import legacy_txn_id


def _txn(merchant, *, seq="", imported_at="2026-06-29T15:52:20", legacy=False):
    t = Transaction(
        txn_id="", date="2026-06-01", post_date="", amount="100", currency="TWD",
        merchant_raw=merchant, merchant_norm=merchant, category_l1="未分類",
        category_l2="", bank="Cathay", account_last4="6224", source_type="pdf",
        source_msg_id="m1", raw_ref="m1", imported_at=imported_at, seq=seq,
    )
    t.txn_id = legacy_txn_id(t) if legacy else make_txn_id(
        t.bank, t.account_last4, t.date, t.amount, t.merchant_raw, int(seq or 0))
    return t


def _csv_with_duplicates(tmp_path):
    """一份含「舊式 id + 重抓後新式 id」的 CSV，兩者其實是同兩筆交易。"""
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("A", legacy=True), _txn("B", legacy=True)])
    append_transactions(csv_path, [
        _txn("A", seq="0", imported_at="2026-09-02T09:41:41"),
        _txn("B", seq="1", imported_at="2026-09-02T09:41:41"),
    ])
    assert len(read_transactions(csv_path)) == 4, "前置條件：重複已存在"
    return csv_path


def _cfg(monkeypatch, csv_path, tmp_path):
    cfg = Config(label="銀行", csv_path=csv_path, db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    return cfg


def test_migrate_ids_dry_run_reports_but_does_not_write(monkeypatch, tmp_path, capsys):
    csv_path = _csv_with_duplicates(tmp_path)
    _cfg(monkeypatch, csv_path, tmp_path)
    before = open(csv_path, "rb").read()

    rc = cli.main(["migrate-ids", "--config", "x.yaml"])
    assert rc == 0
    assert open(csv_path, "rb").read() == before, "dry-run 不該改動 CSV"
    assert glob.glob(csv_path + ".bak-*") == [], "dry-run 不該產生備份"

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "2" in out  # 會移除 2 列


def test_migrate_ids_apply_dedupes_and_backs_up(monkeypatch, tmp_path, capsys):
    csv_path = _csv_with_duplicates(tmp_path)
    _cfg(monkeypatch, csv_path, tmp_path)
    before = open(csv_path, "rb").read()

    rc = cli.main(["migrate-ids", "--config", "x.yaml", "--apply"])
    assert rc == 0

    rows = read_transactions(csv_path)
    assert len(rows) == 2, "重複列沒有被移除"
    assert {t.imported_at for t in rows} == {"2026-06-29T15:52:20"}
    assert all(t.seq != "" for t in rows), "seq 沒有寫回 CSV"

    backups = glob.glob(csv_path + ".bak-*")
    assert len(backups) == 1, "沒有留下備份"
    assert open(backups[0], "rb").read() == before, "備份內容與原檔不符"

    assert "遷移" in capsys.readouterr().out


def test_migrate_ids_apply_rebuilds_sqlite(monkeypatch, tmp_path):
    csv_path = _csv_with_duplicates(tmp_path)
    cfg = _cfg(monkeypatch, csv_path, tmp_path)

    assert cli.main(["migrate-ids", "--config", "x.yaml", "--apply"]) == 0
    assert os.path.exists(cfg.db_path), "遷移後應同步重建 SQLite"

    import sqlite3
    conn = sqlite3.connect(cfg.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    finally:
        conn.close()


def test_migrate_ids_on_clean_csv_is_noop(monkeypatch, tmp_path, capsys):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("A", seq="0"), _txn("B", seq="1")])
    _cfg(monkeypatch, csv_path, tmp_path)
    before = open(csv_path, "rb").read()

    assert cli.main(["migrate-ids", "--config", "x.yaml", "--apply"]) == 0
    assert open(csv_path, "rb").read() == before, "已遷移的 CSV 不該被改寫"
    assert glob.glob(csv_path + ".bak-*") == [], "no-op 不該產生備份"


def test_count_legacy_id_rows(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("A", legacy=True), _txn("B", seq="1")])
    assert count_legacy_id_rows(csv_path) == 1
    assert count_legacy_id_rows(str(tmp_path / "missing.csv")) == 0


def test_run_warns_before_fetching_when_csv_has_legacy_ids(monkeypatch, tmp_path, capsys):
    """舊式 id 還在 CSV 裡就去重抓，必然產生重複 —— 抓之前先警告。"""
    from mailquill.pipeline import RunResult
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("A", legacy=True)])
    _cfg(monkeypatch, csv_path, tmp_path)
    monkeypatch.setattr(cli, "build_service", lambda c, t: object())
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **k: RunResult(fetched=0, matched=0, added=0, skipped=0))

    assert cli.main(["run", "--config", "x.yaml"]) == 0
    out = capsys.readouterr().out
    assert "migrate-ids" in out, "沒有提示先跑 migrate-ids"


def test_apply_fills_missing_seq_even_when_ids_are_already_current(tmp_path):
    # id 已是現行公式、只是 CSV 沒存 seq：apply 仍要把 seq 補上，不然舊式 id 警告永遠消不掉
    from dataclasses import replace
    from mailquill.cli import main
    from mailquill.schema import Transaction, make_txn_id
    from mailquill.store import count_legacy_id_rows, read_transactions, write_transactions_atomic
    csv_path, db_path = str(tmp_path / "t.csv"), str(tmp_path / "t.db")
    t = Transaction(
        txn_id=make_txn_id("Cathay", "1234", "2026-06-01", "100", "全聯", 0),
        date="2026-06-01", post_date="", amount="100", currency="TWD", merchant_raw="全聯",
        merchant_norm="全聯", category_l1="Food", category_l2="", bank="Cathay",
        account_last4="1234", source_type="pdf", source_msg_id="m1", raw_ref="m1",
        imported_at="2026-06-29T10:00:00", seq="")
    write_transactions_atomic(csv_path, [t])
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"label: x\ncsv_path: {csv_path}\ndb_path: {db_path}\n", encoding="utf-8")
    assert count_legacy_id_rows(csv_path) == 1

    assert main(["migrate-ids", "--config", str(cfg), "--apply"]) == 0

    assert count_legacy_id_rows(csv_path) == 0
    assert read_transactions(csv_path)[0].txn_id == t.txn_id
