"""國外交易手續費：分類判斷，以及把手續費行連回它的原始交易。

各銀行把國外手續費列成獨立一行，商家欄只留原商家的殘片，很難看懂：
- 國泰：`國外交易手續費 -MOBILE`（原商家前 6 字）
- 滙豐：`國外交易手續費FAMILYMART`（OCR，常夾雜雜訊）
- 富邦：`國外交易服務費（簽帳 1,012 )`（原交易金額）
- 中信：`國外交易手續費`（什麼都沒有）

`link_foreign_fees` 在同卡、同日的交易裡找出原交易（依殘片名稱、金額
或「手續費約 1.5%」的比例），把手續費的 merchant_norm 改成
`國外手續費 · <原商家>`，找不到就退回 `國外手續費（<殘片>）`。
只動 merchant_norm，merchant_raw/txn_id 不變，重跑結果相同。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from mailquill.schema import Transaction

_FEE_RE = re.compile(r"^\s*國外(交易|退貨)(手續|服務)費")
_AMOUNT_HINT_RE = re.compile(r"簽帳\s*(-?[\d,]+(?:\.\d+)?)")
_TYPICAL_RATE = 0.015
_RATE_RANGE = (0.005, 0.03)
_NAME_WINDOW = 5


def is_foreign_fee(merchant_raw: str) -> bool:
    return bool(_FEE_RE.match(unicodedata.normalize("NFKC", merchant_raw or "")))


def _compact(s: str) -> str:
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", s or "")).upper()


def _to_float(s: str) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def _name_matches(hint: str, merchant: str) -> bool:
    """hint 裡任一段連續 _NAME_WINDOW 字（hint 較短就整段）出現在 merchant 內。"""
    h, m = _compact(hint), _compact(merchant)
    if not h:
        return False
    n = min(_NAME_WINDOW, len(h))
    return any(h[i:i + n] in m for i in range(len(h) - n + 1))


def _ratio_distance(fee: float, parent: float) -> float | None:
    if not parent:
        return None
    ratio = abs(fee) / abs(parent)
    lo, hi = _RATE_RANGE
    return abs(ratio - _TYPICAL_RATE) if lo <= ratio <= hi else None


def _find_parent(fee: Transaction, hint: str, candidates: list[Transaction]) -> Transaction | None:
    fee_amt = _to_float(fee.amount)
    if fee_amt is None:
        return None

    m = _AMOUNT_HINT_RE.search(hint)
    if m:
        target = abs(_to_float(m.group(1)) or 0)
        hits = [c for c in candidates if abs(_to_float(c.amount) or 0) == target]
        return hits[0] if hits else None

    if hint:
        candidates = [c for c in candidates if _name_matches(hint, c.merchant_raw)]
        if len(candidates) == 1:
            return candidates[0]

    scored = [
        (d, c) for c in candidates
        if (d := _ratio_distance(fee_amt, _to_float(c.amount) or 0)) is not None
    ]
    return min(scored, key=lambda x: x[0])[1] if scored else None


def _hint(merchant_raw: str) -> str:
    rest = _FEE_RE.sub("", unicodedata.normalize("NFKC", merchant_raw), count=1)
    return rest.strip().lstrip("-").strip()


def _fee_parent_map(txns: list[Transaction]) -> dict[int, int | None]:
    """{手續費行 index: 原交易 index（找不到為 None）}。"""
    by_day: dict[tuple[str, str, str], list[int]] = {}
    for i, t in enumerate(txns):
        if not is_foreign_fee(t.merchant_raw):
            by_day.setdefault((t.bank, t.account_last4, t.date), []).append(i)

    result: dict[int, int | None] = {}
    for i, t in enumerate(txns):
        if not is_foreign_fee(t.merchant_raw):
            continue
        idxs = by_day.get((t.bank, t.account_last4, t.date), [])
        parent = _find_parent(t, _hint(t.merchant_raw), [txns[j] for j in idxs])
        result[i] = None if parent is None else next(j for j in idxs if txns[j] is parent)
    return result


def link_foreign_fees(txns: list[Transaction]) -> list[Transaction]:
    parents = _fee_parent_map(txns)
    out = list(txns)
    for i, p in parents.items():
        t = txns[i]
        label = "國外退貨手續費" if "退貨" in t.merchant_raw else "國外手續費"
        hint = _hint(t.merchant_raw)
        if p is not None:
            parent = txns[p]
            name = f"{label} · {parent.merchant_norm or parent.merchant_raw}"
        elif hint and not _AMOUNT_HINT_RE.search(hint):
            name = f"{label}（{hint}）"
        else:
            name = label
        out[i] = replace(t, merchant_norm=name)
    return out


@dataclass
class MergedTxn:
    """顯示用：txn.amount 已含併入的手續費；fee 是其中的手續費金額
    （找不到原交易、自成一列的手續費行，fee == 自身金額）。"""
    txn: Transaction
    fee: float


def _fmt_amount(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def merge_foreign_fees(txns: list[Transaction]) -> list[MergedTxn]:
    """把國外手續費併進原交易（金額相加、沿用原交易分類），手續費行本身
    不再單獨列出；找不到原交易或幣別不同的手續費保留成獨立一列。
    只給報表/網頁/Sheets 顯示用，CSV 原始資料不變。"""
    fees: dict[int, float] = {}
    absorbed: set[int] = set()
    for i, p in _fee_parent_map(txns).items():
        if p is not None and txns[p].currency == txns[i].currency:
            fees[p] = fees.get(p, 0.0) + (_to_float(txns[i].amount) or 0.0)
            absorbed.add(i)

    out = []
    for i, t in enumerate(txns):
        if i in absorbed:
            continue
        if i in fees:
            total = (_to_float(t.amount) or 0.0) + fees[i]
            out.append(MergedTxn(replace(t, amount=_fmt_amount(total)), fees[i]))
        elif is_foreign_fee(t.merchant_raw):
            out.append(MergedTxn(t, _to_float(t.amount) or 0.0))
        else:
            out.append(MergedTxn(t, 0.0))
    return out
