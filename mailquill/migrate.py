"""把加入 `seq` 之前的舊式 txn_id 遷移成現行公式，並收斂因此產生的重複列。

`make_txn_id` 後來把 `seq`（同一份帳單內的出現序號）納入雜湊，但既有 CSV 的
id 沒有一併重算。於是同一封信被重抓時，同一筆交易會同時存在兩種 id，
`append_transactions` 只比對 txn_id，就把它當成兩筆不同交易寫進去。

seq 先由「該列在其 (source_msg_id, imported_at) 群組內的位置」還原；位置對不上
（同一份帳單分批寫入、或群組內有列被對帳合併掉）時，改從 id 雜湊直接試出 seq。
位置還原的理由：同一封信的
交易是照解析順序 append 進 CSV 的，而同一份帳單重跑的解析順序一致，
所以位置即當初的 seq。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, replace

from mailquill.schema import Transaction, make_txn_id


def legacy_txn_id(t: Transaction) -> str:
    """加入 seq 之前的舊公式：source_account|date|amount|merchant。"""
    key = "|".join([f"{t.bank}:{t.account_last4}", t.date, t.amount,
                    t.merchant_raw])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


MAX_SEQ_SEARCH = 5000


def _find_seq(t: Transaction) -> int | None:
    """試出讓 make_txn_id 對上 t.txn_id 的 seq；都不是現行公式就回 None。
    雜湊含全部欄位，碰撞機率可忽略，所以對得上就是真正的 seq。"""
    for seq in range(MAX_SEQ_SEARCH):
        if make_txn_id(t.bank, t.account_last4, t.date, t.amount,
                       t.merchant_raw, seq) == t.txn_id:
            return seq
    return None


@dataclass
class Migration:
    txns: list[Transaction]
    """遷移後保留的交易（已重算 id、已填 seq、已去重），維持原本順序。"""
    changed: int
    """id 被重算的列數。"""
    removed: list[Transaction]
    """重算後與他列撞成同一個 id 而被移除的列（保留 imported_at 較早者）。"""
    unexplained: list[Transaction]
    """id 既非舊公式、也無法用位置重現現行公式的列；原封不動，僅回報。"""


def plan_migration(txns: list[Transaction]) -> Migration:
    """計算遷移結果。純函式，不碰檔案，dry-run 與實際套用共用。"""
    groups: dict[tuple[str, str], int] = defaultdict(int)
    staged: list[tuple[Transaction, bool]] = []   # (交易, 是否無法歸類)
    changed = 0
    unexplained: list[Transaction] = []

    for t in txns:
        key = (t.source_msg_id, t.imported_at)
        seq = groups[key]
        groups[key] += 1
        # 存下來的 seq 跟 id 對得上就已經是現行公式。CSV 匯入的列用「同日同店
        # 同額第幾次出現」當 seq（見 store.rekey_csv_rows），跟列在群組內的位置
        # 無關，不能拿位置去重算。
        if t.seq != "" and t.seq.isdigit() and t.txn_id == make_txn_id(
                t.bank, t.account_last4, t.date, t.amount, t.merchant_raw, int(t.seq)):
            staged.append((t, False))
            continue
        current = make_txn_id(t.bank, t.account_last4, t.date, t.amount,
                              t.merchant_raw, seq)
        if t.txn_id == current:
            staged.append((replace(t, seq=str(seq)), False))
        elif t.txn_id == legacy_txn_id(t):
            staged.append((replace(t, txn_id=current, seq=str(seq)), False))
            changed += 1
        elif (found := _find_seq(t)) is not None:
            # id 本身就是現行公式，只是位置推不出 seq（同一份帳單分批寫進 CSV、
            # 或同封信前面幾筆被對帳合併掉）；直接從雜湊試出真正的 seq。
            staged.append((replace(t, seq=str(found)), False))
        else:
            unexplained.append(t)
            staged.append((t, True))

    # 依重算後的 id 去重，保留 imported_at 最早的那列
    keep_index: dict[str, int] = {}
    for i, (t, is_unexplained) in enumerate(staged):
        if is_unexplained:
            continue
        prev = keep_index.get(t.txn_id)
        if prev is None or t.imported_at < staged[prev][0].imported_at:
            keep_index[t.txn_id] = i

    kept: list[Transaction] = []
    removed: list[Transaction] = []
    for i, (t, is_unexplained) in enumerate(staged):
        if is_unexplained or keep_index.get(t.txn_id) == i:
            kept.append(t)
        else:
            removed.append(t)

    return Migration(txns=kept, changed=changed, removed=removed,
                     unexplained=unexplained)
