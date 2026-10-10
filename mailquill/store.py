"""CSV 真實來源讀寫（含去重）。SQLite 重建在 Task 3 追加。"""
from __future__ import annotations

import csv
import os
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace

from mailquill.categorizer import apply_categories, load_categories
from mailquill.merchant_names import aliases_path_for, apply_display_names, load_aliases
from mailquill.reconcile import ReconcileResult, reconcile_provisional
from mailquill.schema import FIELDS, Transaction, make_txn_id


@dataclass
class AppendResult:
    added: int
    skipped: int


def read_transactions(csv_path: str) -> list[Transaction]:
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [Transaction.from_row(row) for row in reader]


def count_legacy_id_rows(csv_path: str) -> int:
    """回傳 seq 為空的列數，也就是 txn_id 還是舊公式（無 seq）算出來的列。

    這些列的 id 無法對上現行公式，同一封信一被重抓就會多長一列，
    所以在抓信前要先提醒跑 migrate-ids。
    """
    return sum(1 for t in read_transactions(csv_path) if t.seq == "")


def append_transactions(csv_path: str, txns: list[Transaction]) -> AppendResult:
    """把新交易併進 CSV（以 txn_id 去重）。整份先寫暫存檔再 rename，
    中途中斷或原檔結尾缺換行都不會弄壞既有資料；沒有新增時不碰檔案。"""
    existing = read_transactions(csv_path)
    seen = {t.txn_id for t in existing}
    fresh: list[Transaction] = []
    for txn in txns:
        if txn.txn_id in seen:
            continue
        seen.add(txn.txn_id)
        fresh.append(txn)
    if fresh or not os.path.exists(csv_path):
        write_transactions_atomic(csv_path, existing + fresh)
    return AppendResult(added=len(fresh), skipped=len(txns) - len(fresh))


_RETRY_PREFIX = "!"
_UNPARSED_PREFIX = "?"   # `?msg_id:parser指紋`：當時 parser 解不出交易的信


def _read_registry_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


@dataclass
class Registry:
    seen: set[str]             # 已完整處理過的信件 msg_id
    retry: set[str]            # 處理到一半、下次 run 仍要重抓
    unparsed: dict[str, str]   # {msg_id: parser 指紋}，parser 當時解不出交易


def read_registry(path: str) -> Registry:
    """一次讀完 seen-id 檔的三種行：`id`（已處理）、`!id`（需重試）、
    `?id:指紋`（parser 當時解不出交易，指紋變了才重試）。"""
    reg = Registry(set(), set(), {})
    for ln in _read_registry_lines(path):
        if ln.startswith(_RETRY_PREFIX):
            reg.retry.add(ln[len(_RETRY_PREFIX):])
        elif ln.startswith(_UNPARSED_PREFIX):
            if ":" in ln:
                msg_id, _, fp = ln[len(_UNPARSED_PREFIX):].rpartition(":")
                reg.unparsed[msg_id] = fp
        else:
            reg.seen.add(ln)
    return reg


def read_seen_ids(path: str) -> set[str]:
    """讀取「已完整處理過的信件 msg_id」清單。獨立於 transactions.csv 之外
    持久保存，這樣 `reconcile` 把重複的那筆交易列丟掉後，CSV 裡雖然不再有
    對應的 source_msg_id，`run` 也不會誤判成新信重抓一次（見 pipeline.py）。
    """
    return read_registry(path).seen


@contextmanager
def atomic_open(path: str, newline: str | None = None):
    """先寫暫存檔、成功才 rename 蓋過原檔；中途出錯就清掉暫存檔，原檔不受影響。"""
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline=newline, encoding="utf-8") as f:
            yield f
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_seen_ids(path: str, ids: set[str], retry: set[str] | frozenset = frozenset(),
                   unparsed: dict[str, str] | None = None) -> None:
    with atomic_open(path) as f:
        for msg_id in sorted(ids):
            f.write(msg_id + "\n")
        for msg_id in sorted(retry):
            f.write(_RETRY_PREFIX + msg_id + "\n")
        for msg_id, fp in sorted((unparsed or {}).items()):
            f.write(f"{_UNPARSED_PREFIX}{msg_id}:{fp}\n")


def write_transactions_atomic(csv_path: str, txns: list[Transaction]) -> None:
    """整份覆寫 CSV（先寫暫存檔再 rename），避免寫到一半中斷弄壞既有檔案。"""
    with atomic_open(csv_path, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for t in txns:
            writer.writerow(t.to_row())


def rekey_txns(txns: list[Transaction]) -> tuple[list[Transaction], int]:
    """CSV 匯入的列改用「同日同店同額第幾次出現」當 seq 重算 txn_id。

    舊版用列在檔案中的位置當 seq，兩份日期範圍重疊的匯出檔會算出不同
    txn_id 而重複入帳；新版（見 ingest.py）改成出現次序，這裡把舊資料轉成
    同一套編號，並把 seq 存進 CSV（migrate-ids 靠它認得這些列已是現行公式）。
    冪等：已是新編號的列不會再變。

    出現次序取決於列在 `txns` 裡的順序：傳入單一檔案的內容（ingest）或整份 CSV
    （rekey_csv_rows）都可以，但別手動刪掉兩筆相同 csv 交易中的一筆之後再整份
    重算，後面那筆的 seq 會前移、txn_id 跟著變，之後重匯同一份檔案會重複入帳。
    """
    occurrences: Counter = Counter()
    out: list[Transaction] = []
    changed = 0
    for t in txns:
        if t.source_type != "csv":
            out.append(t)
            continue
        key = (t.bank, t.account_last4, t.date, t.amount, t.merchant_raw)
        seq = occurrences[key]
        occurrences[key] += 1
        new_id = make_txn_id(t.bank, t.account_last4, t.date, t.amount, t.merchant_raw, seq)
        if new_id != t.txn_id or t.seq != str(seq):
            t = replace(t, txn_id=new_id, seq=str(seq))
            changed += 1
        out.append(t)
    return out, changed


def rekey_csv_rows(csv_path: str) -> int:
    """對 transactions.csv 做 `rekey_txns` 並寫回，回傳改了幾列。"""
    txns, changed = rekey_txns(read_transactions(csv_path))
    if changed:
        write_transactions_atomic(csv_path, txns)
    return changed


def rebuild(csv_path: str, db_path: str, categories_path: str) -> int:
    """讀 CSV → 依 categories.yaml 規則重新分類、依 merchants.yaml 重算顯示名稱
    「所有」交易 → 寫回 CSV → 重建 SQLite。"""
    rules = load_categories(categories_path)
    aliases = load_aliases(aliases_path_for(categories_path))
    txns = apply_display_names(
        [apply_categories(t, rules) for t in read_transactions(csv_path)], aliases)
    write_transactions_atomic(csv_path, txns)
    rebuild_sqlite(csv_path, db_path, txns=txns)
    return len(txns)


def reconcile(csv_path: str, db_path: str) -> ReconcileResult:
    """合併每日通知與月結單的重複記錄、清掉從未真正請款的幽靈通知
    （見 reconcile.py），寫回 CSV/SQLite。"""
    txns = read_transactions(csv_path)
    kept, result = reconcile_provisional(txns)
    if result.merged or result.voided:
        write_transactions_atomic(csv_path, kept)
        rebuild_sqlite(csv_path, db_path, txns=kept)
    return result


def rebuild_sqlite(csv_path: str, db_path: str, txns: list[Transaction] | None = None) -> int:
    """重建 SQLite；`txns` 給的話直接用（呼叫端剛寫完同樣內容的 CSV，避免
    再讀一次剛寫出的檔案），否則照常從 `csv_path` 讀。

    先建在暫存檔、成功才 rename 蓋過舊 DB，中途出錯舊 DB 仍可用（`serve` 同時
    在讀也不會看到空表）。
    """
    if txns is None:
        txns = read_transactions(csv_path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(db_path)), suffix=".tmp")
    os.close(fd)
    try:
        conn = sqlite3.connect(tmp)
        try:
            col_defs = ", ".join(
                f"{name} REAL" if name == "amount" else f"{name} TEXT"
                for name in FIELDS
            )
            conn.execute(f"CREATE TABLE transactions ({col_defs})")
            placeholders = ", ".join("?" for _ in FIELDS)
            rows = []
            for t in txns:
                row = t.to_row()
                rows.append([
                    float(row[name]) if name == "amount" and row[name] != "" else row[name]
                    for name in FIELDS
                ])
            conn.executemany(
                f"INSERT INTO transactions ({', '.join(FIELDS)}) VALUES ({placeholders})",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, db_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(txns)
