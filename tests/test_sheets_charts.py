"""Overview 圖表區（每月總花費、本月各分類花費、本月前 N 大商家）與 Budget
分頁「預算 vs 實際」橫條圖的純函式測試：資料表內容、整頁版面位置與
addChart request 的來源範圍。合成資料，不打網路。"""
from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions
from mailquill.sheets_sync import (
    BUDGET_TITLE, CHARTS_TOP_MERCHANTS, GOAL_HEADER, GOAL_START_COL, OVERVIEW_CHART_DATA_COL,
    OVERVIEW_TITLE, TOTAL_LABEL, _OVERVIEW_CHART_ROWS,
    _charts_page_tables, _chart_data_rows, _overview_layout, _with_emoji,
    build_budget_chart_requests, build_overview_chart_requests, load_state, sync_sheets,
)
from tests.test_sheets_sync import FakeService, _bill as _make_bill


def _txn(merchant, date="2026-06-01", amount="100.00", l1="Uncategorized",
         currency="TWD", bank="Cathay", last4="1234"):
    return Transaction(
        txn_id=make_txn_id(bank, last4, date, amount, merchant), date=date, post_date="",
        amount=amount, currency=currency, merchant_raw=merchant, merchant_norm=merchant,
        category_l1=l1, category_l2="", bank=bank, account_last4=last4,
        source_type="pdf", source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


_TXNS = [
    _txn("全聯", date="2026-05-03", amount="300", l1="Food", bank="Cathay", last4="1111"),
    _txn("Uber", date="2026-05-10", amount="200", l1="Transport", bank="Fubon", last4="2222"),
    _txn("全聯", date="2026-06-02", amount="500", l1="Food", bank="Cathay", last4="1111"),
    _txn("家樂福", date="2026-06-05", amount="250", l1="Food", bank="Cathay", last4="1111"),
    _txn("退款", date="2026-06-06", amount="-50", l1="Food", bank="Cathay", last4="1111"),
    _txn("Netflix", date="2026-06-07", amount="390", l1="Entertainment", bank="Fubon", last4="2222"),
    _txn("Amazon", date="2026-06-08", amount="30", l1="Shopping", currency="USD", bank="Amex", last4="9"),
]


def test_charts_page_tables_trend_is_monthly_total_oldest_first():
    tables = _charts_page_tables(_TXNS)
    assert tables.currency == "TWD"
    assert tables.month == "2026-06"
    # USD 的 Amazon 不計入；6 月退款 -50 沖銷
    assert tables.trend_rows == [
        ["月份", TOTAL_LABEL],
        ["2026-05", 500.0],
        ["2026-06", 1090.0],
    ]


def test_charts_page_tables_categories_are_latest_month_sorted_desc():
    tables = _charts_page_tables(_TXNS)
    # 只看 6 月：Food 700 > Entertainment 390；5 月的 Transport 不列
    assert tables.category_rows == [
        ["分類", "金額"],
        [_with_emoji("Food"), 700.0],
        [_with_emoji("Entertainment"), 390.0],
    ]


def test_charts_page_tables_top_merchants_are_latest_month_positive_only_sorted():
    tables = _charts_page_tables(_TXNS)
    assert tables.merchant_rows == [
        ["商家", "金額"],
        ["全聯", 500.0],
        ["Netflix", 390.0],
        ["家樂福", 250.0],
    ]  # 退款（負值）不列；5 月的不列


def test_charts_page_tables_top_merchants_capped():
    txns = [_txn(f"店{i:02d}", date="2026-06-01", amount=str(100 + i)) for i in range(15)]
    tables = _charts_page_tables(txns)
    assert len(tables.merchant_rows) - 1 == CHARTS_TOP_MERCHANTS
    assert tables.merchant_rows[1][0] == "店14"


def test_charts_page_tables_empty():
    tables = _charts_page_tables([])
    assert tables.month is None
    assert tables.trend_rows == [["月份", TOTAL_LABEL]]
    assert tables.category_rows == [["分類", "金額"]]
    assert tables.merchant_rows == [["商家", "金額"]]


def test_chart_data_rows_stacks_tables_with_blank_row():
    rows = _chart_data_rows(_charts_page_tables(_TXNS))
    assert rows[0] == ["月份", TOTAL_LABEL]
    # 趨勢表 3 列 → 空一列 → 分類表從 index 4 開始（3 列）→ 空一列 → 商家表 index 8
    assert rows[3] == []
    assert rows[4] == ["分類", "金額"]
    assert rows[8] == ["商家", "金額"]


def _bill():
    return _make_bill("HSBC", "2026-06")


def test_overview_layout_stacks_bills_then_charts_then_matrix():
    layout = _overview_layout(_TXNS, [_bill()], today="2026-06-30")
    rows = layout.rows
    assert layout.bill_start == 0 and rows[0][0] == "帳單"
    # 有交易或帳單的銀行各一列（Amex/Cathay/Fubon/HSBC）
    assert layout.num_bill_rows == 4
    assert [r[0] for r in rows[1:5]] == ["Amex", "Cathay", "Fubon", "HSBC"]
    # 帳單（表頭+銀行列）後空一列，圖表從下一列開始
    chart_row = layout.chart_row
    assert rows[chart_row - 1][:1] in ([], [""])
    # 3 張圖各佔 _OVERVIEW_CHART_ROWS 列，接著才是幣別矩陣
    first_matrix = layout.blocks[0]["start"]
    assert first_matrix == chart_row + 3 * _OVERVIEW_CHART_ROWS
    assert rows[first_matrix][0] == "月份 (TWD)"
    # 圖表來源表貼在隱藏欄，不佔 A 欄起的可見區
    assert layout.data_col == OVERVIEW_CHART_DATA_COL
    assert rows[0][OVERVIEW_CHART_DATA_COL:] == ["月份", TOTAL_LABEL]


def test_overview_layout_skips_space_for_charts_without_data():
    layout = _overview_layout([], [], today="2026-06-30")
    assert layout.bill_start is None and layout.chart_row == 0
    assert layout.blocks[0]["start"] == 0  # 沒有帳單也沒有圖表 → 矩陣直接從最上面開始


def test_build_overview_chart_requests_three_charts_reference_hidden_tables():
    layout = _overview_layout(_TXNS, [_bill()], today="2026-06-30")
    requests = build_overview_chart_requests(sheet_id=7, layout=layout)
    c0 = layout.data_col

    hide = requests[0]["updateDimensionProperties"]
    assert hide["range"] == {"sheetId": 7, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c0 + 2}
    assert hide["properties"]["hiddenByUser"] is True

    charts = [r["addChart"]["chart"] for r in requests if "addChart" in r]
    assert len(charts) == 3
    trend, cat, top = charts
    for i, chart in enumerate(charts):
        assert chart["spec"]["hiddenDimensionStrategy"] == "SHOW_ALL"  # 來源欄隱藏也要畫
        bc = chart["spec"]["basicChart"]
        assert "stackedType" not in bc
        assert bc["legendPosition"] == "NO_LEGEND"
        assert len(bc["series"]) == 1
        assert bc["series"][0]["dataLabel"]["type"] == "DATA"
        assert chart["position"]["overlayPosition"]["anchorCell"] == {
            "sheetId": 7, "rowIndex": layout.chart_row + i * _OVERVIEW_CHART_ROWS, "columnIndex": 0}

    tbc = trend["spec"]["basicChart"]
    assert tbc["chartType"] == "COLUMN"
    assert tbc["series"][0]["targetAxis"] == "LEFT_AXIS"
    assert tbc["domains"][0]["domain"]["sourceRange"]["sources"][0] == {
        "sheetId": 7, "startRowIndex": 0, "endRowIndex": 3,
        "startColumnIndex": c0, "endColumnIndex": c0 + 1}

    cbc = cat["spec"]["basicChart"]
    assert cbc["chartType"] == "BAR"
    assert cbc["series"][0]["targetAxis"] == "BOTTOM_AXIS"   # API：橫條圖 series 只能指到下方軸
    cdom = cbc["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert (cdom["startRowIndex"], cdom["endRowIndex"]) == (4, 7)
    assert "2026-06" in cat["spec"]["title"]

    mdom = top["spec"]["basicChart"]["domains"][0]["domain"]["sourceRange"]["sources"][0]
    assert (mdom["startRowIndex"], mdom["endRowIndex"]) == (8, 12)
    assert "2026-06" in top["spec"]["title"]


def test_build_overview_chart_requests_skips_charts_with_no_data():
    layout = _overview_layout([], [], today="2026-06-30")
    assert not [r for r in build_overview_chart_requests(sheet_id=7, layout=layout) if "addChart" in r]


def test_build_budget_chart_requests_compares_budget_and_actual_only():
    requests = build_budget_chart_requests(sheet_id=5, num_categories=4)
    charts = [r["addChart"]["chart"] for r in requests if "addChart" in r]
    assert len(charts) == 1
    bc = charts[0]["spec"]["basicChart"]
    assert bc["chartType"] == "BAR" and "stackedType" not in bc
    dom = bc["domains"][0]["domain"]["sourceRange"]["sources"][0]
    # 表頭在第 2 列（index 1），分類列從第 3 列起共 4 列
    assert dom == {"sheetId": 5, "startRowIndex": 1, "endRowIndex": 6,
                   "startColumnIndex": 0, "endColumnIndex": 1}
    # 只比兩個 series：月預算(B)、實際(D)；近3月平均(C) 不畫，避免三根柱子看不懂
    assert [s["series"]["sourceRange"]["sources"][0]["startColumnIndex"] for s in bc["series"]] \
        == [1, 3]
    assert all("colorStyle" in s for s in bc["series"])
    anchor = charts[0]["position"]["overlayPosition"]["anchorCell"]
    assert anchor["columnIndex"] == GOAL_START_COL + len(GOAL_HEADER) + 1
    assert anchor["rowIndex"] == 1


def test_build_budget_chart_requests_empty_when_no_categories():
    assert build_budget_chart_requests(sheet_id=5, num_categories=0) == []


def test_sync_sheets_puts_charts_on_overview_without_charts_tab(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, _TXNS)
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    assert "Charts" not in load_state(state_path)["sheet_ids"]
    written = next(body["values"] for _, rng, _, body in service.updated
                   if rng == f"{OVERVIEW_TITLE}!A1")
    assert written[0][OVERVIEW_CHART_DATA_COL:] == ["月份", TOTAL_LABEL]
    all_reqs = [r for body in service.batch_updates for r in body["requests"]]
    charts = [r for r in all_reqs if "addChart" in r]
    titles = [c["addChart"]["chart"]["spec"]["title"] for c in charts]
    # Overview 3 張 + Budget 1 張
    assert len(charts) == 4
    assert any("預算" in t for t in titles)
