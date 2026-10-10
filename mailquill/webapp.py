"""本地可編輯 dashboard：讀/寫 CSV + SQLite 的小型 HTTP 伺服器。

跟 `mailquill/report.py`（靜態、唯讀、離線可開）不同，這裡的網頁會呼叫
`/api/*` 真的改到 `transactions.csv` / `categories.yaml`。分類編輯不是
幫單一筆交易打 override 標記，而是幫該商家在 categories.yaml 新增一條
關鍵字規則、再重跑既有的 `store.rebuild()`（讀 CSV → 套規則 → 寫回 CSV →
重建 SQLite）——跟 `report.py`「未分類整理」頁面「產生規則→貼上→手動
rebuild」是同一件事，只是伺服器直接做。

只用標準庫 `http.server`（不新增相依套件），
只 bind 127.0.0.1：這支伺服器有寫入 API、沒有任何驗證，不能對外開放。
"""
from __future__ import annotations

import http.server
import json
import math
import threading
import urllib.parse
from datetime import datetime

from mailquill.categorizer import ensure_rule
from mailquill.config import Config
from mailquill.fees import merge_foreign_fees
from mailquill.report import _STYLE, _to_float
from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, read_transactions, rebuild, rebuild_sqlite


def api_get_data(csv_path: str) -> list[dict]:
    """回傳前端要用的交易列（含 txn_id/merchant_raw，供編輯分類用）。"""
    rows = [
        {
            "txn_id": t.txn_id, "date": t.date, "month": (t.date or "")[:7],
            "merchant": t.merchant_norm, "merchant_raw": t.merchant_raw,
            "amount": _to_float(t.amount), "l1": t.category_l1, "l2": t.category_l2,
            "bank": t.bank, "last4": t.account_last4,
            "currency": t.currency or "TWD", "source_type": t.source_type,
            "fee": m.fee,
        }
        # 國外手續費併進原消費一起顯示（見 fees.merge_foreign_fees）
        for m in merge_foreign_fees(read_transactions(csv_path))
        for t in [m.txn]
    ]
    rows.sort(key=lambda r: (r["date"] or "", r["txn_id"]), reverse=True)
    return rows


def api_categorize(csv_path: str, db_path: str, categories_path: str,
                    keyword: str, l1: str, l2: str) -> dict:
    """幫 keyword 這個商家關鍵字新增/更新分類規則，並重跑 rebuild。"""
    keyword = (keyword or "").strip()
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip()
    if not keyword or not l1:
        raise ValueError("關鍵字與第一層分類為必填")
    rule_added = ensure_rule(categories_path, keyword, l1, l2)
    n = rebuild(csv_path, db_path, categories_path)
    return {"ok": True, "rule_added": rule_added, "count": n}


def api_add_transaction(csv_path: str, db_path: str, categories_path: str,
                         payload: dict) -> dict:
    """新增一筆手動記帳（source_type=manual），視需要補一條分類規則。"""
    date = (payload.get("date") or "").strip()
    merchant = (payload.get("merchant") or "").strip()
    amount = (payload.get("amount") or "").strip()
    currency = (payload.get("currency") or "TWD").strip() or "TWD"
    l1 = (payload.get("l1") or "").strip()
    l2 = (payload.get("l2") or "").strip()
    bank = (payload.get("bank") or "manual").strip() or "manual"
    account_last4 = (payload.get("account_last4") or "").strip()
    if not date or not merchant or not amount or not l1:
        raise ValueError("日期／商家／金額／第一層分類為必填")
    try:
        amount_value = float(amount)
    except ValueError:
        raise ValueError(f"金額不是數字：{amount}")
    if not math.isfinite(amount_value):
        raise ValueError(f"金額不是有限數字：{amount}")

    existing_ids = {t.txn_id for t in read_transactions(csv_path)}
    seq = 0
    txn_id = make_txn_id(bank, account_last4, date, amount, merchant, seq=seq)
    while txn_id in existing_ids:
        seq += 1
        txn_id = make_txn_id(bank, account_last4, date, amount, merchant, seq=seq)

    txn = Transaction(
        txn_id=txn_id, date=date, post_date=date, amount=amount, currency=currency,
        merchant_raw=merchant, merchant_norm=merchant, category_l1=l1, category_l2=l2,
        bank=bank, account_last4=account_last4, source_type="manual", source_msg_id="",
        raw_ref="", imported_at=datetime.now().isoformat(timespec="seconds"),
    )
    append_transactions(csv_path, [txn])

    rule_added = ensure_rule(categories_path, merchant, l1, l2)
    if rule_added:
        rebuild(csv_path, db_path, categories_path)
    else:
        rebuild_sqlite(csv_path, db_path)
    return {"ok": True, "txn_id": txn_id, "rule_added": rule_added}


_EXTRA_STYLE = """
.editrow { display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; padding: 0.4rem 0; }
.editrow input { width: 6.5rem; }
.editrow input.kwide { width: 11rem; }
.linkbtn { border: none; background: none; color: var(--accent); cursor: pointer;
           font-size: 0.8rem; padding: 0.1rem 0.3rem; }
.linkbtn:hover { text-decoration: underline; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
             gap: 0.9rem; margin-bottom: 1rem; }
.form-grid label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.78rem;
                    color: var(--muted); }
.form-grid input, .form-grid select { padding: 0.4rem 0.55rem; }
.status-msg { font-size: 0.85rem; margin-top: 0.5rem; min-height: 1.2em; }
.status-msg.ok { color: var(--neg); }
.status-msg.err { color: var(--pos); }
.yoy { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0.6rem 0 1.2rem; }
.l1field { display: inline-flex; gap: 0.35rem; align-items: center; }
.l1field select { padding: 0.3rem 0.45rem; }
.l1field .l1new { width: 6.5rem; }
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
      <p class="sub">財務 Dashboard · 本地伺服器（可編輯）· 淨額（<span class="pos">紅＝花費</span> / <span class="neg">綠＝退款</span>）</p>
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
  <button data-tab="trends">長期趨勢</button>
  <button data-tab="add">手動記帳</button>
</div>
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
      <th></th>
    </tr></thead>
    <tbody id="detail"></tbody>
  </table></div>
</section>
<section class="tabpane" data-pane="uncat">
  <h2>未分類整理</h2>
  <p class="hint">填上第一/第二層後按「套用」：會直接幫這個商家在 categories.yaml
    新增一條規則並重新分類，畫面即時更新（可縮短「關鍵字」讓比對更精準，例如長店名
    只留品牌兩三字）。</p>
  <div class="tablewrap"><table>
    <thead><tr><th>關鍵字（預設＝商家）</th><th class="amt">金額</th><th>筆數</th>
      <th>第一層</th><th>第二層</th><th></th></tr></thead>
    <tbody id="uncat-rows"></tbody>
  </table></div>
</section>
<section class="tabpane" data-pane="trends">
  <h2>長期趨勢</h2>
  <p class="hint">不受上方月份區間篩選限制，涵蓋全部歷史（仍套用銀行／幣別／分類篩選）。</p>
  <div id="yoy-cards" class="yoy"></div>
  <h3>全歷史月度總額</h3>
  <div id="v-trend-month"></div>
  <h3 class="mtop">分類 Top 6：今年累計（YTD）vs 去年同期</h3>
  <div id="v-trend-yoy"></div>
</section>
<section class="tabpane" data-pane="add">
  <h2>手動記帳</h2>
  <p class="hint">補記帳單上沒有的紀錄（現金消費等）。送出後會歸類到「source=manual」，
    並視需要幫這個商家補一條 categories.yaml 規則。</p>
  <form id="add-form">
    <div class="form-grid">
      <label>日期<input type="date" name="date" required></label>
      <label>商家<input type="text" name="merchant" required></label>
      <label>金額<input type="number" step="0.01" name="amount" required></label>
      <label>幣別<input type="text" name="currency" value="TWD"></label>
      <label>第一層<span id="add-l1-field"></span></label>
      <label>第二層<input type="text" name="l2"></label>
      <label>銀行（選填，預設 manual）<input type="text" name="bank"></label>
      <label>帳戶末四碼（選填）<input type="text" name="account_last4"></label>
    </div>
    <button type="submit" class="btn">新增交易</button>
    <div id="add-status" class="status-msg"></div>
  </form>
</section>
</div>
"""

_JS = """
const BANKS = {
  Cathay:{s:"國泰",c:"#0a8f4e"}, UnionBank:{s:"聯邦",c:"#c8102e"},
  Taishin:{s:"台新",c:"#00a0a0"}, Fubon:{s:"富邦",c:"#1a3b8b"}, manual:{s:"手記",c:"#8a6a30"},
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

let DATA = [];
let months = [], allBanks = [], allCurrencies = [], allCats = [], l2ByL1 = new Map();
const L1ORDER = ['Food','Housing','Transport','Education','Entertainment','Insurance','Medical','Shopping','Other','Uncategorized'];
const l1rank = x => { const i=L1ORDER.indexOf(x); return i<0 ? 999 : i; };
function defaultCurrency(){
  const cnt = new Map();
  for(const r of DATA){ cnt.set(r.currency, (cnt.get(r.currency)||0)+1); }
  return ([...cnt.entries()].sort((a,b)=>b[1]-a[1])[0] || ['TWD'])[0];
}
const COLLABEL = {date:'日期',merchant:'商家',amount:'金額',l1:'第一層',l2:'第二層',bank:'銀行'};
const SEP = '\\u241f';
const leafKey = r => r.l1 + SEP + (r.l2||"");
const l1LeafKeys = l1 => [...(l2ByL1.get(l1)||new Map()).keys()].map(c=>l1+SEP+(c||""));
const state = { banks:new Set(), leaves:new Set(), l2mode:'include',
                from:"", to:"", chartMode:'bar', currency:"", editing:null };
const sortState = { col:'date', dir:'desc' };

function rebuildIndexes(){
  months = [...new Set(DATA.map(r=>r.month))].filter(Boolean).sort();
  allBanks = [...new Set(DATA.map(r=>r.bank))];
  allCurrencies = [...new Set(DATA.map(r=>r.currency))];
  allCats = [...new Set(DATA.map(r=>r.l1))]
    .sort((a,b)=> l1rank(a)-l1rank(b) || (a<b?-1:a>b?1:0));
  l2ByL1 = new Map();
  for(const r of DATA){ if(!l2ByL1.has(r.l1)) l2ByL1.set(r.l1,new Map());
    const mm=l2ByL1.get(r.l1), k=r.l2||""; mm.set(k,(mm.get(k)||0)+Math.abs(r.amount)); }
}

// 第一層分類：下拉選單（從目前資料裡實際出現過的分類產生），選「＋ 新增分類」時
// 才顯示自由輸入框——避免手誤打錯字造成同一分類出現兩種寫法。
function l1FieldHtml(selected){
  const known = allCats.filter(c=>c!=='Uncategorized');
  const isNew = !!selected && !known.includes(selected);
  const opts = ['<option value="">請選擇…</option>'].concat(
    known.map(c=>'<option value="'+esc(c)+'"'+(c===selected?' selected':'')+'>'+esc(c)+'</option>')
  );
  opts.push('<option value="__new__"'+(isNew?' selected':'')+'>＋ 新增分類…</option>');
  return '<span class="l1field" data-l1field>'+
    '<select class="l1sel" data-l1select>'+opts.join('')+'</select>'+
    '<input type="text" class="l1new" placeholder="新分類名稱"'+
    ' style="display:'+(isNew?'inline-block':'none')+'" value="'+(isNew?esc(selected):'')+'"></span>';
}
function l1FieldValue(root){
  const sel = root.querySelector('.l1sel');
  if(!sel) return '';
  if(sel.value==='__new__'){ const n=root.querySelector('.l1new'); return n ? n.value.trim() : ''; }
  return sel.value;
}
document.body.addEventListener('change', e=>{
  const sel = e.target.closest('.l1sel'); if(!sel) return;
  const input = sel.closest('[data-l1field]').querySelector('.l1new');
  if(sel.value==='__new__'){ input.style.display='inline-block'; input.focus(); }
  else { input.style.display='none'; input.value=''; }
});

function inCat(r){
  if(state.leaves.size===0) return true;
  const has = state.leaves.has(leafKey(r));
  return state.l2mode==='exclude' ? !has : has;
}
function passesCommonFilters(r){
  return (state.banks.size===0 || state.banks.has(r.bank)) &&
    r.currency === state.currency && inCat(r);
}
function filtered(){
  return DATA.filter(r=> passesCommonFilters(r) &&
    (!r.month || (r.month>=state.from && r.month<=state.to)));
}
function filteredAllTime(){
  return DATA.filter(passesCommonFilters);
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
  if(!pos.length) return bars(items, opt);
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
  const tree = document.getElementById('f-cattree');
  tree.className = 'cattree' + (state.l2mode==='exclude' ? ' exclude' : '');
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

async function postJSON(url, body){
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.error || ('HTTP '+res.status));
  return data;
}
async function loadData(){
  const res = await fetch('/api/data');
  const data = await res.json();
  DATA = data.rows;
  rebuildIndexes();
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
  document.getElementById('detail').innerHTML = shown.map(r=>{
    const editing = state.editing===r.txn_id;
    const editCell = editing ? editRowHtml(r) : '<button class="linkbtn" data-edit="'+esc(r.txn_id)+'">編輯分類</button>';
    return '<tr><td>'+esc(r.date)+'</td><td>'+esc(r.merchant)+'</td>'+
      '<td class="amt '+(r.amount<0?'neg':'pos')+'">'+fmt(r.amount)+feeNote(r)+'</td><td>'+esc(r.l1)+'</td>'+
      '<td>'+esc(r.l2)+'</td><td>'+badge(r.bank)+'</td>'+
      '<td>'+editCell+'</td></tr>';
  }).join('');
  document.getElementById('detail-note').textContent =
    rows.length + ' 筆' + (rows.length>cap ? '（僅顯示前 '+cap+'）' : '');
  paintSort();
  renderTrends();
}
function editRowHtml(r){
  return '<div class="editrow" data-editrow="'+esc(r.txn_id)+'">'+
    '<input class="kwide kw" value="'+esc(r.merchant_raw)+'" placeholder="關鍵字">'+
    l1FieldHtml(r.l1==='Uncategorized'?'':r.l1)+
    '<input class="l2" value="'+esc(r.l2)+'" placeholder="第二層">'+
    '<button type="button" class="btn sm" data-save="'+esc(r.txn_id)+'">儲存</button>'+
    '<button type="button" class="linkbtn" data-cancel="1">取消</button></div>';
}
async function submitCategorize(keyword, l1, l2, statusEl){
  try{
    await postJSON('/api/categorize', {keyword, l1, l2});
    state.editing = null;
    await loadData();
    renderChips(); renderCurrencyChips(); fillMonths(); render(); renderUncat(); renderAddForm();
  } catch(e){
    if(statusEl){ statusEl.textContent = '錯誤：'+e.message; statusEl.className='status-msg err'; }
  }
}
function paintSort(){
  document.querySelectorAll('th.sortable').forEach(th=>{
    const c=th.dataset.sort, active=c===sortState.col;
    const ind = active ? (sortState.dir==='asc'?'▲':'▼') : '⇅';
    th.innerHTML = esc(COLLABEL[c]) + ' <span class="sort-ind'+(active?' on':'')+'">'+ind+'</span>';
  });
}
function renderUncat(){
  const m = new Map();
  for(const r of DATA){
    if(r.l1 !== 'Uncategorized') continue;
    const k = (r.merchant_raw||'') + SEP + (r.currency||'');
    const e = m.get(k) || {sum:0, cnt:0, merchant:r.merchant_raw, currency:r.currency};
    e.sum += r.amount; e.cnt++; m.set(k, e);  // 淨額（非絕對值加總）：訂房被全額退款的話淨影響是 0，
                                                // 不該因為 abs 加總被誤判成大額待分類
  }
  const items = [...m.values()].sort((a,b)=>Math.abs(b.sum)-Math.abs(a.sum));
  const tb = document.getElementById('uncat-rows');
  if(!items.length){ tb.innerHTML = '<tr><td colspan="6" class="empty">沒有未分類，讚！</td></tr>'; return; }
  tb.innerHTML = items.map(e=>
    '<tr data-uncat="'+esc(e.merchant)+'">'+
    '<td><input class="tag kw" value="'+esc(e.merchant)+'" style="width:15rem"></td>'+
    '<td class="amt">'+fmt(e.sum)+' '+esc(e.currency)+'</td><td>'+e.cnt+'</td>'+
    '<td>'+l1FieldHtml('')+'</td>'+
    '<td><input class="tag l2" style="width:8rem"></td>'+
    '<td><button type="button" class="btn sm" data-apply="1">套用</button>'+
    '<span class="status-msg" data-status="1"></span></td>'+
    '</tr>').join('');
}
function renderAddForm(){
  document.getElementById('add-l1-field').innerHTML = l1FieldHtml('');
}
function fillMonths(){
  const opts = months.map(m=>'<option value="'+esc(m)+'">'+esc(m)+'</option>').join('');
  const f=document.getElementById('f-from'), t=document.getElementById('f-to');
  f.innerHTML=opts; t.innerHTML=opts; f.value=state.from; t.value=state.to;
}

// ---- 長期趨勢 ----
function ymAdd(ym, deltaYears){
  const [y,m] = ym.split('-').map(Number);
  return (y+deltaYears)+'-'+String(m).padStart(2,'0');
}
function renderTrends(){
  const rows = filteredAllTime();
  const byMonth = groupSum(rows, r=>r.month).sort((a,b)=> a[0]<b[0]?-1:a[0]>b[0]?1:0);
  document.getElementById('v-trend-month').innerHTML = bars(byMonth);

  const now = new Date();
  const thisMonth = now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0');
  const smly = ymAdd(thisMonth, -1);
  const thisYearPrefix = String(now.getFullYear());
  const lastYearPrefix = String(now.getFullYear()-1);
  const sumByMonth = k => rows.filter(r=>r.month===k).reduce((s,r)=>s+r.amount,0);
  const sumYTD = yearPrefix => rows
    .filter(r=> r.month && r.month.slice(0,4)===yearPrefix && r.month.slice(5,7) <= String(now.getMonth()+1).padStart(2,'0'))
    .reduce((s,r)=>s+r.amount,0);
  const thisMonthTotal = sumByMonth(thisMonth);
  const smlyTotal = sumByMonth(smly);
  const ytdThis = sumYTD(thisYearPrefix);
  const ytdLast = sumYTD(lastYearPrefix);
  const sign = v => '<span class="'+(v<0?'neg':'pos')+'">'+fmt(v)+'</span>';
  document.getElementById('yoy-cards').innerHTML =
    card('本月 ('+esc(thisMonth)+')', sign(thisMonthTotal)) +
    card('去年同月 ('+esc(smly)+')', sign(smlyTotal)) +
    card('今年累計 YTD', sign(ytdThis)) +
    card('去年同期 YTD', sign(ytdLast));

  const l1Totals = groupSum(rows.filter(r=>r.month && r.month.slice(0,4)===thisYearPrefix &&
    r.month.slice(5,7) <= String(now.getMonth()+1).padStart(2,'0')), r=>r.l1).slice(0,6);
  const topL1 = l1Totals.map(([l1])=>l1);
  const cutoff = String(now.getMonth()+1).padStart(2,'0');
  const html = topL1.map(l1=>{
    const thisY = rows.filter(r=>r.l1===l1 && r.month && r.month.slice(0,4)===thisYearPrefix && r.month.slice(5,7)<=cutoff)
      .reduce((s,r)=>s+r.amount,0);
    const lastY = rows.filter(r=>r.l1===l1 && r.month && r.month.slice(0,4)===lastYearPrefix && r.month.slice(5,7)<=cutoff)
      .reduce((s,r)=>s+r.amount,0);
    return bars([[l1+' · 今年YTD', thisY],[l1+' · 去年同期', lastY]]);
  }).join('<hr class="hairline">');
  document.getElementById('v-trend-yoy').innerHTML = html || '<div class="empty">（無資料）</div>';
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
         const allOn=keys.length && keys.every(k=>state.leaves.has(k));
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
  renderChips(); render();
}));
document.querySelector('th.sortable').closest('thead').addEventListener('click', e=>{
  const th=e.target.closest('th[data-sort]'); if(!th) return;
  const c=th.dataset.sort;
  if(sortState.col===c){ sortState.dir = sortState.dir==='asc'?'desc':'asc'; }
  else { sortState.col=c; sortState.dir = c==='amount'?'desc':'asc'; }
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
document.getElementById('detail').addEventListener('click', e=>{
  const edit=e.target.closest('[data-edit]');
  if(edit){ state.editing = edit.dataset.edit; render(); return; }
  const cancel=e.target.closest('[data-cancel]');
  if(cancel){ state.editing = null; render(); return; }
  const save=e.target.closest('[data-save]');
  if(save){
    const row = e.target.closest('[data-editrow]');
    const kw = row.querySelector('.kw').value.trim();
    const l1 = l1FieldValue(row);
    const l2 = row.querySelector('.l2').value.trim();
    if(!kw || !l1){ return; }
    submitCategorize(kw, l1, l2, null);
  }
});
document.getElementById('uncat-rows').addEventListener('click', e=>{
  const apply=e.target.closest('[data-apply]'); if(!apply) return;
  const tr=e.target.closest('tr');
  const kw=tr.querySelector('.kw').value.trim();
  const l1=l1FieldValue(tr);
  const l2=tr.querySelector('.l2').value.trim();
  const statusEl=tr.querySelector('[data-status]');
  if(!kw || !l1){ statusEl.textContent='請填第一層'; statusEl.className='status-msg err'; return; }
  submitCategorize(kw, l1, l2, statusEl);
});
document.getElementById('add-form').addEventListener('submit', async e=>{
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  const payload = Object.fromEntries(fd.entries());
  payload.l1 = l1FieldValue(form);
  const statusEl = document.getElementById('add-status');
  if(!payload.l1){ statusEl.textContent='請選第一層分類'; statusEl.className='status-msg err'; return; }
  try{
    const res = await postJSON('/api/transactions', payload);
    statusEl.textContent = '已新增（txn_id='+res.txn_id+'）';
    statusEl.className = 'status-msg ok';
    form.reset();
    form.querySelector('[name=currency]').value = 'TWD';
    await loadData();
    renderChips(); renderCurrencyChips(); fillMonths(); render(); renderUncat(); renderAddForm();
  } catch(err){
    statusEl.textContent = '錯誤：'+err.message;
    statusEl.className = 'status-msg err';
  }
});
document.querySelector('.tabbar').addEventListener('click', e=>{
  const b=e.target.closest('[data-tab]'); if(!b) return;
  const t=b.dataset.tab;
  document.querySelectorAll('.tabbar button').forEach(x=>x.classList.toggle('on', x===b));
  document.querySelectorAll('.tabpane').forEach(p=>p.classList.toggle('active', p.dataset.pane===t));
  document.querySelector('.filters').style.display = (t==='uncat'||t==='add') ? 'none' : '';
});
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

(async function boot(){
  await loadData();
  state.currency = defaultCurrency();
  state.from = months[0]||""; state.to = months[months.length-1]||"";
  fillMonths(); renderChips(); renderCurrencyChips(); render(); renderUncat(); renderAddForm();
})();
"""


def render_page() -> str:
    head = (
        '<!doctype html>\n<html lang="zh-Hant">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Mailquill · 本地 Dashboard</title>\n"
        "<style>" + _STYLE + _EXTRA_STYLE + "</style>\n</head>\n<body>\n"
    )
    script = "<script>\n" + _JS + "\n</script>\n"
    return head + _BODY + script + "</body>\n</html>"


_MAX_BODY_BYTES = 1_000_000


def _make_handler(cfg: Config):
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "MailquillServe/0.1"

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                raise ValueError("Content-Length 不是整數")
            if length < 0 or length > _MAX_BODY_BYTES:
                raise ValueError("請求內容大小不合法")
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                raise ValueError("無效的 JSON")
            if not isinstance(data, dict):
                raise ValueError("請求內容必須是 JSON 物件")
            return data

        def _allowed_hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _is_trusted_request(self) -> bool:
            """這支伺服器沒有帳號驗證，只靠「只有本機自己的網頁能呼叫」保護。

            - Host 必須是 127.0.0.1／localhost 加上本伺服器的埠：擋 DNS rebinding
              （惡意網域短暫解析到 127.0.0.1，讓瀏覽器把它當同源）。
            - 有帶 Origin 時必須是本機自己：擋別的網頁用瀏覽器對這裡發的跨站請求。
            """
            if self.headers.get("Host", "") not in self._allowed_hosts():
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in self._allowed_hosts()}

        def do_GET(self) -> None:  # noqa: N802 (http.server 介面命名)
            if not self._is_trusted_request():
                self._send_json(403, {"error": "forbidden"})
                return
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self._send_html(render_page())
                return
            if path == "/api/data":
                self._send_json(200, {"rows": api_get_data(cfg.csv_path)})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._is_trusted_request():
                self._send_json(403, {"error": "forbidden"})
                return
            # 要求 application/json：別的網頁用 text/plain 之類的「簡單請求」
            # 不會觸發 CORS preflight，會直接送達，所以不是 JSON 的一律拒絕。
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._send_json(415, {"error": "Content-Type 必須是 application/json"})
                return
            path = urllib.parse.urlparse(self.path).path
            try:
                payload = self._read_json_body()
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return
            try:
                if path == "/api/categorize":
                    with lock:
                        result = api_categorize(
                            cfg.csv_path, cfg.db_path, cfg.categories_path,
                            payload.get("keyword", ""), payload.get("l1", ""),
                            payload.get("l2", ""),
                        )
                    self._send_json(200, result)
                    return
                if path == "/api/transactions":
                    with lock:
                        result = api_add_transaction(
                            cfg.csv_path, cfg.db_path, cfg.categories_path, payload
                        )
                    self._send_json(200, result)
                    return
                self._send_json(404, {"error": "not found"})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except (TypeError, AttributeError):
                self._send_json(400, {"error": "欄位型別不正確"})

        def log_message(self, fmt: str, *args) -> None:
            pass

    return Handler


def run_server(cfg: Config, port: int = 8420) -> None:
    handler = _make_handler(cfg)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
