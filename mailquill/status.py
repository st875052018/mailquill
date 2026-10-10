"""`mailquill status`：各銀行的帳單／交易同步到哪裡，一眼看出有沒有漏抓最新
帳單。只讀本地 `transactions.csv` + `bills.csv`，不連 Gmail。

每家銀行：
- 最新帳單月份：優先用 `bills.csv` 的最新 period（有支援應繳金額擷取的銀行）；
  沒有的話（如 Amex）退而用月結單來源（pdf/csv）交易的最新月份估計，標
  `estimated`（顯示時加 *）。
- 月結單交易最新日／每日通知（email_body）最新日分開列：常見狀況是每日通知
  一直有進來，但月結單還沒匯入（例如富邦要手動下載 PDF）。
- 落後幾個月＝今天所在月份 − 最新帳單月份。0 ✅；1 ⚠️（本月帳單還沒進來，
  月初常見；顯示文字刻意短，Sheet 上不撐寬欄位）；≥2 ❌。
"""
from __future__ import annotations

from dataclasses import dataclass

from mailquill.bill_due import BillDue

STATEMENT_SOURCES = {"pdf", "csv"}
NOTICE_SOURCE = "email_body"


@dataclass
class BankStatus:
    bank: str
    statement_month: str | None       # "YYYY-MM"
    estimated: bool                   # True＝沒有 bills.csv 資料，依月結單交易估計
    statement_last_date: str | None
    notice_last_date: str | None
    last_imported: str | None
    months_behind: int | None


def _month_index(month: str) -> int:
    y, m = month[:7].split("-")
    return int(y) * 12 + int(m)


def _max(values) -> str | None:
    values = [v for v in values if v]
    return max(values) if values else None


def bank_statuses(txns, bills: list[BillDue], today: str) -> list[BankStatus]:
    banks = sorted({t.bank for t in txns if t.bank} | {b.bank for b in bills if b.bank})
    result = []
    for bank in banks:
        mine = [t for t in txns if t.bank == bank]
        stmt_date = _max(t.date for t in mine if t.source_type in STATEMENT_SOURCES)
        bill_month = _max(b.period for b in bills if b.bank == bank)
        month = bill_month or (stmt_date[:7] if stmt_date else None)
        result.append(BankStatus(
            bank=bank,
            statement_month=month,
            estimated=bill_month is None and month is not None,
            statement_last_date=stmt_date,
            notice_last_date=_max(t.date for t in mine if t.source_type == NOTICE_SOURCE),
            last_imported=_max(t.imported_at for t in mine),
            months_behind=_month_index(today) - _month_index(month) if month else None,
        ))
    return result


def status_mark(months_behind: int | None) -> str:
    if months_behind is None:
        return "— 沒有月結單"
    if months_behind <= 0:
        return "✅ 最新"
    if months_behind == 1:
        return "⚠️ 本月未到"
    return f"❌ 落後{months_behind}個月"


def _width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def format_status(statuses: list[BankStatus], today: str) -> str:
    header = ["銀行", "最新帳單", "月結單交易至", "每日通知至", "最後匯入", "狀態"]
    rows = [[s.bank,
             (s.statement_month or "-") + ("*" if s.estimated else ""),
             s.statement_last_date or "-",
             s.notice_last_date or "-",
             (s.last_imported or "-")[:16].replace("T", " "),
             status_mark(s.months_behind)] for s in statuses]
    widths = [max(_width(r[i]) for r in [header] + rows) for i in range(len(header) - 1)]
    lines = [f"同步狀態（今天 {today}）"]
    for r in [header] + rows:
        lines.append("  ".join(_pad(c, w) for c, w in zip(r, widths)) + "  " + r[-1])
    if any(s.estimated for s in statuses):
        lines.append("* 沒有帳單金額資料，以月結單交易的最新月份估計")
    if any(s.months_behind and s.months_behind > 0 for s in statuses):
        lines.append("有帳單沒跟上：先跑 `mailquill update`；需要手動下載的帳單"
                     "（如富邦 PDF、Amex CSV）下載後用 `mailquill ingest <檔案>` 匯入。")
    return "\n".join(lines)
