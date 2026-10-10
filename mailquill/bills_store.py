"""帳單應繳金額（`BillDue`）CSV 讀寫——跟 `transactions.csv` 分開的獨立小
檔案，一筆代表一家銀行某個帳單月份。`append_bills` 依 (bank, period) 去重
覆寫：同一期重新解出新值就蓋掉舊值（如信件內容修正過、或改了擷取邏輯
重跑），不像 `store.append_transactions` 按 txn_id 去重後略過新值——帳單
金額沒有「同一期出現兩次代表不同筆」的情境。
"""
from __future__ import annotations

import csv
import os

from mailquill.bill_due import BillDue
from mailquill.store import atomic_open

FIELDS = ["bank", "period", "amount_due", "min_due", "source_msg_id"]


def read_bills(csv_path: str) -> list[BillDue]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [BillDue(**{k: row.get(k, "") for k in FIELDS}) for row in reader]


def append_bills(csv_path: str, bills: list[BillDue]) -> None:
    if not bills:
        return
    merged: dict[tuple[str, str], BillDue] = {
        (b.bank, b.period): b for b in read_bills(csv_path)
    }
    for b in bills:
        merged[(b.bank, b.period)] = b
    rows = sorted(merged.values(), key=lambda b: (b.bank, b.period))

    with atomic_open(csv_path, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for b in rows:
            writer.writerow({k: getattr(b, k) for k in FIELDS})
