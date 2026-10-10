"""合併「每日通知」與「月結單」對同一筆消費的重複記錄，並清掉授權後從未
真正請款的「幽靈」通知。

國泰每日消費彙整通知（source_type=email_body）比月結單（source_type=pdf）先到，
但商店名稱常是空的、金額是授權金額；月結單來的時候會補上正式商店名稱與確認
金額。兩者的 merchant 不同，`make_txn_id` 算出的雜湊也不同，所以目前的
append-only 去重機制擋不住——同一筆消費會在 CSV 裡各留一筆，金額被重複計入。

用 (bank, account_last4, amount) 當分組 key（不含日期），組內用「授權日期
跟月結單消費日相差在 `DATE_TOLERANCE_DAYS` 天以內」建配對邊。分組刻意不含
日期，但金額（尤其是像 20、45、75 這種常見小額）常常一年內出現在完全不
相干的好幾個月份——如果整組只要「候選配對數 != 1」就判定含糊，會把兩組
各自獨立、互不衝突的真配對（如一月一筆、二月另一筆，剛好都用同一組合，
只是日期差太遠沒連在一起）誤判成同一組含糊，白白漏掉本來能收回的配對。

所以組內再用配對邊做連通分量（union-find）：每個連通分量各自判斷——通知
筆數 ≤ 月結單筆數（1+1、2+2、1+2…）就視為通知已全被月結單涵蓋，把分量裡
的通知全部丟掉、保留月結單那些筆（商店名稱正確，視為權威來源）。這裡不需要
知道「哪封通知對哪筆月結單」——反正保留的是月結單、丟的是通知，配對關係
不影響結果，總額也不會重複計入。只有通知比月結單多（同一天通知重複列了
兩次、月結單只有一筆之類）才真的含糊：多出來的那筆可能是幽靈、也可能是
月結單漏列，整個分量一律跳過不動——寧可留著讓使用者自己核對，也不要亂猜。

比對分兩輪：先只用「日期完全相同」的邊做一次連通分量，能唯一配對（1+1）
的直接合併、從組內移除——這讓「一週內買了兩次同金額的全家」這種各自有
精準日期解的情況先被乾淨解開，不會因為「日期相近」的交叉邊而混成一團；
沒解開的才留給第二輪，用容忍天數邊做連通分量，照上面的筆數規則判斷。

配對不到、且商店名稱是空字串的每日通知，若該帳戶已有月結單資料涵蓋到
「這筆日期 + 容忍天數」之後，代表對應月份的月結單早就進來了、就是沒有這筆
——最合理的解讀是授權後被取消、從未真正請款，直接當作沒發生過（`voided`，
一樣丟掉這筆暫定記錄）。但前提是這筆通知前後的月結單交易相隔不超過
`MAX_COVERAGE_GAP_DAYS`（否則可能是中間整期月結單沒進來，不能斷定沒請款）。
還沒到下一期月結單的（帳戶月結單資料還沒涵蓋到那麼
晚），就留著等下次 `reconcile` 再檢查，不會被誤判成幽靈交易。

刻意只對「商店名稱空白」的配對不到記錄做這個判定——有商店名稱的（本行
簽約特約商店，daily notice 就直接帶真名，如 `EVA AIRWAYS`、`TICKET BOARD`）
即使金額配不上也絕不能當幽靈交易砍掉：外幣消費常常因為授權/請款兩個時點
匯率認列不同，金額本來就會有出入（見 `parsers/cathay.py` docstring），這種
「配不到只是金額對不齊」跟「真的沒發生過」是兩回事，砍掉前者會弄丟一筆
使用者的實際消費紀錄。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as _date

from mailquill.schema import Transaction

DATE_TOLERANCE_DAYS = 3
# 這筆通知前後最近的兩筆月結單交易若相隔超過這麼多天，代表中間可能整期
# 月結單沒進來（解鎖失敗、沒抓到），沒辦法證明「沒請款」，不作廢。
MAX_COVERAGE_GAP_DAYS = 15


@dataclass
class ReconcileResult:
    merged: int = 0
    ambiguous: int = 0
    voided: int = 0


def _parse_date(s: str) -> _date | None:
    try:
        return _date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _connected_components(edges: list[tuple[int, int]]) -> list[tuple[list[int], list[int]]]:
    """把配對邊（email 索引, pdf 索引）分成連通分量，回傳每個分量的
    (email 索引們, pdf 索引們)。用 union-find，節點用 ("e", i)/("p", i)
    區分兩側，避免 email 跟 pdf 剛好索引相同時混在一起。
    """
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(x: tuple[str, int]) -> tuple[str, int]:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ei, pi in edges:
        for node in ((("e", ei)), (("p", pi))):
            parent.setdefault(node, node)
        ra, rb = find(("e", ei)), find(("p", pi))
        if ra != rb:
            parent[ra] = rb

    members: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    for node in parent:
        members[find(node)].append(node)

    return [
        ([i for side, i in nodes if side == "e"], [i for side, i in nodes if side == "p"])
        for nodes in members.values()
    ]


def reconcile_provisional(
    txns: list[Transaction],
    date_tolerance_days: int = DATE_TOLERANCE_DAYS,
) -> tuple[list[Transaction], ReconcileResult]:
    groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for i, t in enumerate(txns):
        groups[(t.bank, t.account_last4, t.amount)].append(i)

    latest_pdf_date: dict[tuple[str, str], str] = {}
    for t in txns:
        if t.source_type == "pdf" and t.date:
            key = (t.bank, t.account_last4)
            if key not in latest_pdf_date or t.date > latest_pdf_date[key]:
                latest_pdf_date[key] = t.date

    pdf_dates: dict[tuple[str, str], list[_date]] = defaultdict(list)
    for t in txns:
        if t.source_type == "pdf" and (d := _parse_date(t.date)):
            pdf_dates[(t.bank, t.account_last4)].append(d)
    for ds in pdf_dates.values():
        ds.sort()

    def covered(key: tuple[str, str], ed: _date) -> bool:
        """通知日期前後的月結單交易夠密，才有把握說這段期間沒有這筆請款。"""
        ds = pdf_dates.get(key, [])
        prev = max((d for d in ds if d <= ed), default=None)
        nxt = min((d for d in ds if d > ed), default=None)
        if prev is None:
            return True  # 前面沒有資料可比，維持原本「之後已有月結單」的判斷
        return nxt is not None and (nxt - prev).days <= MAX_COVERAGE_GAP_DAYS

    drop: set[int] = set()
    result = ReconcileResult()

    for idxs in groups.values():
        pdf_idxs = [i for i in idxs if txns[i].source_type == "pdf"]
        email_idxs = [i for i in idxs if txns[i].source_type == "email_body"]
        if not email_idxs:
            continue

        matched_emails: set[int] = set()
        remaining_emails = set(email_idxs)
        remaining_pdfs = set(pdf_idxs)

        exact_edges = [
            (ei, pi)
            for ei in email_idxs
            for pi in pdf_idxs
            if txns[ei].date == txns[pi].date
        ]
        for comp_emails, comp_pdfs in _connected_components(exact_edges):
            if len(comp_emails) == 1 and len(comp_pdfs) == 1:
                drop.add(comp_emails[0])
                result.merged += 1
                matched_emails.add(comp_emails[0])
                remaining_emails.discard(comp_emails[0])
                remaining_pdfs.discard(comp_pdfs[0])
            # 分量裡不只 1+1 的（如同一天出現兩筆一樣金額），留給下面用容忍
            # 天數的第二輪處理——多半還是會落回同一個含糊分量。

        edges = [
            (ei, pi)
            for ei in remaining_emails
            for pi in remaining_pdfs
            if (ed := _parse_date(txns[ei].date)) and (pd_ := _parse_date(txns[pi].date))
            and abs((ed - pd_).days) <= date_tolerance_days
        ]
        matched_emails |= {ei for ei, _ in edges}

        for comp_emails, comp_pdfs in _connected_components(edges):
            if len(comp_emails) <= len(comp_pdfs):
                drop.update(comp_emails)
                result.merged += len(comp_emails)
            else:
                result.ambiguous += 1

        for ei in email_idxs:
            if ei in matched_emails:
                continue
            t = txns[ei]
            if (t.merchant_raw or "").strip() or (t.merchant_norm or "").strip():
                continue  # 有商店名稱，配不到只是金額對不齊，不是幽靈交易
            latest = latest_pdf_date.get((t.bank, t.account_last4))
            ed = _parse_date(t.date)
            latest_d = _parse_date(latest) if latest else None
            if (latest_d and ed and (latest_d - ed).days > date_tolerance_days
                    and covered((t.bank, t.account_last4), ed)):
                drop.add(ei)
                result.voided += 1

    kept = [t for i, t in enumerate(txns) if i not in drop]
    return kept, result
