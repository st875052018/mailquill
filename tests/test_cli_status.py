"""`mailquill status` 子指令：合成資料，不打網路。"""
from mailquill.bills_store import append_bills
from mailquill.store import append_transactions
from tests.test_status import BILLS, TXNS


def test_main_status_subcommand(tmp_path, capsys):
    from mailquill.cli import main
    csv_path, bills_path = str(tmp_path / "t.csv"), str(tmp_path / "bills.csv")
    append_transactions(csv_path, TXNS)
    append_bills(bills_path, BILLS)
    assert main(["status", "--csv", csv_path, "--bills", bills_path, "--today", "2026-09-30"]) == 0
    out = capsys.readouterr().out
    assert "Cathay" in out and "Fubon" in out and "Amex" in out
