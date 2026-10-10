from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, rebuild_sqlite
from mailquill.report import aggregate, ReportData, render_html, generate_report


def _txn(date, merchant, amount, l1, l2="", bank="B", currency="TWD"):
    tid = make_txn_id(bank, "1234", date, amount, merchant)
    return Transaction(
        txn_id=tid, date=date, post_date="", amount=amount, currency=currency,
        merchant_raw=merchant, merchant_norm=merchant, category_l1=l1, category_l2=l2,
        bank=bank, account_last4="1234", source_type="pdf", source_msg_id="m1",
        raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def _db(tmp_path, txns):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    append_transactions(csv_path, txns)
    rebuild_sqlite(csv_path, db_path)
    return db_path


def test_aggregate_totals_and_breakdowns(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-05-10", "全聯", "300", "Food", "Groceries"),
        _txn("2026-06-01", "全聯", "1200", "Food", "Groceries"),
        _txn("2026-06-02", "台電", "800", "Housing", "Utilities"),
    ])
    data = aggregate(db)
    assert isinstance(data, ReportData)
    assert data.count == 3
    assert abs(data.total - 2300.0) < 1e-9
    # 第一層：食 1500 > 住 800
    assert data.by_l1[0] == ("Food", 1500.0)
    assert data.by_l1[1] == ("Housing", 800.0)
    # 月度：2026-05 在前、2026-06 在後
    assert data.by_month[0] == ("2026-05", 300.0)
    assert data.by_month[1] == ("2026-06", 2000.0)
    # 明細：日期新到舊，第一筆是 06-02
    assert data.rows[0]["date"] == "2026-06-02"
    assert data.rows[0]["merchant"] == "台電"


def test_aggregate_empty_amount_counts_as_zero_but_row_present(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-06-01", "正常", "100", "Food"),
        _txn("2026-06-02", "壞資料", "", "Uncategorized"),
    ])
    data = aggregate(db)
    assert data.count == 2
    assert abs(data.total - 100.0) < 1e-9
    assert any(r["merchant"] == "壞資料" for r in data.rows)


def test_render_html_contains_key_sections(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-06-01", "全聯", "1200", "Food", "Groceries"),
        _txn("2026-06-02", "台電", "800", "Housing", "Utilities"),
    ])
    html_str = render_html(aggregate(db))
    assert "<html" in html_str.lower()
    assert "mailquill" in html_str.lower()
    # 分類與金額有出現
    assert "Food" in html_str
    assert "Housing" in html_str
    assert "全聯" in html_str
    assert "2026-06" in html_str
    # 自包含：不引用外部資源
    assert "http://" not in html_str
    assert "https://" not in html_str


def test_render_html_escapes_merchant(tmp_path):
    # 資料內嵌於 <script>，商家字串中的 < > & 須轉義成 \\uXXXX，避免破壞腳本/XSS
    db = _db(tmp_path, [_txn("2026-06-01", "<script>x</script>", "100", "Food")])
    html_str = render_html(aggregate(db))
    assert "<script>x</script>" not in html_str
    assert "\\u003cscript" in html_str


def test_render_html_has_interactive_controls(tmp_path):
    db = _db(tmp_path, [_txn("2026-06-01", "全聯", "1200", "Food", "Groceries")])
    html_str = render_html(aggregate(db))
    # 互動 dashboard：內嵌資料 + 篩選控制 + 各樞紐視角的容器
    assert "const DATA = " in html_str
    assert 'id="f-banks"' in html_str
    assert 'id="f-cattree"' in html_str                 # 分類/品類 左右階層 filter
    assert "catcol" in html_str
    assert 'data-l2mode="exclude"' in html_str          # 品類包含/排除切換
    assert "L1ORDER" in html_str                         # 第一層固定順序
    assert 'id="f-from"' in html_str and 'id="f-to"' in html_str
    for vid in ("v-month", "v-bank", "v-l1", "v-l2", "v-merchant", "detail"):
        assert f'id="{vid}"' in html_str
    assert 'data-mode="pie"' in html_str                # 長條/圓餅切換
    assert "function donut" in html_str                 # 圓餅圖渲染
    assert "l2ByL1" in html_str                         # 品類依第一層分列
    assert 'id="uncat-rows"' in html_str                # 未分類整理面板
    assert 'id="gen-rules"' in html_str                 # 產生 categories.yaml 規則
    assert "renderUncat" in html_str
    assert 'class="container"' in html_str              # 置中容器 (margin-x 50)
    assert "margin: 1.5rem 50px" in html_str
    assert 'class="tabbar"' in html_str                 # 分頁
    assert 'data-tab="uncat"' in html_str
    assert 'id="theme-btn"' in html_str                 # 深色主題切換
    assert '[data-theme="dark"]' in html_str
    assert "--pos:" in html_str and "--neg:" in html_str  # 花費紅/退款綠


def test_aggregate_rows_include_currency(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-06-01", "全聯", "1200", "Food", currency="TWD"),
        _txn("2026-06-02", "Amazon", "50", "Uncategorized", currency="USD"),
    ])
    data = aggregate(db)
    currencies = {r["merchant"]: r["currency"] for r in data.rows}
    assert currencies["全聯"] == "TWD"
    assert currencies["Amazon"] == "USD"


def test_render_html_has_currency_filter_and_defaults_to_majority(tmp_path):
    # 兩筆 TWD、一筆 USD：預設幣別應該是筆數較多的 TWD，避免混幣別加總。
    db = _db(tmp_path, [
        _txn("2026-06-01", "全聯", "1200", "Food", currency="TWD"),
        _txn("2026-06-02", "台電", "800", "Housing", currency="TWD"),
        _txn("2026-06-03", "Amazon", "50", "Uncategorized", currency="USD"),
    ])
    html_str = render_html(aggregate(db))
    assert 'id="f-currency"' in html_str
    assert "allCurrencies" in html_str
    assert "state.currency" in html_str
    assert "defaultCurrency" in html_str
    assert '"currency": "TWD"' in html_str
    assert '"currency": "USD"' in html_str


def test_generate_report_writes_file(tmp_path):
    db = _db(tmp_path, [_txn("2026-06-01", "全聯", "1200", "Food")])
    out = str(tmp_path / "report.html")
    returned = generate_report(db, out)
    assert returned == out
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "全聯" in content
    assert "<html" in content.lower()


def test_render_html_embeds_numeric_amounts(tmp_path):
    # 金額以數值內嵌，前端 JS 格式化（千分位、不顯示 .0）
    db = _db(tmp_path, [
        _txn("2026-06-01", "全聯", "1200", "Food"),
        _txn("2026-06-02", "咖啡", "55.50", "Food"),
    ])
    html_str = render_html(aggregate(db))
    assert '"amount": 1200.0' in html_str
    assert '"amount": 55.5' in html_str


def test_aggregate_rows_merge_foreign_fee_into_parent(tmp_path):
    db = _db(tmp_path, [
        _txn("2026-07-20", "MOBILE SUICA APPLE V", "3998", "Transport", "Suica"),
        _txn("2026-07-20", "國外交易手續費 -MOBILE", "60", "Other", "Fee"),
    ])
    rows = aggregate(db).rows
    assert len(rows) == 1
    assert (rows[0]["amount"], rows[0]["fee"], rows[0]["l1"]) == (4058.0, 60.0, "Transport")


def test_render_html_shows_fee_note_and_card(tmp_path):
    html = render_html(aggregate(_db(tmp_path, [
        _txn("2026-07-20", "MOBILE SUICA APPLE V", "3998", "Transport"),
        _txn("2026-07-20", "國外交易手續費 -MOBILE", "60", "Other", "Fee"),
    ])))
    assert '"fee": 60.0' in html
    assert "+ 手續費" in html
    assert "國外手續費" in html


def _default_currency_js(source: str) -> str:
    import re
    m = re.search(r"function defaultCurrency\(\)\{.*?\n\}", source, re.S)
    assert m, "defaultCurrency not found"
    return m.group(0)


def test_default_currency_survives_empty_data_in_report_and_webapp(tmp_path):
    # 空資料庫時 DATA 是空陣列：defaultCurrency() 不能丟 TypeError，不然整頁 script 中止
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    from mailquill import report, webapp
    for source in (report._JS, webapp._JS):
        js = "const DATA = [];\n" + _default_currency_js(source) + "\nconsole.log(defaultCurrency());"
        out = subprocess.run([node, "-e", js], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "TWD"
