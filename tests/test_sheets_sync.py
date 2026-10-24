import json

from mailquill.bill_due import BillDue
from mailquill.bills_store import append_bills
from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, read_transactions
from mailquill.sheets_sync import (
    ARCHIVE_CUTOFF, ARCHIVE_TITLE, BUDGET_CATEGORIES,
    BUDGET_HEADER,
    BUDGET_REF_HEADER, BUDGET_TITLE,
    CHARTS_CATEGORY_HEADER, CHARTS_TREND_HEADER, DETAIL_TITLE, GOAL_CATEGORIES,
    GOAL_HEADER, GOAL_START_COL, HEADER,
    OVERVIEW_TITLE, SHEET_TITLE, TOTAL_LABEL, account_label,
    archive_rows_from_csv, budget_info_row, budget_scaffold_rows,
    build_budget_format_requests,
    build_budget_reference_and_actual_rows,
    build_combined_overview_format_requests, build_format_requests,
    build_goal_format_requests, build_month_detail_format_requests,
    category_totals, combined_overview_rows_and_blocks, current_month_actuals,
    build_goal_rows,
    latest_recent_month, list_accounts, list_currencies, load_state, monthly_totals,
    overview_rows_from_csv, primary_currency,
    read_budget, read_budget_categories, rows_from_csv, save_state,
    sync_sheets, write_budget_reference_and_actual_columns,
    write_goal_rows, write_month_detail_query, write_month_detail_selectors,
    month_detail_formula, build_tab_order_requests, DETAIL_HEADER, ALL_OPTION,
    _budget_actual_formula, _budget_reference_formula,
    _col_letter, _column_pixel_widths,
    _detail_default_month, _detail_months, _foreign_currency_blocks, _goal_actual_formula,
    _goal_actual_month_sum,
    _HEADER_BG, _HEADER_FG, _text_pixel_width, _with_emoji,
)


def _txn(merchant, date="2026-06-01", amount="100.00", l1="Uncategorized", l2="",
        currency="TWD", bank="Cathay", last4="1234"):
    tid = make_txn_id(bank, last4, date, amount, merchant)
    return Transaction(
        txn_id=tid, date=date, post_date="", amount=amount,
        currency=currency, merchant_raw=merchant, merchant_norm=merchant,
        category_l1=l1, category_l2=l2, bank=bank,
        account_last4=last4, source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


class FakeService:
    """收集呼叫、模擬 Sheets API client 的分頁狀態：不打真的網路。"""

    def __init__(self, existing_sheets=None, conditional_formats=None, budget_values=None,
                banded_ranges=None, merged_sheets=None):
        self.existing_sheets = dict(existing_sheets or {})  # title -> sheetId
        self.conditional_formats = dict(conditional_formats or {})  # sheetId -> count
        self.banded_ranges = dict(banded_ranges or {})  # sheetId -> [bandedRangeId, ...]
        self.merged_sheets = set(merged_sheets or ())  # sheetId 集合：回報「這個分頁有合併儲存格」
        self.budget_values = list(budget_values) if budget_values is not None else None
        self._next_sheet_id = 1000
        self.created = []
        self.cleared = []
        self.updated = []
        self.tab_lookup_calls = 0
        self.conditional_lookup_calls = 0
        self.budget_read_calls = 0
        self.batch_updates = []
        self._pending = None
        self._pending_body = None
        self._pending_fields = None
        self._pending_range = None

    def spreadsheets(self):
        return self

    def create(self, body, fields):
        self.created.append(body)
        self._pending = "create"
        return self

    def get(self, spreadsheetId, fields=None, range=None):
        self._pending = "get"
        self._pending_fields = fields
        self._pending_range = range
        if range is not None:
            self.budget_read_calls += 1
        elif "conditionalFormats" in fields:
            self.conditional_lookup_calls += 1
        else:
            self.tab_lookup_calls += 1
        return self

    def values(self):
        return self

    def clear(self, spreadsheetId, range):
        self.cleared.append((spreadsheetId, range))
        self._pending = "clear"
        return self

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.updated.append((spreadsheetId, range, valueInputOption, body))
        self._pending = "update"
        return self

    def batchUpdate(self, spreadsheetId, body):
        self.batch_updates.append(body)
        self._pending = "batchUpdate"
        self._pending_body = body
        return self

    def _alloc_sheet_id(self):
        self._next_sheet_id += 1
        return self._next_sheet_id

    def execute(self):
        pending, self._pending = self._pending, None
        if pending == "create":
            sheet_id = self._alloc_sheet_id()
            self.existing_sheets[SHEET_TITLE] = sheet_id
            return {"spreadsheetId": "sheet-123",
                    "spreadsheetUrl": "https://sheets.google/sheet-123",
                    "sheets": [{"properties": {"sheetId": sheet_id, "title": SHEET_TITLE}}]}
        if pending == "get":
            if self._pending_range is not None:
                return {"values": self.budget_values} if self.budget_values is not None else {}
            if "conditionalFormats" in self._pending_fields:
                return {"sheets": [
                    {"properties": {"sheetId": sid},
                     "conditionalFormats": [{}] * self.conditional_formats.get(sid, 0),
                     "bandedRanges": [{"bandedRangeId": bid}
                                      for bid in self.banded_ranges.get(sid, [])],
                     "merges": [{}] if sid in self.merged_sheets else []}
                    for sid in self.existing_sheets.values()
                ]}
            return {"sheets": [{"properties": {"sheetId": sid, "title": title}}
                               for title, sid in self.existing_sheets.items()]}
        if pending == "batchUpdate":
            replies = []
            for req in self._pending_body["requests"]:
                if "addSheet" in req:
                    title = req["addSheet"]["properties"]["title"]
                    sheet_id = self._alloc_sheet_id()
                    self.existing_sheets[title] = sheet_id
                    replies.append({"addSheet": {"properties": {"sheetId": sheet_id, "title": title}}})
                elif "deleteSheet" in req:
                    sid = req["deleteSheet"]["sheetId"]
                    for t, existing_id in list(self.existing_sheets.items()):
                        if existing_id == sid:
                            del self.existing_sheets[t]
                    replies.append({})
                elif "deleteConditionalFormatRule" in req:
                    replies.append({})
                elif "deleteBanding" in req:
                    replies.append({})
                elif "unmergeCells" in req:
                    replies.append({})
            return {"replies": replies}
        return {}


def test_col_letter_single_and_double_letter_columns():
    assert _col_letter(0) == "A"
    assert _col_letter(4) == "E"
    assert _col_letter(25) == "Z"
    assert _col_letter(26) == "AA"
    assert _col_letter(27) == "AB"


def test_account_label_joins_bank_and_last4():
    assert account_label("Cathay", "1234") == "Cathay-1234"


def test_account_label_sanitizes_forbidden_sheet_title_chars():
    assert account_label("Fu/bon:X", "5678") == "Fu_bon_X-5678"


def test_account_label_uses_alias_when_default_name_matches():
    aliases = {"Fubon-6040": "富邦帳戶"}
    assert account_label("Fubon", "6040", aliases) == "富邦帳戶"


def test_account_label_falls_back_to_default_when_no_alias_matches():
    aliases = {"Fubon-6040": "富邦帳戶"}
    assert account_label("Fubon", "7254", aliases) == "Fubon-7254"


def test_account_label_sanitizes_forbidden_chars_in_alias_too():
    aliases = {"Fubon-6040": "富邦/帳戶"}
    assert account_label("Fubon", "6040", aliases) == "富邦_帳戶"


def test_list_accounts_returns_sorted_unique_labels(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("全聯", bank="Fubon", last4="2222"),
        _txn("家樂福", bank="Cathay", last4="1111"),
        _txn("711", bank="Cathay", last4="1111"),
    ])
    assert list_accounts(csv_path) == ["Cathay-1111", "Fubon-2222"]


def test_list_accounts_applies_aliases(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("全聯", bank="Fubon", last4="6040"),
        _txn("家樂福", bank="Fubon", last4="7254"),
    ])
    aliases = {"Fubon-6040": "富邦帳戶", "Fubon-7254": "富邦JCB卡"}
    assert list_accounts(csv_path, aliases) == ["富邦JCB卡", "富邦帳戶"]


def test_list_currencies_returns_sorted_unique_currencies(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", bank="Cathay", last4="1111", currency="TWD"),
        _txn("B", bank="Amex", last4="9999", currency="USD"),
        _txn("C", bank="Cathay", last4="1111", currency="TWD"),
    ])
    assert list_currencies(csv_path) == ["TWD", "USD"]


def test_rows_from_csv_includes_header_and_maps_fields(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("全聯", amount="88.00", l1="Food", l2="Supermarket")])
    rows = rows_from_csv(csv_path, {"Cathay-1234": "國泰cube卡"})
    assert rows[0] == HEADER
    assert rows[1] == ["2026-06-01", "全聯", 88.0, "TWD", "Food", "Supermarket", "Cathay", "1234",
                       "國泰cube卡"]

def test_rows_from_csv_sorts_by_date_desc(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("全聯", date="2026-05-01"),
        _txn("家樂福", date="2026-06-15"),
        _txn("711", date="2026-06-01"),
    ])
    rows = rows_from_csv(csv_path)
    assert [r[0] for r in rows[1:]] == ["2026-06-15", "2026-06-01", "2026-05-01"]


def test_rows_from_csv_empty_file_returns_header_only(tmp_path):
    csv_path = str(tmp_path / "missing.csv")
    assert rows_from_csv(csv_path) == [HEADER]


def test_rows_from_csv_excludes_transactions_before_archive_cutoff(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("舊的", date="2025-12-31"),
        _txn("新的", date=ARCHIVE_CUTOFF),
    ])
    rows = rows_from_csv(csv_path)
    assert [r[1] for r in rows[1:]] == ["新的"]


def test_archive_rows_from_csv_only_includes_pre_cutoff_transactions(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("舊的", date="2025-12-31"),
        _txn("新的", date=ARCHIVE_CUTOFF),
    ])
    rows = archive_rows_from_csv(csv_path)
    assert rows[0] == HEADER
    assert [r[1] for r in rows[1:]] == ["舊的"]


def test_list_accounts_excludes_accounts_with_only_archived_transactions(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("舊卡消費", date="2025-01-01", bank="Closed", last4="0000"),
        _txn("新卡消費", date="2026-06-01", bank="Cathay", last4="1111"),
    ])
    assert list_accounts(csv_path) == ["Cathay-1111"]


def test_overview_rows_from_csv_builds_month_by_account_matrix(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A1", date="2026-05-01", amount="100.00", bank="Cathay", last4="1111"),
        _txn("A2", date="2026-06-01", amount="50.00", bank="Cathay", last4="1111"),
        _txn("B1", date="2026-06-02", amount="30.00", bank="Fubon", last4="2222"),
        _txn("B2", date="2026-06-03", amount="20.00", bank="Fubon", last4="2222"),
    ])
    rows = overview_rows_from_csv(csv_path, "TWD")
    assert rows[0] == ["月份", "Cathay-1111", "Fubon-2222", TOTAL_LABEL]
    assert rows[1] == ["2026-06", 50.0, 50.0, 100.0]
    assert rows[2] == ["2026-05", 100.0, 0.0, 100.0]
    assert len(rows) == 3  # 不含帳戶合計列（跟每月合計欄重複，使用者反應拿掉）


def test_overview_rows_from_csv_excludes_months_before_archive_cutoff(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("舊的", date="2025-12-31", amount="100.00", bank="Cathay", last4="1111"),
        _txn("新的", date=ARCHIVE_CUTOFF, amount="50.00", bank="Cathay", last4="1111"),
    ])
    rows = overview_rows_from_csv(csv_path, "TWD")
    assert [r[0] for r in rows[1:]] == ["2026-01"]


def test_overview_rows_from_csv_only_includes_matching_currency(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("TWD txn", date="2026-06-01", amount="100.00", bank="Cathay", last4="1111", currency="TWD"),
        _txn("USD txn", date="2026-06-01", amount="30.00", bank="Amex", last4="9999", currency="USD"),
    ])
    twd_rows = overview_rows_from_csv(csv_path, "TWD")
    usd_rows = overview_rows_from_csv(csv_path, "USD")
    assert twd_rows[0] == ["月份", "Cathay-1111", TOTAL_LABEL]
    assert usd_rows[0] == ["月份", "Amex-9999", TOTAL_LABEL]


def test_overview_rows_from_csv_empty_returns_header_only(tmp_path):
    csv_path = str(tmp_path / "missing.csv")
    rows = overview_rows_from_csv(csv_path, "TWD")
    assert rows == [["月份", TOTAL_LABEL]]


def test_combined_overview_stacks_currencies_with_spacer_row(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("TWD txn", date="2026-06-01", amount="100.00", bank="Cathay", last4="1111", currency="TWD"),
        _txn("USD txn", date="2026-06-01", amount="30.00", bank="Amex", last4="9999", currency="USD"),
    ])
    rows, blocks = combined_overview_rows_and_blocks(csv_path)

    # TWD 排字母序在前：表頭、一列月份，接著空一列，再來 USD 那段
    assert rows[0] == ["月份 (TWD)", "Cathay-1111", TOTAL_LABEL]
    assert rows[1][0] == "2026-06"
    assert rows[2] == []
    assert rows[3] == ["月份 (USD)", "Amex-9999", TOTAL_LABEL]

    assert blocks == [
        {"start": 0, "num_months": 1, "num_accounts": 1},
        {"start": 3, "num_months": 1, "num_accounts": 1},
    ]


def test_overview_rows_from_csv_uses_aliases_for_account_columns(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A1", date="2026-06-01", amount="100.00", bank="Fubon", last4="6040"),
        _txn("A2", date="2026-06-02", amount="50.00", bank="Fubon", last4="7254"),
    ])
    aliases = {"Fubon-6040": "富邦帳戶", "Fubon-7254": "富邦JCB卡"}
    rows = overview_rows_from_csv(csv_path, "TWD", aliases)
    assert rows[0] == ["月份", "富邦JCB卡", "富邦帳戶", TOTAL_LABEL]


def test_combined_overview_empty_returns_placeholder_row_and_block(tmp_path):
    csv_path = str(tmp_path / "missing.csv")
    rows, blocks = combined_overview_rows_and_blocks(csv_path)
    assert rows == [["月份", TOTAL_LABEL]]
    assert blocks == [{"start": 0, "num_months": 0, "num_accounts": 0}]










def test_budget_scaffold_rows_has_blank_budget_per_category():
    # 只有 A/B 欄的資料列（分類/預算），不含月份說明/表頭——那兩列每次同步
    # 都重寫，見 budget_info_row/BUDGET_HEADER，不算 scaffold 的一部分
    rows = budget_scaffold_rows()
    # 分類名稱顯示層加 emoji 字首（見 _with_emoji），純好看，不影響底層比對
    assert [r[0] for r in rows] == [_with_emoji(c) for c in BUDGET_CATEGORIES]
    assert all(r[1] == "" for r in rows)


def test_budget_info_row_shows_month_and_currency():
    assert budget_info_row("2026-08", "TWD") == ["月份：2026-08　幣別：TWD"]


def test_budget_info_row_handles_no_recent_month():
    assert budget_info_row(None, "TWD") == ["月份：（無近期資料）　幣別：TWD"]


def test_primary_currency_picks_most_common_among_recent_transactions(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", currency="TWD"), _txn("B", currency="TWD"), _txn("C", currency="USD"),
    ])
    assert primary_currency(csv_path) == "TWD"


def test_primary_currency_defaults_to_twd_when_no_data(tmp_path):
    assert primary_currency(str(tmp_path / "missing.csv")) == "TWD"


def test_primary_currency_ignores_archived_transactions(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("old-usd", date="2025-01-01", currency="USD"),
        _txn("old-usd2", date="2025-02-01", currency="USD"),
        _txn("recent-twd", date="2026-06-01", currency="TWD"),
    ])
    assert primary_currency(csv_path) == "TWD"


def test_latest_recent_month_returns_max_month_for_currency(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", date="2026-05-01", currency="TWD"),
        _txn("B", date="2026-07-01", currency="TWD"),
        _txn("C", date="2026-08-01", currency="USD"),
    ])
    assert latest_recent_month(csv_path, "TWD") == "2026-07"
    assert latest_recent_month(csv_path, "USD") == "2026-08"


def test_latest_recent_month_ignores_archived_and_missing_currency(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [_txn("old", date="2025-01-01", currency="TWD")])
    assert latest_recent_month(csv_path, "TWD") is None
    assert latest_recent_month(csv_path, "GBP") is None


def test_current_month_actuals_groups_by_l1_category(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("超商", date="2026-06-01", amount="50.00", l1="Food"),
        _txn("餐廳", date="2026-06-05", amount="80.00", l1="Food"),
        _txn("捷運", date="2026-06-10", amount="30.00", l1="Transport"),
        _txn("其他月份", date="2026-05-01", amount="999.00", l1="Food"),
    ])
    actual = current_month_actuals(csv_path, "2026-06", "TWD")
    assert actual == {"Food": 130.0, "Transport": 30.0}


def test_monthly_totals_sums_by_month_oldest_first(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", date="2026-06-01", amount="50.00"),
        _txn("B", date="2026-06-05", amount="80.00"),
        _txn("C", date="2026-05-01", amount="30.00"),
        _txn("D", date="2026-07-01", amount="10.00", currency="USD"),
    ])
    # 月份由舊到新（跟 Overview 分頁本身的新到舊排序刻意相反，折線圖要用）
    assert monthly_totals(csv_path, "TWD") == [("2026-05", 30.0), ("2026-06", 130.0)]


def test_monthly_totals_excludes_archived_transactions(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", date="2025-01-01", amount="999.00"),
        _txn("B", date="2026-06-01", amount="50.00"),
    ])
    assert monthly_totals(csv_path, "TWD") == [("2026-06", 50.0)]


def test_category_totals_sums_by_l1_desc_and_drops_non_positive(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", date="2026-06-01", amount="50.00", l1="Food"),
        _txn("B", date="2026-06-01", amount="80.00", l1="Food"),
        _txn("C", date="2026-06-01", amount="30.00", l1="Transport"),
        # 退款沖銷到淨額 <= 0 的分類不進圓餅圖（圓餅圖不支援負值切片）
        _txn("D", date="2026-06-01", amount="-100.00", l1="Other"),
    ])
    assert category_totals(csv_path, "TWD") == [("Food", 130.0), ("Transport", 30.0)]


def test_category_totals_filters_to_given_month_when_provided(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("A", date="2026-06-01", amount="50.00", l1="Food"),
        _txn("B", date="2026-05-01", amount="80.00", l1="Food"),
        _txn("C", date="2026-06-02", amount="30.00", l1="Transport"),
    ])
    assert category_totals(csv_path, "TWD", month="2026-06") == [
        ("Food", 50.0), ("Transport", 30.0),
    ]


def test_read_budget_parses_numeric_rows_and_skips_bad_ones(tmp_path):
    service = FakeService(budget_values=[["Food", "8000"], ["Transport", "abc"], ["Entertainment"]])
    assert read_budget(service, "sheet-123") == {"Food": 8000.0}
    assert service.budget_read_calls == 1


def test_read_budget_returns_empty_when_tab_has_no_values():
    service = FakeService(budget_values=None)
    assert read_budget(service, "sheet-123") == {}


def test_read_budget_strips_emoji_prefix_so_matching_stays_on_plain_names():
    # Budget 分頁的分類欄顯示層有 emoji 字首（見 budget_scaffold_rows），讀
    # 回來要先去掉，不然後面 Budget vs Actual 拿去跟 BUDGET_CATEGORIES/實際
    # 花費分類（都是純文字）比對時會配不到。
    service = FakeService(budget_values=[[_with_emoji("Food"), "8000"], ["Transport", "1000"]])
    assert read_budget(service, "sheet-123") == {"Food": 8000.0, "Transport": 1000.0}


def test_read_budget_categories_returns_column_a_in_row_order():
    service = FakeService(budget_values=[["Food", "8000"], ["Transport", "1000"], ["Other"]])
    assert read_budget_categories(service, "sheet-1") == ["Food", "Transport", "Other"]


def test_read_budget_categories_strips_emoji_prefix():
    # 跟 test_read_budget_strips_emoji_prefix_so_matching_stays_on_plain_names
    # 同樣的理由：這份清單會拿去比對 _HOUSING_CATEGORY（見
    # _budget_reference_formula），留著 emoji 會讓 Housing 特殊邏輯配不到。
    service = FakeService(budget_values=[[_with_emoji("Housing"), "38000"]])
    assert read_budget_categories(service, "sheet-1") == ["Housing"]


def test_build_budget_reference_and_actual_rows_all_three_columns_are_formulas():
    # C:E 三欄現在全部是 Sheets 原生公式（見 build_budget_reference_and_actual_rows
    # 的說明）：近3月平均、實際各自是 SUMPRODUCT 公式，差異用同一列的儲存格
    # 參照（=D{row}-B{row}），會隨 B 欄（使用者填的預算）跟 D 欄即時重算。
    rows = build_budget_reference_and_actual_rows(["Food", "Transport"], start_row=3)
    assert len(rows) == 2
    assert rows[0][0] == _budget_reference_formula("Food")
    assert rows[0][1] == _budget_actual_formula("Food")
    assert rows[0][2] == "=D3-B3"
    assert rows[1][2] == "=D4-B4"


def test_write_budget_reference_and_actual_columns_writes_three_formula_columns():
    service = FakeService()
    write_budget_reference_and_actual_columns(service, "sheet-1", ["Food", "Transport"])
    write = next(u for u in service.updated if u[1] == f"{BUDGET_TITLE}!C3")
    assert write[2] == "USER_ENTERED"  # 不是 RAW，公式字串要被解析成公式
    values = write[3]["values"]
    assert len(values) == 2
    # 分類條件用字串常值（不是 $A{row} 儲存格參照——Budget 分頁 A 欄的顯示
    # 文字有 emoji 字首，跟 Transactions 的純文字分類欄比對不中，見
    # `_budget_reference_month_sum` 的說明），只認 TWD，除以 3 個「已完整
    # 結束」的日曆月（不含本月，見 `_budget_reference_formula` 的說明）；用
    # SUMPRODUCT 陣列比較，不是 SUMIFS 條件字串（後者對純文字日期欄套用 >=
    # 永遠回傳 0）
    assert values[0] == [
        "=SUMPRODUCT(('交易'!$E$2:$E$20000=\"Food\")*"
        "('交易'!$D$2:$D$20000=\"TWD\")*"
        "('交易'!$A$2:$A$20000>=TEXT(EOMONTH(TODAY(),-4)+1,\"yyyy-mm-dd\"))*"
        "('交易'!$A$2:$A$20000<=TEXT(EOMONTH(TODAY(),-1),\"yyyy-mm-dd\"))*"
        "'交易'!$C$2:$C$20000)/3",
        _budget_actual_formula("Food"),
        "=D3-B3",
    ]
    assert '"Transport"' in values[1][0]
    assert '"Food"' not in values[1][0]
    assert '"Transport"' in values[1][1]


def test_write_budget_reference_and_actual_columns_uses_monthly_rent_floor_for_housing():
    # Housing 房租固定 38,000/月，但有時候會用沒被追蹤的帳戶繳，導致某個月
    # 交易金額少算——「近3月平均」逐月取 MAX(該月實際, 38000) 再平均，確保
    # 平均不會被拉低；已經抓到房租的月份（金額本來就 >= 38000）不受影響，
    # 不會被重複加總（見 _budget_reference_formula 的說明）。
    service = FakeService()
    write_budget_reference_and_actual_columns(service, "sheet-1", ["Food", "Housing"])
    write = next(u for u in service.updated if u[1] == f"{BUDGET_TITLE}!C3")
    values = write[3]["values"]

    housing_formula = values[1][0]
    assert housing_formula.startswith("=(")
    assert housing_formula.endswith(")/3")
    assert housing_formula.count("MAX(") == 3
    assert housing_formula.count(",38000)") == 3
    assert housing_formula.count('"Housing"') == 3    # 三段都比對 Housing 這個分類（字串常值）
    # 排除本月：三段窗口是上個月、上上個月、上上上個月，不含本月
    assert "EOMONTH(TODAY(),-2)+1" in housing_formula  # 上個月起始
    assert "EOMONTH(TODAY(),-1)" in housing_formula    # 上個月結束
    assert "EOMONTH(TODAY(),-4)+1" in housing_formula  # 上上上個月起始
    assert "EOMONTH(TODAY(),0)" not in housing_formula  # 不含本月

    # Food 分類不受影響，仍是原本的單一視窗總額/3
    assert "MAX(" not in values[0][0]


def test_goal_actual_month_sum_applies_housing_rent_floor():
    formula = _goal_actual_month_sum(["Food", "Housing"], month_offset=1)
    assert formula.count("MAX(") == 1  # 只有 Housing 那一項套下限
    assert '"Housing"' in formula
    assert '"Food"' in formula
    assert "EOMONTH(TODAY(),-2)+1" in formula  # 上個月起始
    assert "EOMONTH(TODAY(),-1)" in formula    # 上個月結束


def test_goal_actual_formula_averages_three_complete_months():
    formula = _goal_actual_formula(["Entertainment", "Shopping", "Other"])
    assert formula.startswith("=(")
    assert formula.endswith(")/3")
    assert formula.count("SUMPRODUCT(") == 9  # 3 個分類 x 3 個月
    assert "MAX(" not in formula  # 沒有 Housing，不套下限


def test_build_goal_rows_uses_static_target_and_formula_actual_for_mapped_categories():
    rows = build_goal_rows(100000, start_row=2)
    body = {r[0]: r[1:] for r in rows}
    life = body["生活開銷"]
    assert life[0] == 0.65
    assert life[1] == 65000.0  # 目標金額 = 月收入 x 目標% ，Python 算好的靜態值
    assert life[2] == _goal_actual_formula(["Food", "Housing", "Transport", "Medical",
                                            "Education", "Insurance", "Clothing",
                                            "Entertainment", "Shopping", "Other"])
    assert life[3] == "=J3-I3"  # 差異 = 實際欄(J) - 目標金額欄(I)，同一列儲存格參照


def test_build_goal_rows_splits_income_minus_life_actual_by_pct_ratio_for_unmapped_categories():
    # 主動投資/風險預備量不到實際消費，但使用者要的是「收入 - 生活開銷實際
    # 花費」還剩多少可以存，依 20:15 的目標比例拆開——一樣是 Sheets 公式，
    # 生活開銷實際花費（J3）一變就自動重算，不是 Python 算好的靜態值。
    rows = build_goal_rows(100000)
    body = {r[0]: r[1:] for r in rows}
    assert body["主動投資"] == [0.20, 20000.0, "=(100000-(J3))*20/35", "=J4-I4"]
    assert body["風險預備"] == [0.15, 15000.0, "=(100000-(J3))*15/35", "=J5-I5"]


def test_write_goal_rows_writes_header_and_data_starting_row_two():
    # 表頭跟 A:E 主表表頭（第 2 列）對齊，兩張表並排看起來才整齊
    service = FakeService()
    write_goal_rows(service, "sheet-1", 100000)
    write = next(u for u in service.updated if u[1] == f"{BUDGET_TITLE}!G2")
    assert write[2] == "USER_ENTERED"  # 實際/差異欄是公式字串，要被解析成公式
    values = write[3]["values"]
    assert values[0] == GOAL_HEADER
    assert len(values) == 1 + len(GOAL_CATEGORIES)


def test_build_goal_format_requests_colors_diff_column():
    requests = build_goal_format_requests(sheet_id=3)
    kinds = [next(iter(r)) for r in requests]
    assert kinds.count("addConditionalFormatRule") == 2
    diff_ranges = {json.dumps(r["addConditionalFormatRule"]["rule"]["ranges"][0], sort_keys=True)
                  for r in requests if next(iter(r)) == "addConditionalFormatRule"}
    diff_col = GOAL_START_COL + GOAL_HEADER.index("差異")
    # 表頭在第 2 列（index 1），3 個目標分類資料列 -> rows 2..5
    assert json.dumps({"sheetId": 3, "startRowIndex": 2, "endRowIndex": 5,
                       "startColumnIndex": diff_col, "endColumnIndex": diff_col + 1},
                      sort_keys=True) in diff_ranges


def test_sync_sheets_uses_goal_monthly_income_from_caller_not_the_sheet(tmp_path):
    # 月收入不進 Sheet（使用者不想讓收入數字出現在 Sheet 上），改由呼叫端
    # （對應本地 config.yaml 的 goal_monthly_income）傳進來；G2 起的表頭/
    # 目標列每次同步都重寫。
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill", goal_monthly_income=200000)
    goal_writes = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!G2"]
    assert len(goal_writes) == 1
    body = {r[0]: r[1:] for r in goal_writes[0][3]["values"][1:]}
    assert body["生活開銷"][1] == 130000.0  # 目標金額 = 200,000 x 65%

    sync_sheets(csv_path, state_path, service, title="mailquill", goal_monthly_income=200000)
    goal_writes_after = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!G2"]
    assert len(goal_writes_after) == 2  # 每次同步都重寫，沒有 scaffold 保護


def test_detail_default_month_returns_latest_recent_month_across_currencies(tmp_path):
    txns = [
        _txn("A", date="2026-05-01", currency="TWD"),
        _txn("B", date="2026-06-01", currency="USD"),  # 不分幣別，USD 也算
        _txn("C", date="2025-01-01", currency="TWD"),  # ARCHIVE_CUTOFF 之前，不算
    ]
    assert _detail_default_month(txns) == "2026-06"


def test_detail_default_month_is_all_when_no_recent_data():
    assert _detail_default_month([]) == ALL_OPTION

def test_write_month_detail_selectors_writes_month_and_account_defaults():
    service = FakeService()
    write_month_detail_selectors(service, "sheet-1", "2026-06")
    main = next(u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1")
    # RAW，不是 USER_ENTERED：「2026-06」長得像日期，USER_ENTERED 會被 Sheets
    # 解析成日期序號存進 B1，公式裡 $B$1&"-%" 就對不上文字（實測過的 bug）。
    assert main[2] == "RAW"
    assert main[3]["values"] == [["選月份", "2026-06", "選帳戶", ALL_OPTION]]


def test_month_detail_formula_filters_by_month_and_account_with_all_option():
    f = month_detail_formula()
    assert f.startswith("=IFERROR(QUERY('交易'!A2:I,")
    assert "select A,B,C,D,E,F,I" in f
    month = 'IF(ISNUMBER($B$1),TEXT($B$1,"yyyy-mm"),$B$1)'
    assert f'IF({month}="{ALL_OPTION}",""," and A like \'"&{month}&"-%\'")' in f
    assert f'IF($D$1="{ALL_OPTION}",""," and I = \'"&$D$1&"\'")' in f
    assert "order by A desc" in f

def test_write_month_detail_query_rewrites_header_and_formula():
    # 表頭與公式都不算使用者狀態，每次同步都重寫（表頭曾卡在舊名稱，是實際
    # 發生過的 bug）；公式要用 USER_ENTERED 才會被解析成公式。
    service = FakeService()
    write_month_detail_query(service, "sheet-1")
    header = next(u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A3")
    assert (header[2], header[3]["values"]) == ("RAW", [DETAIL_HEADER])
    formula = next(u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A4")
    assert (formula[2], formula[3]["values"]) == ("USER_ENTERED", [[month_detail_formula()]])

def test_detail_months_sorts_recent_months_descending(tmp_path):
    txns = [
        _txn("A", date="2026-04-01"),
        _txn("B", date="2026-06-01"),
        _txn("C", date="2026-05-01"),
        _txn("D", date="2025-01-01"),  # ARCHIVE_CUTOFF 之前，不算
    ]
    assert _detail_months(txns) == [ALL_OPTION, "2026-06", "2026-05", "2026-04"]


def test_build_month_detail_format_requests_sets_dropdowns_and_freezes_header():
    reqs = build_month_detail_format_requests(sheet_id=42, months=[ALL_OPTION, "2026-06"],
                                              accounts=["富邦J卡", "國泰cube卡"])
    freeze = next(r["updateSheetProperties"] for r in reqs if "updateSheetProperties" in r)
    assert freeze["properties"]["gridProperties"]["frozenRowCount"] == 3

    validations = {v["range"]["startColumnIndex"]: v for r in reqs
                   if (v := r.get("setDataValidation"))}
    # ONE_OF_LIST（不是 ONE_OF_RANGE）：實測過 ONE_OF_RANGE 指到公式展開的
    # 範圍時，選單裡的選項會被 Sheets 誤判成「違反資料驗證」拒絕寫入。
    assert {c: [x["userEnteredValue"] for x in v["rule"]["condition"]["values"]]
            for c, v in validations.items()} == {
        1: [ALL_OPTION, "2026-06"],
        3: [ALL_OPTION, "富邦J卡", "國泰cube卡"],
    }
    assert all(v["rule"]["condition"]["type"] == "ONE_OF_LIST" for v in validations.values())
    # 不能嚴格驗證：選了長得像日期的月份，Sheets 自動轉成日期序號後會被當成
    # 「不在清單內」拒絕寫入，使用者看到的就是選了沒反應（實際發生過）。
    assert all(v["rule"]["strict"] is False for v in validations.values())

    # 兩個選單格都強制純文字：不然選了「2026-06」Sheets 會自動轉成日期序號。
    text_cols = {r["repeatCell"]["range"]["startColumnIndex"] for r in reqs
                 if r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {})
                 .get("numberFormat", {}).get("type") == "TEXT"}
    assert text_cols == {1, 3}

def test_build_budget_format_requests_bolds_info_row_and_sets_explicit_widths():
    requests = build_budget_format_requests(sheet_id=3, num_data_rows=0)
    kinds = [next(iter(r)) for r in requests]
    assert "addConditionalFormatRule" not in kinds  # 沒有資料列，不套差異欄顏色
    # 欄寬明確設定（C:E 是公式，autoResize 估不準、表頭「近3月平均」會被截斷）
    assert "autoResizeDimensions" not in kinds
    widths = [r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r]
    assert {(w["range"]["startIndex"], w["range"]["endIndex"]) for w in widths} == {(0, 1), (1, 5)}
    # 第 1 列說明文字跨 A:E 合併，置中時才不會溢出被切掉
    merge = next(r["mergeCells"]["range"] for r in requests if "mergeCells" in r)
    assert merge == {"sheetId": 3, "startRowIndex": 0, "endRowIndex": 1,
                     "startColumnIndex": 0, "endColumnIndex": 5}
    assert requests[0]["updateSheetProperties"]["properties"]["gridProperties"]["frozenRowCount"] == 2


def test_build_budget_format_requests_skips_conditional_rules_when_empty():
    requests = build_budget_format_requests(sheet_id=3, num_data_rows=0)
    kinds = [next(iter(r)) for r in requests]
    assert "addConditionalFormatRule" not in kinds


def test_build_budget_format_requests_colors_diff_column():
    requests = build_budget_format_requests(sheet_id=3, num_data_rows=2)
    kinds = [next(iter(r)) for r in requests]
    assert kinds.count("addConditionalFormatRule") == 2
    diff_ranges = {json.dumps(r["addConditionalFormatRule"]["rule"]["ranges"][0], sort_keys=True)
                  for r in requests if next(iter(r)) == "addConditionalFormatRule"}
    # 表頭在第 2 列（index 1），資料列從第 3 列開始，共 2 筆分類 -> rows 2..4，
    # 差異是第 5 欄（分類/月預算/近3月平均/實際/差異 -> index 4）
    assert json.dumps({"sheetId": 3, "startRowIndex": 2, "endRowIndex": 4,
                       "startColumnIndex": 4, "endColumnIndex": 5}, sort_keys=True) in diff_ranges


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "sheets_state.json")
    assert load_state(path) == {}
    save_state(path, {"spreadsheet_id": "abc", "spreadsheet_url": "https://x"})
    assert load_state(path) == {"spreadsheet_id": "abc", "spreadsheet_url": "https://x"}
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["spreadsheet_id"] == "abc"


def test_build_format_requests_skips_data_row_formatting_when_empty():
    requests = build_format_requests(sheet_id=5, num_data_rows=0)
    kinds = [next(iter(r)) for r in requests]
    assert kinds == ["updateSheetProperties", "repeatCell", "addBanding", "autoResizeDimensions",
                     "updateDimensionProperties"]
    # 日期欄固定寬度，排在 autoResize 之後才蓋得過（置中後太擠）
    date_w = requests[-1]["updateDimensionProperties"]
    assert (date_w["range"]["startIndex"], date_w["range"]["endIndex"]) == (0, 1)
    assert date_w["properties"]["pixelSize"] == 110


def test_build_format_requests_header_is_colored_and_bold():
    requests = build_format_requests(sheet_id=5, num_data_rows=0)
    header_req = next(r for r in requests if next(iter(r)) == "repeatCell")
    fmt = header_req["repeatCell"]["cell"]["userEnteredFormat"]
    assert fmt["backgroundColor"] == _HEADER_BG
    assert fmt["textFormat"]["bold"] is True
    assert fmt["textFormat"]["foregroundColor"] == _HEADER_FG


def test_build_format_requests_bands_header_and_data_rows():
    requests = build_format_requests(sheet_id=5, num_data_rows=3, num_cols=4)
    band_req = next(r for r in requests if next(iter(r)) == "addBanding")
    band_range = band_req["addBanding"]["bandedRange"]["range"]
    assert band_range == {"sheetId": 5, "startRowIndex": 0, "endRowIndex": 4,
                          "startColumnIndex": 0, "endColumnIndex": 4}


def test_build_format_requests_includes_amount_formatting_and_conditional_rules():
    requests = build_format_requests(sheet_id=5, num_data_rows=3)
    kinds = [next(iter(r)) for r in requests]
    # 只有負數（退款）標綠；正數＝消費維持預設黑字，不然整頁全綠
    assert kinds.count("addConditionalFormatRule") == 1
    rule = next(r for r in requests if "addConditionalFormatRule" in r)["addConditionalFormatRule"]["rule"]
    assert rule["booleanRule"]["condition"]["type"] == "NUMBER_LESS"
    assert "setBasicFilter" in kinds
    number_format_reqs = [r for r in requests if next(iter(r)) == "repeatCell"
                          and "numberFormat" in json.dumps(r)]
    assert len(number_format_reqs) == 1
    amount_range = number_format_reqs[0]["repeatCell"]["range"]
    assert amount_range == {"sheetId": 5, "startRowIndex": 1, "endRowIndex": 4,
                            "startColumnIndex": 2, "endColumnIndex": 3}


def test_foreign_currency_blocks_merges_consecutive_rows():
    assert _foreign_currency_blocks(
        ["TWD", "USD", "USD", "TWD", "TWD", "JPY", ""]) == [(1, 3), (5, 6)]


def test_foreign_currency_blocks_all_twd_is_empty():
    assert _foreign_currency_blocks(["TWD", "", "TWD"]) == []


def test_foreign_currency_blocks_trailing_foreign_run():
    assert _foreign_currency_blocks(["TWD", "USD", "USD"]) == [(1, 3)]


def test_build_format_requests_amount_column_defaults_to_twd_integer_format():
    requests = build_format_requests(sheet_id=5, num_data_rows=3,
                                     currencies=["TWD", "TWD", "TWD"])
    number_format_reqs = [r for r in requests if next(iter(r)) == "repeatCell"
                          and "numberFormat" in json.dumps(r)]
    assert len(number_format_reqs) == 1
    fmt = number_format_reqs[0]["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
    assert fmt["pattern"] == "#,##0"


def test_build_format_requests_overrides_foreign_currency_rows_with_two_decimals():
    # 列 0,2 是 TWD，列 1 是 USD：整欄先鋪台幣整數格式，USD 那一列（資料列
    # index 1 -> sheet 第 2 列）再疊一個固定兩位小數的格式蓋過去。
    requests = build_format_requests(sheet_id=5, num_data_rows=3,
                                     currencies=["TWD", "USD", "TWD"])
    number_format_reqs = [r for r in requests if next(iter(r)) == "repeatCell"
                          and "numberFormat" in json.dumps(r)]
    assert len(number_format_reqs) == 2
    base, override = (r["repeatCell"] for r in number_format_reqs)
    assert base["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "#,##0"
    assert override["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "#,##0.00"
    assert override["range"] == {"sheetId": 5, "startRowIndex": 2, "endRowIndex": 3,
                                 "startColumnIndex": 2, "endColumnIndex": 3}


def test_build_combined_overview_format_requests_covers_all_data_rows():
    # 沒有帳戶合計列了（已拿掉），負紅/正綠跟千分位數字格式該蓋滿所有資料列
    # （header_row+1 到 header_row+num_months+1，不多也不少）。
    blocks = [{"start": 0, "num_months": 2, "num_accounts": 1}]
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)

    for r in requests:
        if next(iter(r)) != "addConditionalFormatRule":
            continue
        rng = r["addConditionalFormatRule"]["rule"]["ranges"][0]
        assert rng["startRowIndex"] == 1 and rng["endRowIndex"] == 3

    number_format_reqs = [r for r in requests if next(iter(r)) == "repeatCell"
                          and "numberFormat" in json.dumps(r)]
    assert any(r["repeatCell"]["range"]["endRowIndex"] == 3 for r in number_format_reqs)


def test_build_combined_overview_format_requests_resets_text_format_on_every_data_row():
    # 迴歸測試：矩陣列會隨月份增加往下位移，同一個列位置在不同次同步可能
    # 對到不同月份；曾經發生過某一列卡著舊格式（粗體＋白字）沒被蓋掉，數字
    # 看起來像消失了（見 sheets_sync 的說明）。每次同步都要整段 reset 回
    # 預設，蓋過任何殘留格式，之後的月份欄粗體/正紅負綠再疊上去。
    blocks = [{"start": 0, "num_months": 2, "num_accounts": 1}]
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)
    reset_reqs = [r for r in requests if next(iter(r)) == "repeatCell"
                 and r["repeatCell"].get("fields") == "userEnteredFormat.textFormat(bold,foregroundColor)"]
    assert len(reset_reqs) == 1
    reset = reset_reqs[0]["repeatCell"]
    assert reset["range"] == {"sheetId": 9, "startRowIndex": 1, "endRowIndex": 3,
                              "startColumnIndex": 0, "endColumnIndex": 3}
    assert reset["cell"]["userEnteredFormat"]["textFormat"] == {
        "bold": False, "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}}
    # reset 要排在欄 0 粗體（月份名稱）之前，不然會把粗體洗掉
    kinds_order = [json.dumps(r) for r in requests]
    reset_idx = kinds_order.index(json.dumps(reset_reqs[0]))
    col0_bold_idx = next(i for i, r in enumerate(requests) if next(iter(r)) == "repeatCell"
                         and r["repeatCell"]["range"].get("endColumnIndex") == 1
                         and r["repeatCell"]["fields"] == "userEnteredFormat.textFormat.bold")
    assert reset_idx < col0_bold_idx


def test_build_combined_overview_format_requests_without_column_widths_only_autosizes():
    # 沒傳 column_widths（純函式測試常見情境，不準備真的列內容）時，欄寬
    # 完全交給 autoResizeDimensions 猜，不主動加 updateDimensionProperties。
    blocks = [{"start": 0, "num_months": 2, "num_accounts": 1}]
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)
    kinds = [next(iter(r)) for r in requests]
    assert "updateDimensionProperties" not in kinds


def test_build_combined_overview_format_requests_applies_explicit_column_widths():
    # 月份表頭（如「月份 (TWD)」/帳戶別名如「富邦JCB卡」）是全形字混英數，
    # autoResize 對 CJK 字寬的估算常常偏窄、把文字截斷掉；呼叫端算好實際
    # 需要的寬度傳進來，明確蓋過 autoResize 的猜測。
    blocks = [{"start": 0, "num_months": 2, "num_accounts": 1}]
    requests = build_combined_overview_format_requests(
        sheet_id=9, blocks=blocks, column_widths=[130, 0, 90])
    dim_reqs = {r["updateDimensionProperties"]["range"]["startIndex"]:
                r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r}
    # 欄 1 寬度是 0（沒有需要保證的寬度）——不該生出對應的 request
    assert set(dim_reqs) == {0, 2}
    assert dim_reqs[0]["properties"]["pixelSize"] == 130
    assert dim_reqs[0]["range"] == {"sheetId": 9, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}
    assert dim_reqs[2]["properties"]["pixelSize"] == 90
    assert dim_reqs[0]["fields"] == "pixelSize"


def test_text_pixel_width_cjk_and_emoji_wider_than_ascii():
    assert _text_pixel_width("Food") < _text_pixel_width("🎬 Entertainment")
    assert _text_pixel_width("TWD") < _text_pixel_width("月份")


def test_column_pixel_widths_scans_all_rows_not_just_header():
    # 分類佔比表的資料列（emoji+英文分類名稱）常常比表頭「分類」寬，欄寬要
    # 算過所有列，不能只看表頭。
    rows = [["分類", "金額"], ["🎬 Entertainment", 18621], ["Food", 131141]]
    widths = _column_pixel_widths(rows, 2)
    assert widths[0] == _text_pixel_width("🎬 Entertainment")
    # 金額欄的數字也要計入（以 #,##0 顯示的寬度）：明確設定的欄寬會蓋過
    # autoResize，只看表頭「富邦」這種短字，大金額會被擠成 ###（實際發生過）。
    assert widths[1] == _text_pixel_width("131,141")


def test_column_pixel_widths_counts_numbers_wider_than_short_header():
    widths = _column_pixel_widths([["富邦"], [165583], [-2500.5]], 1)
    assert widths[0] == _text_pixel_width("165,583") > _text_pixel_width("富邦")


def test_sync_sheets_resets_formatting_on_rewritten_tabs_only(tmp_path):
    # Overview 每次整頁重寫；舊版面留下的格式（如被移除的圖表來源表
    # 表頭深色底）清值不會一起清掉，要先整頁重設格式與欄寬。Budget 有使用者
    # 手填的資料，不能動。
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", amount="50.00", l1="Food")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    ids = load_state(state_path)["sheet_ids"]
    first = next(b["requests"] for b in service.batch_updates
                 if any("updateCells" in r for r in b["requests"]))
    # 重設要跟清舊裝飾同一批、搶在任何格式套用之前
    assert first is not service.batch_updates[-1]["requests"]
    reset = {r["updateCells"]["range"]["sheetId"] for r in first if "updateCells" in r}
    assert reset == {ids[OVERVIEW_TITLE]}
    assert all(r["updateCells"]["fields"] == "userEnteredFormat" for r in first if "updateCells" in r)
    widths = {r["updateDimensionProperties"]["range"]["sheetId"] for r in first
              if "updateDimensionProperties" in r}
    assert widths == {ids[OVERVIEW_TITLE]}


def test_column_pixel_widths_skips_empty_cells():
    assert _column_pixel_widths([["", "x"], [None, ""]], 2) == [0, _text_pixel_width("x")]


def test_build_combined_overview_format_requests_skips_value_formatting_when_block_empty():
    blocks = [{"start": 0, "num_months": 0, "num_accounts": 0}]
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)
    kinds = [next(iter(r)) for r in requests]
    assert "addConditionalFormatRule" not in kinds


def test_build_combined_overview_format_requests_offsets_each_block_by_start_row():
    blocks = [
        {"start": 0, "num_months": 2, "num_accounts": 1},
        {"start": 5, "num_months": 1, "num_accounts": 3},
    ]
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)
    kinds = [next(iter(r)) for r in requests]
    # 矩陣全是消費金額，不上正綠負紅（整片綠只是雜訊）
    assert kinds.count("addConditionalFormatRule") == 0

    value_ranges = {json.dumps(r["repeatCell"]["range"], sort_keys=True)
                    for r in requests if "repeatCell" in r
                    and "numberFormat" in r["repeatCell"]["fields"]}
    # block 1：header 在第 0 列，資料列 1-2 -> 數字格式蓋資料列 1..3
    assert json.dumps({"sheetId": 9, "startRowIndex": 1, "endRowIndex": 3,
                       "startColumnIndex": 1, "endColumnIndex": 3}, sort_keys=True) in value_ranges
    # block 2：header 在第 5 列，資料列 6 -> value range rows 6..7
    assert json.dumps({"sheetId": 9, "startRowIndex": 6, "endRowIndex": 7,
                       "startColumnIndex": 1, "endColumnIndex": 5}, sort_keys=True) in value_ranges










def test_sync_sheets_creates_only_core_tabs_on_first_run(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [
        _txn("全聯", bank="Cathay", last4="1111"),
        _txn("家樂福", bank="Fubon", last4="2222"),
    ])
    service = FakeService()

    r = sync_sheets(csv_path, state_path, service, title="mailquill")

    assert r.created is True
    assert r.rows == 2
    assert r.accounts == ["Cathay-1111", "Fubon-2222"]  # 帳戶仍回報（給選單用），但不開分頁

    state = load_state(state_path)
    assert set(state["sheet_ids"]) == {SHEET_TITLE, ARCHIVE_TITLE, OVERVIEW_TITLE,
                                       BUDGET_TITLE, DETAIL_TITLE}
    # 預算/明細查詢的使用者狀態不透過會 clear 整個分頁的 _write_tab
    written_tabs = {range_ for _, range_ in service.cleared}
    assert written_tabs == {SHEET_TITLE, ARCHIVE_TITLE, OVERVIEW_TITLE}
    add_titles = {req["addSheet"]["properties"]["title"]
                 for body in service.batch_updates for req in body["requests"]
                 if "addSheet" in req}
    assert add_titles == {ARCHIVE_TITLE, OVERVIEW_TITLE, BUDGET_TITLE, DETAIL_TITLE}


def test_sync_sheets_account_dropdown_uses_aliases(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [
        _txn("全聯", bank="Fubon", last4="6040"),
        _txn("家樂福", bank="Fubon", last4="7254"),
    ])
    service = FakeService()
    aliases = {"Fubon-6040": "富邦帳戶", "Fubon-7254": "富邦JCB卡"}

    r = sync_sheets(csv_path, state_path, service, title="mailquill", aliases=aliases)

    assert r.accounts == ["富邦JCB卡", "富邦帳戶"]
    detail_id = load_state(state_path)["sheet_ids"][DETAIL_TITLE]
    account_options = next(
        [v["userEnteredValue"] for v in dv["rule"]["condition"]["values"]]
        for body in service.batch_updates for req in body["requests"]
        if (dv := req.get("setDataValidation")) and dv["range"]["sheetId"] == detail_id
        and dv["range"]["startColumnIndex"] == 3)
    assert account_options == [ALL_OPTION, "富邦JCB卡", "富邦帳戶"]
    main_update = next(u for u in service.updated if u[1] == f"{SHEET_TITLE}!A1")
    assert {row[8] for row in main_update[3]["values"][1:]} == {"富邦帳戶", "富邦JCB卡"}


def test_sync_sheets_renames_legacy_english_tabs_in_place(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", bank="Cathay", last4="1111")])
    legacy = {"Transactions": 1, "Archive": 2, "Overview": 3, "Budget": 4, "Month Detail": 5,
              "國泰cube卡": 6}
    save_state(state_path, {"spreadsheet_id": "sheet-123",
                            "spreadsheet_url": "https://sheets.google/sheet-123",
                            "sheet_ids": dict(legacy)})
    service = FakeService(existing_sheets=legacy, budget_values=[["Food", "5000"]])

    sync_sheets(csv_path, state_path, service, title="mailquill")

    state = load_state(state_path)
    # 同一個 sheetId 改名，不是刪掉重建；舊的帳戶分頁被移除
    assert state["sheet_ids"] == {SHEET_TITLE: 1, ARCHIVE_TITLE: 2, OVERVIEW_TITLE: 3,
                                  BUDGET_TITLE: 4, DETAIL_TITLE: 5}
    reqs = [r for body in service.batch_updates for r in body["requests"]]
    renamed = {r["updateSheetProperties"]["properties"]["sheetId"]:
               r["updateSheetProperties"]["properties"]["title"]
               for r in reqs if r.get("updateSheetProperties", {}).get("fields") == "title"}
    assert renamed == {1: SHEET_TITLE, 2: ARCHIVE_TITLE, 3: OVERVIEW_TITLE,
                       4: BUDGET_TITLE, 5: DETAIL_TITLE}
    assert not any("addSheet" in r for r in reqs)
    assert {"deleteSheet": {"sheetId": 6}} in reqs
    # 預算骨架不重寫（使用者填的月預算保留）；明細查詢補上帳戶選單
    assert not any(u[1] == f"{BUDGET_TITLE}!A3" for u in service.updated)
    assert any(u[1] == f"{DETAIL_TITLE}!A1" for u in service.updated)


def test_build_tab_order_requests_orders_and_hides_archive():
    ids = {SHEET_TITLE: 1, ARCHIVE_TITLE: 2, OVERVIEW_TITLE: 3, BUDGET_TITLE: 4, DETAIL_TITLE: 5}
    props = [r["updateSheetProperties"]["properties"] for r in build_tab_order_requests(ids)]
    assert [(p["sheetId"], p["index"], p["hidden"]) for p in props] == [
        (3, 0, False), (4, 1, False), (5, 2, False), (1, 3, False), (2, 4, True)]


def test_sync_sheets_moves_old_transactions_into_archive_tab(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [
        _txn("舊的", date="2025-01-01", bank="Cathay", last4="1111"),
        _txn("新的", date="2026-06-01", bank="Cathay", last4="1111"),
    ])
    service = FakeService()

    r = sync_sheets(csv_path, state_path, service, title="mailquill")

    assert r.rows == 1
    assert r.archived_rows == 1
    main_update = next(u for u in service.updated if u[1] == f"{SHEET_TITLE}!A1")
    archive_update = next(u for u in service.updated if u[1] == f"{ARCHIVE_TITLE}!A1")
    assert [row[1] for row in main_update[3]["values"][1:]] == ["新的"]
    assert [row[1] for row in archive_update[3]["values"][1:]] == ["舊的"]


def test_sync_sheets_prunes_legacy_per_currency_overview_and_closed_accounts(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    # 模擬改版前殘留的 Overview-TWD 分頁 + 一個現在 CSV 裡已經沒有的帳戶分頁
    service = FakeService(existing_sheets={
        SHEET_TITLE: 1, "Overview-TWD": 2, "Closed-0000": 3,
    })
    save_state(state_path, {
        "spreadsheet_id": "sheet-123", "spreadsheet_url": "https://sheets.google/sheet-123",
        "sheet_ids": {SHEET_TITLE: 1, "Overview-TWD": 2, "Closed-0000": 3},
    })

    sync_sheets(csv_path, state_path, service, title="mailquill")

    delete_ids = {req["deleteSheet"]["sheetId"] for body in service.batch_updates
                 for req in body["requests"] if "deleteSheet" in req}
    assert delete_ids == {2, 3}
    state = load_state(state_path)
    assert "Overview-TWD" not in state["sheet_ids"]
    assert "Closed-0000" not in state["sheet_ids"]
    assert OVERVIEW_TITLE in state["sheet_ids"]


def test_sync_sheets_clears_existing_conditional_format_rules_before_readding(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    state = load_state(state_path)
    txn_sheet_id = state["sheet_ids"][SHEET_TITLE]

    # 模擬上一次同步已經在 Transactions 分頁留下 2 條規則（沒清掉的話這次會變 4 條）
    service.conditional_formats = {txn_sheet_id: 2}
    service.batch_updates.clear()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    all_requests = [req for body in service.batch_updates for req in body["requests"]]
    delete_count = sum(1 for req in all_requests
                      if req.get("deleteConditionalFormatRule", {}).get("sheetId") == txn_sheet_id)
    assert delete_count == 2
    # 刪除必須排在新增規則之前，不然新規則會先插入又被刪掉
    delete_idx = next(i for i, req in enumerate(all_requests) if "deleteConditionalFormatRule" in req)
    add_idx = next(i for i, req in enumerate(all_requests) if "addConditionalFormatRule" in req)
    assert delete_idx < add_idx


def test_sync_sheets_clears_existing_banded_ranges_before_readding(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    state = load_state(state_path)
    txn_sheet_id = state["sheet_ids"][SHEET_TITLE]

    # 模擬上一次同步已經在 Transactions 分頁留下一條 banding（沒清掉的話這次
    # 重疊會被 Sheets API 拒絕，這裡先確認我們有先送 deleteBanding）。
    service.banded_ranges = {txn_sheet_id: [777]}
    service.batch_updates.clear()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    all_requests = [req for body in service.batch_updates for req in body["requests"]]
    delete_reqs = [req for req in all_requests
                  if req.get("deleteBanding", {}).get("bandedRangeId") == 777]
    assert len(delete_reqs) == 1
    delete_idx = next(i for i, req in enumerate(all_requests) if "deleteBanding" in req)
    add_idx = next(i for i, req in enumerate(all_requests) if "addBanding" in req)
    assert delete_idx < add_idx


def test_sync_sheets_unmerges_cells_before_readding_layout(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    state = load_state(state_path)
    txn_sheet_id = state["sheet_ids"][SHEET_TITLE]

    # 模擬分頁上還殘留舊版面留下的合併儲存格（例如試過的並排版型）——沒清掉
    # 的話，改版面版型之後新資料寫進去會跟舊的合併邊界對不上、整個跑版。
    service.merged_sheets = {txn_sheet_id}
    service.batch_updates.clear()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    all_requests = [req for body in service.batch_updates for req in body["requests"]]
    unmerge_reqs = [req for req in all_requests
                    if req.get("unmergeCells", {}).get("range", {}).get("sheetId") == txn_sheet_id]
    assert len(unmerge_reqs) == 1


def test_sync_sheets_skips_unmerge_when_sheet_has_no_merges(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    service.batch_updates.clear()

    sync_sheets(csv_path, state_path, service, title="mailquill")

    all_requests = [req for body in service.batch_updates for req in body["requests"]]
    assert not any("unmergeCells" in req for req in all_requests)


def test_sync_sheets_reuses_tabs_without_recreating_on_second_run(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])

    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")

    # 第二次同步：狀態檔已經有全部分頁的 sheetId，不該再查一次分頁清單
    # （但清條件式格式規則那次 get 仍會發生，兩者分開計數）
    service.tab_lookup_calls = 0
    service.batch_updates.clear()
    r = sync_sheets(csv_path, state_path, service, title="mailquill")

    assert r.created is False
    assert service.tab_lookup_calls == 0
    add_requests = [req for body in service.batch_updates for req in body["requests"]
                    if "addSheet" in req]
    assert add_requests == []


def test_sync_sheets_scaffolds_budget_tab_only_on_first_run(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", bank="Cathay", last4="1111")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")
    scaffold_writes = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]
    assert len(scaffold_writes) == 1
    assert scaffold_writes[0][3]["values"] == budget_scaffold_rows()

    # 第二次同步：Budget 分頁的分類/預算資料列（A3 起）已經存在，不該再被
    # 清空重寫（使用者填的數字要留著）；月份說明/表頭（A1）則每次都重寫，
    # 見 test_sync_sheets_writes_recent_average_formula_and_actual_every_run。
    service.budget_values = [[c, ""] for c in BUDGET_CATEGORIES]
    r = sync_sheets(csv_path, state_path, service, title="mailquill")
    scaffold_writes_after = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]
    assert len(scaffold_writes_after) == 1  # 沒有新增第二筆
    info_writes = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A1"]
    assert len(info_writes) == 2  # 每次同步都重寫
    assert r.budget_month == "2026-06"


def test_sync_sheets_scaffolds_month_detail_tab_only_on_first_run(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", bank="Cathay", last4="1111")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")
    writes = [u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1"]
    assert len(writes) == 1
    assert writes[0][3]["values"][0] == ["選月份", "2026-06", "選帳戶", ALL_OPTION]

    # 第二次同步：Month Detail 的 B1（選到的月份）已經存在，不該再被清空重寫
    # （使用者選的月份要留著）；表頭（A3）不算使用者狀態，兩次同步都要寫。
    sync_sheets(csv_path, state_path, service, title="mailquill")
    writes_after = [u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1"]
    assert len(writes_after) == 1  # 沒有新增第二筆
    assert not any(range_ == DETAIL_TITLE for _, range_ in service.cleared)
    header_writes = [u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A3"]
    assert len(header_writes) == 2
    assert header_writes[0][3]["values"] == [DETAIL_HEADER]


def test_sync_sheets_refreshes_month_detail_dropdown_options_every_run(tmp_path):
    # 下拉選單「選項清單」（跟 B1 現在選到的值不同）每次同步都該更新，讓新
    # 月份的資料一出現就能選——不像 A1 的 scaffold 只寫一次。
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", bank="Cathay", last4="1111")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")
    state = load_state(state_path)
    detail_sheet_id = state["sheet_ids"][DETAIL_TITLE]

    def _dropdown_months(body):
        for req in body["requests"]:
            dv = req.get("setDataValidation")
            if (dv and dv["range"]["sheetId"] == detail_sheet_id
                    and dv["range"]["startColumnIndex"] == 1):
                return [v["userEnteredValue"] for v in dv["rule"]["condition"]["values"]]
        return None

    first_months = next(m for body in service.batch_updates
                        for m in [_dropdown_months(body)] if m is not None)
    assert first_months == [ALL_OPTION, "2026-06"]

    append_transactions(csv_path, [_txn("家樂福", date="2026-07-01", bank="Cathay", last4="1111")])
    service.batch_updates.clear()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    second_months = next(m for body in service.batch_updates
                         for m in [_dropdown_months(body)] if m is not None)
    assert second_months == [ALL_OPTION, "2026-07", "2026-06"]


def test_sync_sheets_writes_recent_average_formula_and_actual_every_run(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [
        _txn("超商", date="2026-06-01", amount="900.00", l1="Food", bank="Cathay", last4="1111"),
    ])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill")
    ref_writes = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!C3"]
    assert len(ref_writes) == 1
    assert ref_writes[0][2] == "USER_ENTERED"
    # 第一次同步（新建的 Budget 分頁）用 BUDGET_CATEGORIES 順序，Food 排第
    # 一列（第 3 列）：三欄都是公式，見 build_budget_reference_and_actual_rows
    assert ref_writes[0][3]["values"][0] == [
        _budget_reference_formula("Food"), _budget_actual_formula("Food"), "=D3-B3",
    ]

    # 第二次同步：跟 A/B 欄不同，C:E 欄每次都要重寫（同一套公式模板），行數
    # 對齊現有分類列數。FakeService 不會真的記錄 update() 寫進去的內容，這裡
    # 手動模擬「Budget 分頁 A3:B 現在讀得到 scaffold 寫過的分類清單」，對應
    # 真實 Sheets 的狀態。
    service.budget_values = [[c, ""] for c in BUDGET_CATEGORIES]
    sync_sheets(csv_path, state_path, service, title="mailquill")
    ref_writes_after = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!C3"]
    assert len(ref_writes_after) == 2
    assert ref_writes_after[1][3]["values"] == ref_writes[0][3]["values"]

    # 使用者填的 A/B 欄骨架仍然只在第一次寫過一次
    scaffold_writes = [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]
    assert len(scaffold_writes) == 1


def test_sync_sheets_aligns_formula_rows_to_existing_budget_category_order(tmp_path):
    # Budget 分頁已經存在（不是第一次建立）時，分類列順序改用現有的順序
    # （見 read_budget_categories），不假設還是 BUDGET_CATEGORIES 當初
    # scaffold 的順序——這裡模擬使用者只留了一列「Food」，公式要對齊那一列
    # （第 3 列）。
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [
        _txn("超商", date="2026-06-01", amount="9200.00", l1="Food", bank="Cathay", last4="1111"),
    ])
    service = FakeService(
        existing_sheets={SHEET_TITLE: 1, BUDGET_TITLE: 2}, budget_values=[["Food", "8000"]])
    save_state(state_path, {
        "spreadsheet_id": "sheet-123", "spreadsheet_url": "https://sheets.google/sheet-123",
        "sheet_ids": {SHEET_TITLE: 1, BUDGET_TITLE: 2},
    })

    sync_sheets(csv_path, state_path, service, title="mailquill")

    ref_write = next(u for u in service.updated if u[1] == f"{BUDGET_TITLE}!C3")
    rows = ref_write[3]["values"]
    assert len(rows) == 1
    assert rows[0] == [_budget_reference_formula("Food"), _budget_actual_formula("Food"), "=D3-B3"]


def _stmt_txn(merchant, bank, date, source_type="pdf"):
    from dataclasses import replace
    return replace(_txn(merchant, bank=bank, last4="1", date=date), source_type=source_type)


def _bill(bank, period, amount_due="100", min_due=""):
    return BillDue(bank=bank, period=period, amount_due=amount_due, min_due=min_due,
                   source_msg_id="m")


def test_bill_status_rows_merge_amount_due_and_sync_status_per_bank():
    from mailquill.sheets_sync import _bill_status_rows
    txns = [
        _stmt_txn("a", "Cathay", "2026-09-14"),
        _stmt_txn("n", "Cathay", "2026-09-27", source_type="email_body"),
        _stmt_txn("b", "Amex", "2026-07-27", source_type="csv"),
    ]
    bills = [_bill("Cathay", "2026-08", "999"), _bill("Cathay", "2026-09", "3402", "1969")]
    rows = _bill_status_rows(txns, bills, today="2026-09-30")
    # 應繳金額緊接銀行名稱；同步狀態（狀態／帳單月份）放最後
    assert rows[0] == ["帳單", "應繳總額", "最低應繳", "明細至", "每日通知至", "狀態 09/30", "帳單月份"]
    # 沒有應繳資料的銀行（Amex）金額欄留空、月份標 (估)
    assert rows[1] == ["Amex", "", "", "2026-07-27", "-", "❌ 落後2個月", "2026-07 (估)"]
    # 金額只取最新一期
    assert rows[2] == ["Cathay", 3402.0, 1969.0, "2026-09-14", "2026-09-27", "✅ 最新", "2026-09"]


def test_bill_status_rows_blank_min_due_stays_blank():
    from mailquill.sheets_sync import _bill_status_rows
    rows = _bill_status_rows([], [_bill("HSBC", "2026-09", "19711")], today="2026-09-30")
    assert rows[1][1:3] == [19711.0, ""]


def test_bill_status_rows_empty_without_data():
    from mailquill.sheets_sync import _bill_status_rows
    assert _bill_status_rows([], [], today="2026-09-30") == []


def test_bill_month_total_row_sums_this_months_bills():
    from mailquill.sheets_sync import _bill_month_total_row
    txns = [_stmt_txn("b", "Amex", "2026-09-16", source_type="csv")]
    bills = [_bill("CTBC", "2026-10", "17765", "1800"),
             _bill("Cathay", "2026-10", "43184", "4328"),
             _bill("Fubon", "2026-09", "2810", "800"),     # 上個月的已經繳過，不算
             _bill("HSBC", "2026-10", "22898", "")]        # 最低應繳缺的當 0
    row = _bill_month_total_row(txns, bills, today="2026-10-24")
    assert row == ["10月應繳", 83847.0, 6128.0, "未到 Amex、Fubon"]


def test_bill_month_total_row_flags_arrived_bill_without_amount():
    # Amex 本月明細已到但沒有應繳金額資料：不是「未到」，是金額沒算進去
    from mailquill.sheets_sync import _bill_month_total_row
    txns = [_stmt_txn("b", "Amex", "2026-10-16", source_type="csv")]
    row = _bill_month_total_row(txns, [_bill("HSBC", "2026-10", "100", "10")],
                                today="2026-10-24")
    assert row == ["10月應繳", 100.0, 10.0, "Amex 沒有金額"]


def test_bill_month_total_row_all_in():
    from mailquill.sheets_sync import _bill_month_total_row
    row = _bill_month_total_row([], [_bill("HSBC", "2026-10", "100", "10")], today="2026-10-24")
    assert row == ["10月應繳", 100.0, 10.0, "全部到齊"]


def test_build_bill_total_format_requests():
    from mailquill.sheets_sync import build_bill_total_format_requests
    reqs = build_bill_total_format_requests(sheet_id=3, row=0)
    merge = next(r["mergeCells"]["range"] for r in reqs if "mergeCells" in r)
    assert (merge["startRowIndex"], merge["startColumnIndex"], merge["endColumnIndex"]) == (0, 3, 7)
    num = next(r["repeatCell"]["range"] for r in reqs if "repeatCell" in r
               and "numberFormat" in r["repeatCell"]["cell"]["userEnteredFormat"])
    assert (num["startColumnIndex"], num["endColumnIndex"], num["startRowIndex"]) == (1, 3, 0)


def test_build_bill_status_format_requests():
    from mailquill.sheets_sync import build_bill_status_format_requests
    assert build_bill_status_format_requests(sheet_id=3, start_row=20, num_data_rows=0) == []
    reqs = build_bill_status_format_requests(sheet_id=3, start_row=20, num_data_rows=5)
    header = reqs[0]["repeatCell"]["range"]
    assert (header["startRowIndex"], header["endRowIndex"], header["endColumnIndex"]) == (20, 21, 7)
    band = next(r["addBanding"]["bandedRange"]["range"] for r in reqs if "addBanding" in r)
    assert (band["startRowIndex"], band["endRowIndex"], band["endColumnIndex"]) == (20, 26, 7)
    num = next(r["repeatCell"]["range"] for r in reqs if "repeatCell" in r
               and "numberFormat" in r["repeatCell"]["cell"]["userEnteredFormat"])
    assert (num["startColumnIndex"], num["endColumnIndex"], num["startRowIndex"]) == (1, 3, 21)
    rules = [r["addConditionalFormatRule"]["rule"] for r in reqs if "addConditionalFormatRule" in r]
    assert {r["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in rules} \
        == {"✅", "⚠️", "❌"}
    assert rules[0]["ranges"][0]["startColumnIndex"] == 5


def test_sync_sheets_puts_single_bill_status_block_at_top_of_overview(tmp_path):
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    bills_path = str(tmp_path / "bills.csv")
    append_transactions(csv_path, [_txn("全聯", bank="HSBC", last4="", date="2026-06-01", amount="50.00")])
    append_bills(bills_path, [_bill("HSBC", "2026-08", "19711", "2233")])
    service = FakeService()

    sync_sheets(csv_path, state_path, service, title="mailquill", bills_path=bills_path,
                today="2026-09-30")

    rows = next(u for u in service.updated if u[1] == f"{OVERVIEW_TITLE}!A1")[3]["values"]
    idx = [i for i, r in enumerate(rows) if r and r[0] == "帳單"]
    assert idx == [1]                          # 只有一張合併後的表，放最上面（最需要行動）
    assert rows[0][:4] == ["9月應繳", 0, 0, "未到 HSBC"]   # 最上面是本月應繳合計
    assert rows[idx[0] + 2][:1] in ([], [""])  # 隔一空列跟下面的圖表分開
    row = rows[idx[0] + 1]
    assert row[:3] == ["HSBC", 19711.0, 2233.0]
    assert row[5:7] == ["⚠️ 本月未到", "2026-08"]
    assert not any(r and r[0] in ("本期應繳", "同步狀態") for r in rows)



def test_build_readability_requests_sets_row_heights_and_middle_alignment():
    from mailquill.sheets_sync import build_readability_requests, _DATA_ROW_PX, _HEADER_ROW_PX
    requests = build_readability_requests(sheet_id=4, num_rows=10, header_rows=[0, 5])
    whole = requests[0]["repeatCell"]
    assert whole["range"] == {"sheetId": 4}  # 整頁
    assert whole["cell"]["userEnteredFormat"] == {"horizontalAlignment": "CENTER",
                                                  "verticalAlignment": "MIDDLE"}
    dims = [r["updateDimensionProperties"] for r in requests if "updateDimensionProperties" in r]
    assert dims[0]["range"] == {"sheetId": 4, "dimension": "ROWS", "startIndex": 0, "endIndex": 10}
    assert dims[0]["properties"]["pixelSize"] == _DATA_ROW_PX
    # 表頭列要排在整段資料列之後，才會蓋過去
    assert [(d["range"]["startIndex"], d["properties"]["pixelSize"]) for d in dims[1:]] == [
        (0, _HEADER_ROW_PX), (5, _HEADER_ROW_PX)]
    assert not any("updateSheetProperties" in r for r in requests)  # 資料分頁保留格線


def test_build_readability_requests_dashboard_hides_gridlines_and_colors_tab():
    from mailquill.sheets_sync import build_readability_requests
    requests = build_readability_requests(sheet_id=4, num_rows=0, dashboard=True)
    assert not any("updateDimensionProperties" in r for r in requests)
    props = next(r for r in requests if "updateSheetProperties" in r)["updateSheetProperties"]
    assert props["properties"]["gridProperties"]["hideGridlines"] is True
    assert "tabColorStyle" in props["properties"]


def test_build_combined_overview_format_requests_highlights_total_column_and_dashes_zero():
    from mailquill.sheets_sync import _AMOUNT_PATTERN_DASH_ZERO, _TOTAL_BG
    blocks = [{"start": 0, "num_months": 2, "num_accounts": 1}]  # 欄：月份/帳戶/合計
    requests = build_combined_overview_format_requests(sheet_id=9, blocks=blocks)
    fmts = [r["repeatCell"] for r in requests if "repeatCell" in r]
    number = next(f for f in fmts if "numberFormat" in f["fields"])
    assert number["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == _AMOUNT_PATTERN_DASH_ZERO
    total = next(f for f in fmts if f["cell"]["userEnteredFormat"].get("backgroundColor") == _TOTAL_BG)
    assert total["range"] == {"sheetId": 9, "startRowIndex": 1, "endRowIndex": 3,
                              "startColumnIndex": 2, "endColumnIndex": 3}


def test_build_goal_format_requests_resets_stale_text_color_on_data_rows():
    # 迴歸：舊版面殘留白字讓「生活開銷」那格整個看不見
    requests = build_goal_format_requests(sheet_id=3)
    reset = next(r["repeatCell"] for r in requests if "repeatCell" in r
                 and r["repeatCell"]["fields"] == "userEnteredFormat.textFormat(bold,foregroundColor)")
    assert reset["range"]["startRowIndex"] == 2 and reset["range"]["startColumnIndex"] == 6
    assert reset["cell"]["userEnteredFormat"]["textFormat"]["foregroundColor"] == {
        "red": 0.0, "green": 0.0, "blue": 0.0}


def test_rows_from_csv_merges_foreign_fee_into_parent(tmp_path):
    from mailquill.sheets_sync import rows_from_csv
    csv_path = str(tmp_path / "t.csv")
    append_transactions(csv_path, [
        _txn("Suica 儲值", date="2026-07-20", amount="3998", l1="Transport"),
        _txn("國外交易手續費 -Suica", date="2026-07-20", amount="60", l1="Other", l2="Fee"),
    ])
    rows = rows_from_csv(csv_path)
    assert rows[1:] == [["2026-07-20", "Suica 儲值（3,998 + 手續費 60）", 4058.0, "TWD",
                         "Transport", "", "Cathay", "1234", "Cathay-1234"]]


def test_table_column_widths_pads_header_for_filter_icon_caps_and_keeps_date_min():
    from mailquill.sheets_sync import (_table_column_widths, _FILTER_ICON_PX, _MAX_COL_PX,
                                       _DATE_COL_PX)
    rows = [["date", "merchant", "currency"],
            ["2026-09-29", "X" * 100, "TWD"]]
    widths = _table_column_widths(rows, 3)
    assert widths[0] >= _DATE_COL_PX
    assert widths[1] == _MAX_COL_PX                    # 極長商家名稱不撐爆
    # 表頭比資料寬時，表頭要多留篩選器圖示的寬度
    assert widths[2] == _text_pixel_width("currency") + _FILTER_ICON_PX


def test_build_format_requests_uses_explicit_column_widths_when_given():
    requests = build_format_requests(sheet_id=5, num_data_rows=0, num_cols=3,
                                     column_widths=[120, 200, 90])
    kinds = [next(iter(r)) for r in requests]
    assert "autoResizeDimensions" not in kinds
    widths = [(r["updateDimensionProperties"]["range"]["startIndex"],
               r["updateDimensionProperties"]["properties"]["pixelSize"])
              for r in requests if "updateDimensionProperties" in r]
    assert widths == [(0, 120), (1, 200), (2, 90)]


class _FailOnceService(FakeService):
    """第一次寫到指定 range 時丟例外，模擬同步中途 API 出錯（配額、逾時…）。"""

    def __init__(self, fail_range, **kw):
        super().__init__(**kw)
        self._fail_range = fail_range
        self._failed = False

    def update(self, spreadsheetId, range, valueInputOption, body):
        if range == self._fail_range and not self._failed:
            self._failed = True
            raise RuntimeError("quota exceeded")
        return super().update(spreadsheetId, range, valueInputOption, body)


def test_sync_sheets_retries_first_time_setup_after_midway_failure(tmp_path):
    # 第一次同步：分頁建好、state 已存檔，之後 API 出錯中斷。重跑時預算骨架與
    # 明細查詢的下拉選單仍要補寫，不能因為分頁「已存在」就永遠跳過。
    import pytest
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", bank="Cathay", last4="1111")])
    service = _FailOnceService(fail_range=f"{BUDGET_TITLE}!A1")

    with pytest.raises(RuntimeError):
        sync_sheets(csv_path, state_path, service, title="mailquill")
    assert not [u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]

    sync_sheets(csv_path, state_path, service, title="mailquill")
    assert len([u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]) == 1
    assert len([u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1"]) == 1

    # 補寫成功後就算完成：第三次不能再重寫（使用者填的預算要留著）
    service.budget_values = [[c, ""] for c in BUDGET_CATEGORIES]
    sync_sheets(csv_path, state_path, service, title="mailquill")
    assert len([u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]) == 1
    assert len([u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1"]) == 1


def test_sync_sheets_legacy_state_without_marker_is_not_rescaffolded(tmp_path):
    # 舊版 state 檔沒有「已完成首次設定」的記錄：已追蹤的分頁一律視為早就設定過，
    # 升級後不能把使用者填的預算洗掉。
    import json
    csv_path = str(tmp_path / "t.csv")
    state_path = str(tmp_path / "sheets_state.json")
    append_transactions(csv_path, [_txn("全聯", date="2026-06-01", bank="Cathay", last4="1111")])
    service = FakeService()
    sync_sheets(csv_path, state_path, service, title="mailquill")
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    state.pop("scaffolded", None)               # 模擬升級前寫的 state
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

    service.budget_values = [[c, "123"] for c in BUDGET_CATEGORIES]
    sync_sheets(csv_path, state_path, service, title="mailquill")
    assert len([u for u in service.updated if u[1] == f"{BUDGET_TITLE}!A3"]) == 1
    assert len([u for u in service.updated if u[1] == f"{DETAIL_TITLE}!A1"]) == 1
