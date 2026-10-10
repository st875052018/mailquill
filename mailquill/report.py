"""由 SQLite 彙整並產生自包含、可互動篩選的 HTML dashboard。

資料以 JSON 內嵌、所有篩選與樞紐統計皆由前端 vanilla JS 計算，
無任何外部資源（CDN/字型/JS 函式庫），可離線開啟。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from mailquill.fees import merge_foreign_fees
from mailquill.schema import FIELDS, Transaction


@dataclass
class ReportData:
    total: float
    count: int
    by_l1: list[tuple[str, float]]
    by_month: list[tuple[str, float]]
    rows: list[dict]


def _to_float(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def aggregate(db_path: str) -> ReportData:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        total = cur.execute(
            "SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) FROM transactions"
        ).fetchone()[0]
        count = cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        by_l1 = cur.execute(
            "SELECT category_l1, COALESCE(SUM(CAST(amount AS REAL)), 0) AS s "
            "FROM transactions GROUP BY category_l1 ORDER BY s DESC"
        ).fetchall()
        by_month = cur.execute(
            "SELECT substr(date, 1, 7) AS ym, "
            "COALESCE(SUM(CAST(amount AS REAL)), 0) "
            "FROM transactions GROUP BY ym ORDER BY ym"
        ).fetchall()
        detail = cur.execute(
            f"SELECT {', '.join(FIELDS)} FROM transactions "
            "ORDER BY date DESC, rowid DESC"
        ).fetchall()
        txns = [
            Transaction.from_row({k: ("" if v is None else str(v)) for k, v in zip(FIELDS, r)})
            for r in detail
        ]
        # 國外手續費併進原消費一起顯示（見 fees.merge_foreign_fees）
        rows = [
            {"date": t.date, "month": (t.date or "")[:7], "merchant": t.merchant_norm,
             "amount": _to_float(t.amount), "l1": t.category_l1, "l2": t.category_l2,
             "bank": t.bank, "last4": t.account_last4, "currency": t.currency or "TWD",
             "fee": m.fee}
            for m in merge_foreign_fees(txns)
            for t in [m.txn]
        ]
        # total/by_l1/by_month 混合所有幣別加總，不被 render_html 使用（只給
        # 想直接用 aggregate() 的呼叫者/測試做粗略檢視）；畫面上實際顯示的
        # 統計數字改由 JS 端依 state.currency 篩選單一幣別後才計算，見 _JS。
        return ReportData(
            total=float(total),
            count=int(count),
            by_l1=[(a, float(b)) for a, b in by_l1],
            by_month=[(a, float(b)) for a, b in by_month],
            rows=rows,
        )
    finally:
        conn.close()


def _embed(rows: list[dict]) -> str:
    """把交易列轉成可安全內嵌於 <script> 的 JSON。"""
    data = [
        {"date": r["date"], "month": r["month"], "amount": r["amount"],
         "l1": r["l1"], "l2": r["l2"], "bank": r["bank"],
         "merchant": r["merchant"], "last4": r.get("last4", ""),
         "currency": r.get("currency", "TWD"), "fee": r.get("fee", 0.0)}
        for r in rows
    ]
    s = json.dumps(data, ensure_ascii=False)
    # 轉義 < > & 以免內容破壞 <script> 區塊（XSS 安全）
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


_STYLE = """
:root {
  --bg:#eef0f3; --bgimg:none; --panel:#ffffff; --panel2:#fbfcfd;
  --text:#191b1f; --muted:#727986; --soft:#414855;
  --border:#e3e6ec; --track:#eceef2; --card:#fbfbfd; --tline:#eef0f3;
  --chipOn:#20242b; --accent:#a9791c; --gold:#c99a52; --goldSoft:#e6cf9c;
  --resetBg:#ebedf1; --resetFg:#525a67;
  --pos:#c42f4b; --neg:#178a48;                  /* 花費=紅, 退款=綠 */
  --barPos1:#d0475f; --barPos2:#eb8ba0; --barNeg1:#2fae63; --barNeg2:#8fd6ac;
  --shadow:0 1px 2px rgba(20,24,33,.05), 0 6px 22px rgba(20,24,33,.06);
  --ring:rgba(201,154,82,.45);
}
[data-theme="dark"] {
  --bg:#0e1116; --bgimg:radial-gradient(1100px 520px at 82% -8%, rgba(216,178,110,.10), transparent 60%);
  --panel:#161b24; --panel2:#1a212c; --text:#eae5d9; --muted:#8b95a6; --soft:#c3c9d4;
  --border:#28303c; --track:#222932; --card:#1b212c; --tline:#252d38;
  --chipOn:#eae5d9; --accent:#d8b26e; --gold:#d8b26e; --goldSoft:#8a6a30;
  --resetBg:#222932; --resetFg:#c3c9d4;
  --pos:#ff6b85; --neg:#43c98a;
  --barPos1:#c9556c; --barPos2:#7c3244; --barNeg1:#2c9c63; --barNeg2:#17583a;
  --shadow:0 1px 2px rgba(0,0,0,.45), 0 10px 34px rgba(0,0,0,.38);
  --ring:rgba(216,178,110,.40);
}
* { box-sizing: border-box; }
:root { --serif: Georgia, "Songti TC", "Noto Serif TC", "Times New Roman", serif;
        --sans: -apple-system, "PingFang TC", "Microsoft JhengHei", "Segoe UI", sans-serif; }
body { font-family: var(--sans); margin: 0; padding: 0; color: var(--text);
       background-color: var(--bg); background-image: var(--bgimg);
       background-attachment: fixed; -webkit-font-smoothing: antialiased; line-height: 1.5; }
.container { margin: 1.5rem 50px; }
@media (min-width: 1360px) { .container { margin: 2.25rem auto; max-width: 1240px; padding: 0 24px; } }

/* ---- brand header ---- */
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.brand { display: flex; align-items: center; gap: 0.85rem; }
.brand .mark { flex: none; width: 40px; height: 40px; }
.wordmark { font-family: var(--serif); font-size: 1.85rem; font-weight: 700; letter-spacing: .3px;
            line-height: 1.05; color: var(--text); }
.wordmark span { color: var(--gold); }
.theme-btn { border: 1px solid var(--border); background: var(--panel); color: var(--soft);
             border-radius: 999px; padding: 0.4rem 0.85rem; cursor: pointer; font-size: 0.82rem; flex: none;
             transition: border-color .15s, color .15s; }
.theme-btn:hover { border-color: var(--gold); color: var(--text); }
.hairline { height: 1px; border: 0; margin: 1rem 0 1.25rem;
            background: linear-gradient(90deg, var(--gold), var(--border) 32%, transparent); }
h1 { font-family: var(--serif); font-size: 1.5rem; margin: 0 0 0.25rem; }
h2 { font-family: var(--serif); font-size: 1.15rem; margin: 0 0 0.9rem; display: flex; align-items: center; }
h2::before { content: ""; display: inline-block; width: 3px; height: 0.95em; margin-right: 0.55rem;
             border-radius: 2px; background: linear-gradient(var(--gold), var(--goldSoft)); }
h3 { font-size: 0.8rem; font-weight: 600; letter-spacing: .4px; margin: 0 0 0.7rem; color: var(--soft);
     text-transform: uppercase; }
.sub { color: var(--muted); margin: 0.25rem 0 0; font-size: 0.83rem; }
.pos { color: var(--pos); }
.neg { color: var(--neg); }

.tabbar { display: flex; gap: 0.35rem; margin: 0.25rem 0 1.5rem; border-bottom: 1px solid var(--border); }
.tabbar button { border: none; background: none; padding: 0.55rem 1.1rem 0.7rem; cursor: pointer;
                 font-size: 0.92rem; color: var(--muted); border-bottom: 2px solid transparent;
                 margin-bottom: -1px; transition: color .15s; }
.tabbar button:hover { color: var(--soft); }
.tabbar button.on { color: var(--text); border-bottom-color: var(--gold); font-weight: 600; }
.tabpane { display: none; }
.tabpane.active { display: block; animation: fade .25s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

.filters { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
           padding: 0.9rem 1.3rem; margin-bottom: 1.4rem; box-shadow: var(--shadow); }
.toprow { display: flex; align-items: center; gap: 0.75rem 1.4rem; flex-wrap: wrap; }
.toprow .reset { margin-left: auto; }
.fg { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
.fg > .lbl, .frow > .lbl { color: var(--muted); font-size: 0.72rem; letter-spacing: .5px;
                           text-transform: uppercase; font-weight: 600; }
.frow { display: flex; align-items: flex-start; gap: 0.6rem; flex-wrap: wrap; margin: 0.5rem 0 0.1rem; }
.frow > .lbl { width: 3rem; flex: none; padding-top: 0.35rem; line-height: 1.3; }
.chips { display: flex; gap: 0.4rem; flex-wrap: wrap; flex: 1; }
.chip { border: 1px solid var(--border); background: var(--panel); border-radius: 999px;
        padding: 0.24rem 0.75rem; font-size: 0.85rem; cursor: pointer; display: inline-flex;
        align-items: center; gap: 0.35rem; color: var(--text); transition: all .13s; }
.chip:hover { border-color: var(--gold); }
.chip.sm { padding: 0.14rem 0.6rem; font-size: 0.8rem; }
.chip.on { background: var(--gold); color: #1a130a; border-color: var(--gold); font-weight: 600;
           box-shadow: 0 1px 6px var(--ring); }
.chip .dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; display: inline-block;
             box-shadow: 0 0 0 1px rgba(0,0,0,.08) inset; }
.toggle.sm button { padding: 0.12rem 0.6rem; font-size: 0.75rem; }
.cattree { display: flex; gap: 0.95rem 1.1rem; flex-wrap: wrap; flex: 1; align-items: flex-start;
           max-height: 15rem; overflow-y: auto; padding: 0.15rem 4px 0.15rem 0; }
.cattree::-webkit-scrollbar { width: 8px; }
.cattree::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
.cattree.exclude .chip.on { background: var(--pos); border-color: var(--pos);
                            color: #fff; text-decoration: line-through; box-shadow: none; }
.l1chip.partial { background: var(--panel); color: var(--accent); border-color: var(--gold);
                  border-style: dashed; box-shadow: none; font-weight: 600; }
.cattree.exclude .l1chip.partial { color: var(--pos); border-color: var(--pos); }
.catcol { display: flex; flex-direction: column; gap: 0.32rem; align-items: flex-start; }
.l1chip { font-weight: 600; }
.l2col { display: flex; flex-direction: column; gap: 0.28rem; align-items: flex-start;
         border-left: 1px solid var(--border); padding-left: 0.5rem; margin-left: 0.4rem; }
select { padding: 0.3rem 0.5rem; border-radius: 8px; border: 1px solid var(--border);
         background: var(--panel); color: var(--text); font-size: 0.85rem; }
select:focus-visible, .chip:focus-visible, button:focus-visible, input:focus-visible {
         outline: 2px solid var(--ring); outline-offset: 1px; }
.reset { border: 1px solid var(--border); background: var(--resetBg); color: var(--resetFg);
         border-radius: 999px; padding: 0.34rem 0.85rem; cursor: pointer; font-size: 0.8rem; }
.reset:hover { border-color: var(--gold); color: var(--text); }

section { background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
          padding: 1.3rem 1.4rem; margin-bottom: 1.4rem; box-shadow: var(--shadow); }
.sec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
.sec-head h2 { margin: 0; }
.toggle button { border: 1px solid var(--border); background: var(--panel); padding: 0.22rem 0.75rem;
                 font-size: 0.8rem; cursor: pointer; color: var(--resetFg); }
.toggle button.on { background: var(--chipOn); color: var(--panel); border-color: var(--chipOn); }
.toggle button:first-child { border-radius: 8px 0 0 8px; }
.toggle button:last-child { border-radius: 0 8px 8px 0; border-left: none; }
.feenote { font-size: .75rem; font-weight: normal; color: var(--muted); margin-top: .15rem; white-space: nowrap; }
.cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.card { background: linear-gradient(160deg, var(--panel2), var(--card)); border: 1px solid var(--border);
        border-radius: 14px; padding: 0.9rem 1.15rem 1rem; min-width: 138px; position: relative;
        overflow: hidden; box-shadow: var(--shadow); }
.card::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
                background: linear-gradient(var(--gold), var(--goldSoft)); }
.card .k { color: var(--muted); font-size: 0.72rem; letter-spacing: .4px; }
.card .v { font-size: 1.55rem; font-weight: 700; margin-top: 0.25rem; font-variant-numeric: tabular-nums;
           letter-spacing: -.3px; }
.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.75rem; }
.three { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.25rem 1.75rem; }
.col-wide { grid-column: 1 / -1; }
.split2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1.75rem; }
.mtop { margin-top: 1.5rem; }
@media (max-width: 640px) { .split2 { grid-template-columns: 1fr; } }
.bar-row { display: flex; align-items: center; gap: 0.6rem; margin: 0.35rem 0; }
.bar-label { width: 7rem; flex: none; font-size: 0.85rem; white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
.bar-label.wide { width: 15rem; white-space: normal; overflow: visible; line-height: 1.25; }
.bar-track { flex: 1; background: var(--track); border-radius: 999px; height: 12px; min-width: 40px;
             box-shadow: inset 0 1px 2px rgba(0,0,0,.06); }
.bar-fill { background: linear-gradient(90deg,var(--barPos1),var(--barPos2)); height: 12px;
            border-radius: 999px; transition: width .4s cubic-bezier(.2,.7,.3,1); }
.bar-fill.neg { background: linear-gradient(90deg,var(--barNeg1),var(--barNeg2)); }
.bar-val { width: 5.5rem; flex: none; text-align: right; font-size: 0.85rem; font-weight: 600;
           font-variant-numeric: tabular-nums; }
.badge { color: #fff; border-radius: 999px; padding: 0.08rem 0.5rem; font-size: 0.72rem;
         font-weight: 600; white-space: nowrap; letter-spacing: .3px; }
.pie { display: flex; gap: 1.25rem; align-items: center; flex-wrap: wrap; }
.pie svg { filter: drop-shadow(0 4px 12px rgba(0,0,0,.12)); }
.pie circle.hole { fill: var(--panel); }
.legend { font-size: 0.82rem; }
.leg { display: flex; align-items: center; gap: 0.45rem; margin: 0.22rem 0; }
.sw { width: 0.7rem; height: 0.7rem; border-radius: 3px; display: inline-block; flex: none; }
.empty { color: var(--muted); font-size: 0.85rem; padding: 0.5rem 0; }
.tablewrap { overflow-x: auto; border-radius: 10px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.55rem 0.6rem; border-bottom: 1px solid var(--tline);
         font-size: 0.85rem; white-space: nowrap; }
thead th { color: var(--muted); font-size: 0.72rem; font-weight: 600; letter-spacing: .5px;
           text-transform: uppercase; border-bottom: 1px solid var(--border); }
tbody tr { transition: background .12s; }
tbody tr:hover { background: var(--track); }
td.amt, th.amt { text-align: right; font-variant-numeric: tabular-nums; }
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--gold); }
.sort-ind { font-size: 0.72em; margin-left: 3px; color: var(--muted); opacity: .45;
            font-variant-numeric: normal; }
.sort-ind.on { color: var(--gold); opacity: 1; }
th.sortable:hover .sort-ind { color: var(--gold); opacity: .9; }
/* 明細表：除「商家」(第2欄)外全部置中 */
[data-pane="detail"] th, [data-pane="detail"] td { text-align: center; }
[data-pane="detail"] th.amt, [data-pane="detail"] td.amt { text-align: center; }
[data-pane="detail"] th:nth-child(2), [data-pane="detail"] td:nth-child(2) { text-align: left; }
input.tag { font-size: 0.85rem; padding: 0.28rem 0.45rem; border: 1px solid var(--border);
            border-radius: 8px; background: var(--panel); color: var(--text); }
#rules-out { display: none; width: 100%; height: 9rem; margin-top: 0.75rem; box-sizing: border-box;
             font-family: ui-monospace, "SF Mono", monospace; font-size: 0.8rem; border: 1px solid var(--border);
             border-radius: 10px; padding: 0.7rem; background: var(--card); color: var(--text); line-height: 1.5; }
.hint { color: var(--muted); font-size: 0.82rem; margin: 0 0 0.85rem; line-height: 1.6; }
.btn { border: 1px solid var(--gold); background: linear-gradient(var(--gold), var(--goldSoft));
       color: #1a130a; border-radius: 999px; padding: 0.34rem 0.95rem; cursor: pointer;
       font-size: 0.82rem; font-weight: 600; box-shadow: 0 1px 8px var(--ring); }
.btn:hover { filter: brightness(1.04); }
code { background: var(--track); padding: 0.08rem 0.35rem; border-radius: 5px;
       font-family: ui-monospace, "SF Mono", monospace; font-size: 0.82em; }
"""

_BODY = """
<div class="container">
<div class="topbar">
  <div class="brand">
    <svg class="mark" viewBox="0 0 40 40" aria-hidden="true">
      <defs>
        <linearGradient id="qg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#f3d9a0"/><stop offset="1" stop-color="#c99a52"/>
        </linearGradient>
      </defs>
      <circle cx="20" cy="20" r="19" fill="none" stroke="url(#qg)" stroke-width="1.5" opacity="0.55"/>
      <path d="M11 30 C19 24 26 15 31 8 C27 17 21 26 14 32 Z" fill="url(#qg)"/>
      <path d="M31 8 C25 16 19 24 14 32" fill="none" stroke="#8a6a30" stroke-width="0.8" opacity="0.6"/>
      <path d="M11 30 L8 35 L15 31 Z" fill="#f3d9a0"/>
    </svg>
    <div>
      <div class="wordmark">Mail<span>quill</span></div>
      <p class="sub">財務 Dashboard · 本地離線 · 淨額（<span class="pos">紅＝花費</span> / <span class="neg">綠＝退款</span>）</p>
    </div>
  </div>
  <button id="theme-btn" class="theme-btn">🌙 深色</button>
</div>
<hr class="hairline">
<div class="filters">
  <div class="frow toprow">
    <span class="fg"><span class="lbl">月份</span>
      <select id="f-from"></select><span>～</span><select id="f-to"></select></span>
    <span class="fg"><span class="lbl">銀行</span><span class="chips" id="f-banks"></span></span>
    <span class="fg" id="f-currency-wrap" style="display:none">
      <span class="lbl">幣別</span><span class="chips" id="f-currency"></span></span>
    <span class="fg"><span class="lbl">品類模式</span>
      <span class="toggle sm"><button class="on" data-l2mode="include">包含</button><button data-l2mode="exclude">排除</button></span>
    </span>
    <button id="f-reset" class="reset">重設</button>
  </div>
  <div class="frow"><span class="lbl">分類<br>品類</span><div class="cattree" id="f-cattree"></div></div>
</div>
<div class="tabbar">
  <button class="on" data-tab="overview">總覽</button>
  <button data-tab="breakdown">分類拆解</button>
  <button data-tab="detail">明細</button>
  <button data-tab="uncat">未分類整理</button>
</div>
<datalist id="l1list">
  <option>食</option><option>衣</option><option>住</option><option>行</option>
  <option>育</option><option>樂</option><option>醫療</option><option>保險</option><option>其他</option>
</datalist>
<section class="tabpane active" data-pane="overview">
  <h2>總覽</h2>
  <div id="cards" class="cards"></div>
  <div class="two">
    <div><h3>月度趨勢</h3><div id="v-month"></div></div>
    <div><h3>依銀行</h3><div id="v-bank"></div></div>
  </div>
</section>
<section class="tabpane" data-pane="breakdown">
  <div class="sec-head"><h2>分類拆解</h2>
    <span class="toggle">
      <button class="on" data-mode="bar">長條</button><button data-mode="pie">圓餅</button>
    </span>
  </div>
  <div class="split2">
    <div><h3>依分類（第一層）</h3><div id="v-l1"></div></div>
    <div><h3>依品類（第二層 Top 12）</h3><div id="v-l2"></div></div>
  </div>
  <div class="mtop"><h3>商家 Top 15</h3><div id="v-merchant"></div></div>
</section>
<section class="tabpane" data-pane="detail">
  <h2>明細 <span id="detail-note" class="sub"></span></h2>
  <div class="tablewrap"><table>
    <thead><tr>
      <th class="sortable" data-sort="date">日期</th>
      <th class="sortable" data-sort="merchant">商家</th>
      <th class="sortable amt" data-sort="amount">金額</th>
      <th class="sortable" data-sort="l1">第一層</th>
      <th class="sortable" data-sort="l2">第二層</th>
      <th class="sortable" data-sort="bank">銀行</th>
    </tr></thead>
    <tbody id="detail"></tbody>
  </table></div>
</section>
<section class="tabpane" data-pane="uncat">
  <div class="sec-head"><h2>未分類整理</h2>
    <button id="gen-rules" class="btn">產生 categories.yaml 規則</button>
  </div>
  <p class="hint">為未分類商家填上第一/第二層（可縮短「關鍵字」讓比對更精準，例如長店名只留品牌兩三字）。
    按右上角產生規則 → 複製貼進 <code>categories.yaml</code> → 跑 <code>mailquill rebuild</code>，即永久生效。</p>
  <div class="tablewrap"><table>
    <thead><tr><th>關鍵字（預設＝商家）</th><th class="amt">金額</th><th>筆數</th>
      <th>第一層</th><th>第二層</th></tr></thead>
    <tbody id="uncat-rows"></tbody>
  </table></div>
  <textarea id="rules-out" readonly></textarea>
</section>
</div>
"""

_JS = """
const BANKS = {
  Cathay:{s:"國泰",c:"#0a8f4e"}, UnionBank:{s:"聯邦",c:"#c8102e"},
  Taishin:{s:"台新",c:"#00a0a0"}, Fubon:{s:"富邦",c:"#1a3b8b"},
  ExampleBank:{s:"範例",c:"#888888"}
};
const PALETTE = ['#4f8cff','#34c759','#ff9f0a','#ff375f','#5e5ce6','#00c7be',
                 '#ffd60a','#bf5af2','#8e8e93','#0a8f4e','#ff6482','#64d2ff'];
function bankMeta(b){ return BANKS[b] || {s: b || "其他", c:"#888888"}; }
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(v){ return Math.round(v).toLocaleString('en-US'); }
function badge(b){ const m=bankMeta(b);
  return '<span class="badge" style="background:'+m.c+'">'+esc(m.s)+'</span>'; }
function sliceColor(label,i,opt){ return opt.bank ? bankMeta(label).c : PALETTE[i%PALETTE.length]; }

const months = [...new Set(DATA.map(r=>r.month))].filter(Boolean).sort();
const allBanks = [...new Set(DATA.map(r=>r.bank))];
const allCurrencies = [...new Set(DATA.map(r=>r.currency))];
function defaultCurrency(){
  // 依筆數最多的幣別當預設，避免一開啟就把不同幣別金額混在一起加總。
  const cnt = new Map();
  for(const r of DATA){ cnt.set(r.currency, (cnt.get(r.currency)||0)+1); }
  return ([...cnt.entries()].sort((a,b)=>b[1]-a[1])[0] || ['TWD'])[0];
}
const L1ORDER = ['Food','Housing','Transport','Education','Entertainment','Insurance','Medical','Shopping','Other','Uncategorized'];
const l1rank = x => { const i=L1ORDER.indexOf(x); return i<0 ? 999 : i; };
const allCats = [...new Set(DATA.map(r=>r.l1))]
  .sort((a,b)=> l1rank(a)-l1rank(b) || (a<b?-1:a>b?1:0));
const l2ByL1 = (()=>{ const m=new Map();
  for(const r of DATA){ if(!m.has(r.l1)) m.set(r.l1,new Map());
    const mm=m.get(r.l1), k=r.l2||""; mm.set(k,(mm.get(k)||0)+Math.abs(r.amount)); }
  return m; })();
const COLLABEL = {date:'日期',merchant:'商家',amount:'金額',l1:'第一層',l2:'第二層',bank:'銀行'};
const SEP = '\\u241f';                          // 葉節點 key = 第一層 + SEP + 第二層
const leafKey = r => r.l1 + SEP + (r.l2||"");
const l1LeafKeys = l1 => [...(l2ByL1.get(l1)||new Map()).keys()].map(c=>l1+SEP+(c||""));
const state = { banks:new Set(), leaves:new Set(), l2mode:'include',
                from: months[0]||"", to: months[months.length-1]||"", chartMode:'bar',
                currency: defaultCurrency() };
const sortState = { col:'date', dir:'desc' };

function inCat(r){
  if(state.leaves.size===0) return true;                 // 都沒選 → 全部
  const has = state.leaves.has(leafKey(r));
  return state.l2mode==='exclude' ? !has : has;          // 包含=命中顯示；排除=命中隱藏
}
function filtered(){
  return DATA.filter(r=>
    (state.banks.size===0 || state.banks.has(r.bank)) &&
    r.currency === state.currency &&
    inCat(r) &&
    (!r.month || (r.month>=state.from && r.month<=state.to)));
}
function groupSum(arr, key){
  const m = new Map();
  for(const r of arr){ const k=key(r)||"(無)"; m.set(k,(m.get(k)||0)+r.amount); }
  return [...m.entries()].sort((a,b)=>Math.abs(b[1])-Math.abs(a[1]));
}
function bars(items, opt){
  opt = opt || {};
  if(!items.length) return '<div class="empty">（無資料）</div>';
  const max = Math.max(...items.map(i=>Math.abs(i[1]))) || 1;
  const lw = opt.wideLabel ? ' wide' : '';
  return items.map(([label,val])=>{
    const w = (Math.abs(val)/max*100).toFixed(1);
    const lab = opt.bank ? badge(label) : esc(label);
    const neg = val<0 ? ' neg' : '';
    return '<div class="bar-row"><div class="bar-label'+lw+'">'+lab+'</div>'+
      '<div class="bar-track"><div class="bar-fill'+neg+'" style="width:'+w+'%"></div></div>'+
      '<div class="bar-val '+(val<0?'neg':'pos')+'">'+fmt(val)+'</div></div>';
  }).join('');
}
function donut(items, opt){
  opt = opt || {};
  const pos = items.filter(i=>i[1]>0);
  if(!pos.length) return bars(items, opt);     // 全負/空 → 退回長條
  const tot = pos.reduce((s,i)=>s+i[1],0);
  let seg='';
  if(pos.length===1){
    seg = '<circle cx="80" cy="80" r="72" fill="'+sliceColor(pos[0][0],0,opt)+'"/>';
  } else {
    let a=-Math.PI/2;
    pos.forEach(([label,val],i)=>{
      const f=val/tot, a1=a+f*2*Math.PI;
      const x0=(80+72*Math.cos(a)).toFixed(2), y0=(80+72*Math.sin(a)).toFixed(2);
      const x1=(80+72*Math.cos(a1)).toFixed(2), y1=(80+72*Math.sin(a1)).toFixed(2);
      seg += '<path d="M80 80 L'+x0+' '+y0+' A72 72 0 '+(f>0.5?1:0)+' 1 '+x1+' '+y1+' Z" fill="'+sliceColor(label,i,opt)+'"/>';
      a=a1;
    });
  }
  const legend = pos.map(([label,val],i)=>{
    const lab = opt.bank ? bankMeta(label).s : label;
    return '<div class="leg"><span class="sw" style="background:'+sliceColor(label,i,opt)+'"></span>'+
      esc(lab)+' '+(val/tot*100).toFixed(0)+'% ('+fmt(val)+')</div>';
  }).join('');
  return '<div class="pie"><svg viewBox="0 0 160 160" width="150" height="150">'+seg+
    '<circle class="hole" cx="80" cy="80" r="44"/></svg><div class="legend">'+legend+'</div></div>';
}
function chart(items, opt){ return state.chartMode==='pie' ? donut(items, opt) : bars(items, opt); }
// 併進原消費的國外手續費：金額欄顯示總額，下方拆成「消費 + 手續費」
function feeNote(r){ return (r.fee && r.fee!==r.amount)
  ? '<div class="feenote">'+fmt(r.amount-r.fee)+' + 手續費 '+fmt(r.fee)+'</div>' : ''; }
function card(k,v){ return '<div class="card"><div class="k">'+k+'</div><div class="v">'+v+'</div></div>'; }

function renderCurrencyChips(){
  const wrap = document.getElementById('f-currency-wrap');
  if(allCurrencies.length<=1){ wrap.style.display='none'; return; }
  wrap.style.display='';
  document.getElementById('f-currency').innerHTML = allCurrencies.map(c=>{
    const on = c===state.currency ? ' on' : '';
    return '<button class="chip'+on+'" data-currency="'+esc(c)+'">'+esc(c)+'</button>';
  }).join('');
}
function renderChips(){
  document.getElementById('f-banks').innerHTML = allBanks.map(b=>{
    const m=bankMeta(b); const on=state.banks.has(b)?' on':'';
    return '<button class="chip'+on+'" data-bank="'+esc(b)+'">'+
      '<span class="dot" style="background:'+m.c+'"></span>'+esc(m.s)+'</button>';
  }).join('');
  // 分類/品類樹：每個第一層一欄，欄內垂直展開其第二層。點第一層＝全選/全取消其品類；
  // 只選部分品類時，第一層顯示「部分選取」(虛線)。篩選由葉節點(品類)驅動。
  const tree = document.getElementById('f-cattree');
  tree.className = 'cattree' + (state.l2mode==='exclude' ? ' exclude' : '');  // 排除模式視覺提示
  tree.innerHTML = allCats.map(l1=>{
    const mm = l2ByL1.get(l1);
    const l2s = mm ? [...mm.keys()].sort((a,b)=>mm.get(b)-mm.get(a)) : [];
    const keys = l2s.map(c=>l1+SEP+(c||""));
    const sel = keys.filter(k=>state.leaves.has(k)).length;
    const on = keys.length && sel===keys.length ? ' on' : (sel ? ' partial' : '');
    const l2html = l2s.map(c=>{ const k=l1+SEP+(c||""); const o=state.leaves.has(k)?' on':'';
      return '<button class="chip sm'+o+'" data-leaf="'+esc(k)+'">'+esc(c||'(無)')+'</button>'; }).join('');
    return '<div class="catcol">'+
      '<button class="chip l1chip'+on+'" data-cat="'+esc(l1)+'">'+esc(l1)+'</button>'+
      (l2html ? '<div class="l2col">'+l2html+'</div>' : '')+
      '</div>';
  }).join('');
}
function render(){
  const rows = filtered();
  const total = rows.reduce((s,r)=>s+r.amount,0);
  const mset = new Set(rows.map(r=>r.month).filter(Boolean));
  const sign = v => '<span class="'+(v<0?'neg':'pos')+'">'+fmt(v)+'</span>';
  document.getElementById('cards').innerHTML =
    card('淨消費 ('+esc(state.currency)+')', sign(total)) + card('筆數', rows.length) +
    card('月份數', mset.size) +
    card('平均/月 ('+esc(state.currency)+')', mset.size? sign(total/mset.size) : '0') +
    card('國外手續費 ('+esc(state.currency)+')', sign(rows.reduce((s,r)=>s+(r.fee||0),0)));
  const byMonth = groupSum(rows, r=>r.month).sort((a,b)=> a[0]<b[0]?-1:a[0]>b[0]?1:0);
  document.getElementById('v-month').innerHTML = bars(byMonth);
  document.getElementById('v-bank').innerHTML = chart(groupSum(rows, r=>r.bank), {bank:true});
  document.getElementById('v-l1').innerHTML = chart(groupSum(rows, r=>r.l1));
  document.getElementById('v-l2').innerHTML = bars(groupSum(rows, r=>r.l2).slice(0,12));
  document.getElementById('v-merchant').innerHTML =
    bars(groupSum(rows, r=>r.merchant).slice(0,15), {wideLabel:true});
  const sorted = rows.slice().sort((a,b)=>{
    const c=sortState.col, d=sortState.dir;
    if(c==='amount') return d==='asc' ? a.amount-b.amount : b.amount-a.amount;
    const av=String(a[c]||''), bv=String(b[c]||'');
    if(av===bv) return 0;
    return d==='asc' ? (av<bv?-1:1) : (av>bv?-1:1);
  });
  const cap = 1000, shown = sorted.slice(0, cap);
  document.getElementById('detail').innerHTML = shown.map(r=>
    '<tr><td>'+esc(r.date)+'</td><td>'+esc(r.merchant)+'</td>'+
    '<td class="amt '+(r.amount<0?'neg':'pos')+'">'+fmt(r.amount)+feeNote(r)+'</td><td>'+esc(r.l1)+'</td>'+
    '<td>'+esc(r.l2)+'</td><td>'+badge(r.bank)+'</td></tr>').join('');
  document.getElementById('detail-note').textContent =
    rows.length + ' 筆' + (rows.length>cap ? '（僅顯示前 '+cap+'）' : '');
  paintSort();
}
function paintSort(){
  document.querySelectorAll('th.sortable').forEach(th=>{
    const c=th.dataset.sort, active=c===sortState.col;
    const ind = active ? (sortState.dir==='asc'?'▲':'▼') : '⇅';   // ⇅=可排序提示
    th.innerHTML = esc(COLLABEL[c]) + ' <span class="sort-ind'+(active?' on':'')+'">'+ind+'</span>';
  });
}
function renderUncat(){
  const m = new Map();   // "商家|幣別" -> {sum, cnt, merchant, currency}
  for(const r of DATA){
    if(r.l1 !== 'Uncategorized') continue;
    const k = (r.merchant||'') + SEP + (r.currency||'');
    const e = m.get(k) || {sum:0, cnt:0, merchant:r.merchant, currency:r.currency};
    e.sum += r.amount; e.cnt++; m.set(k, e);  // 淨額（非絕對值加總）：訂房被全額退款的話淨影響是 0，
                                                // 不該因為 abs 加總被誤判成大額待分類
  }
  const items = [...m.values()].sort((a,b)=>Math.abs(b.sum)-Math.abs(a.sum));
  const tb = document.getElementById('uncat-rows');
  if(!items.length){ tb.innerHTML = '<tr><td colspan="5" class="empty">沒有未分類，讚！</td></tr>'; return; }
  tb.innerHTML = items.map(e=>
    '<tr>'+
    '<td><input class="tag kw" value="'+esc(e.merchant)+'" style="width:15rem"></td>'+
    '<td class="amt">'+fmt(e.sum)+' '+esc(e.currency)+'</td><td>'+e.cnt+'</td>'+
    '<td><input class="tag l1" list="l1list" style="width:5rem"></td>'+
    '<td><input class="tag l2" style="width:8rem"></td>'+
    '</tr>').join('');
}
function fillMonths(){
  const opts = months.map(m=>'<option value="'+esc(m)+'">'+esc(m)+'</option>').join('');
  const f=document.getElementById('f-from'), t=document.getElementById('f-to');
  f.innerHTML=opts; t.innerHTML=opts; f.value=state.from; t.value=state.to;
}
document.getElementById('f-banks').addEventListener('click', e=>{
  const el=e.target.closest('[data-bank]'); if(!el) return;
  const v=el.dataset.bank; state.banks.has(v)?state.banks.delete(v):state.banks.add(v);
  renderChips(); render();
});
document.getElementById('f-currency').addEventListener('click', e=>{
  const el=e.target.closest('[data-currency]'); if(!el) return;
  state.currency = el.dataset.currency;
  renderCurrencyChips(); render();
});
document.getElementById('f-cattree').addEventListener('click', e=>{
  const c=e.target.closest('[data-cat]');
  if(c){ const keys=l1LeafKeys(c.dataset.cat);
         const allOn=keys.length && keys.every(k=>state.leaves.has(k));   // 全選了→再點取消整包
         keys.forEach(k=> allOn ? state.leaves.delete(k) : state.leaves.add(k));
         renderChips(); render(); return; }
  const l=e.target.closest('[data-leaf]');
  if(l){ const k=l.dataset.leaf; state.leaves.has(k)?state.leaves.delete(k):state.leaves.add(k);
         renderChips(); render(); }
});
document.querySelectorAll('[data-mode]').forEach(btn=>btn.addEventListener('click', ()=>{
  state.chartMode = btn.dataset.mode;
  document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('on', b===btn));
  render();
}));
document.querySelectorAll('[data-l2mode]').forEach(btn=>btn.addEventListener('click', ()=>{
  state.l2mode = btn.dataset.l2mode;
  document.querySelectorAll('[data-l2mode]').forEach(b=>b.classList.toggle('on', b===btn));
  renderChips(); render();   // renderChips: 更新排除模式的視覺提示
}));
document.querySelector('th.sortable').closest('thead').addEventListener('click', e=>{
  const th=e.target.closest('th[data-sort]'); if(!th) return;
  const c=th.dataset.sort;
  if(sortState.col===c){ sortState.dir = sortState.dir==='asc'?'desc':'asc'; }
  else { sortState.col=c; sortState.dir = c==='amount'?'desc':'asc'; }  // 金額預設大到小
  render();
});
document.getElementById('f-from').addEventListener('change', e=>{ state.from=e.target.value; render(); });
document.getElementById('f-to').addEventListener('change', e=>{ state.to=e.target.value; render(); });
document.getElementById('f-reset').addEventListener('click', ()=>{
  state.banks.clear(); state.leaves.clear(); state.l2mode='include';
  state.from=months[0]||""; state.to=months[months.length-1]||"";
  state.currency = defaultCurrency();
  document.querySelectorAll('[data-l2mode]').forEach(x=>x.classList.toggle('on', x.dataset.l2mode==='include'));
  fillMonths(); renderChips(); renderCurrencyChips(); render();
});
document.getElementById('gen-rules').addEventListener('click', ()=>{
  const lines = [];
  document.querySelectorAll('#uncat-rows tr').forEach(tr=>{
    const kw=tr.querySelector('.kw'), l1=tr.querySelector('.l1'), l2=tr.querySelector('.l2');
    if(!kw || !l1) return;
    const k=kw.value.trim().replace(/"/g,''), a=l1.value.trim(), b=l2?l2.value.trim():'';
    if(k && a) lines.push('  - {keyword: "'+k+'", l1: "'+a+'", l2: "'+b+'"}');
  });
  const out = document.getElementById('rules-out');
  out.value = lines.length
    ? '# 貼到 categories.yaml 的 rules: 之下，再跑 mailquill rebuild\\n' + lines.join('\\n')
    : '（尚未填寫任何分類）';
  out.style.display = 'block'; out.focus(); out.select();
});
// 分頁切換
document.querySelector('.tabbar').addEventListener('click', e=>{
  const b=e.target.closest('[data-tab]'); if(!b) return;
  const t=b.dataset.tab;
  document.querySelectorAll('.tabbar button').forEach(x=>x.classList.toggle('on', x===b));
  document.querySelectorAll('.tabpane').forEach(p=>p.classList.toggle('active', p.dataset.pane===t));
  document.querySelector('.filters').style.display = (t==='uncat') ? 'none' : '';  // 未分類整理忽略篩選
});
// 深色主題（記住選擇；首次依系統偏好）
function applyTheme(t){
  document.documentElement.dataset.theme = t;
  const b=document.getElementById('theme-btn');
  if(b) b.textContent = t==='dark' ? '☀️ 淺色' : '🌙 深色';
  try { localStorage.setItem('mailquill-theme', t); } catch(e){}
}
let _t; try { _t = localStorage.getItem('mailquill-theme'); } catch(e){}
if(!_t) _t = (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
applyTheme(_t);
document.getElementById('theme-btn').addEventListener('click', ()=>{
  applyTheme(document.documentElement.dataset.theme==='dark' ? 'light' : 'dark');
});
fillMonths(); renderChips(); renderCurrencyChips(); render(); renderUncat();
"""


def render_html(data: ReportData) -> str:
    head = (
        '<!doctype html>\n<html lang="zh-Hant">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Mailquill · 財務報表</title>\n<style>" + _STYLE + "</style>\n</head>\n<body>\n"
    )
    script = "<script>\nconst DATA = " + _embed(data.rows) + ";\n" + _JS + "\n</script>\n"
    return head + _BODY + script + "</body>\n</html>"


def generate_report(db_path: str, out_path: str) -> str:
    html_str = render_html(aggregate(db_path))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)
    return out_path
