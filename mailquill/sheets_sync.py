"""把 transactions.csv 整份鏡像到一份 Google Sheet，供手機瀏覽。

跟 Firefly III 那種下游帳本不同，Sheet 這裡只是唯讀鏡像、CSV 仍是唯一
真實來源，所以刻意不做逐筆去重/更新追蹤：每次執行都整份「清空再覆寫」
每個分頁，永遠跟 CSV 一致，也不會有「同一筆重複建立」的問題。

分頁配置（由左到右，見 `TAB_ORDER`；舊版英文名稱會原地改名，見 `LEGACY_TITLES`）：
- `總覽`／`預算`／`明細查詢`／`交易` 四個常用分頁，`歸檔` 預設隱藏。
- 帳戶不再各開一個分頁（原本 6 個帳戶分頁內容跟交易分頁完全重複）：交易
  分頁多一欄 `account`（套 config 的 account_aliases），改在明細查詢用帳戶
  下拉選單篩。
- `交易`：`ARCHIVE_CUTOFF` 之後（含）的交易合併總表，手機上滑動範圍不會隨
  歷史資料一直變長。
- `歸檔`：`ARCHIVE_CUTOFF` 之前的所有交易，欄位跟交易分頁一樣；平常用不到，
  預設隱藏。
- `總覽`（舊名 Overview）：單一分頁，裡面依幣別各疊一段「月份 x 帳戶」加總矩陣（中間
  空一列隔開），只含 `ARCHIVE_CUTOFF` 之後的月份，跟交易分頁
  一致；更舊的月份不重複列出（archive 沒有對應的月度總覽）。刻意仍按
  幣別分段、不把數字加在一起——不同幣別不能直接相加，但放同一個分頁方便
  手機上不用切分頁就能都看到。整頁由上往下：「帳單」小表 → 三張圖表 →
  幣別矩陣（見 `_overview_layout`；原本圖表另開 `Charts` 分頁，跟矩陣的
  每月合計重複、還要切分頁，併進來了）。全部單欄往下疊，手機只要上下滑、
  電腦也不用左右找。「帳單」小表（`_bill_status_rows`）：每家銀行一列，合併最新一期應繳總額/
  最低應繳（讀獨立的 `bills.csv`，見 `bills_store.py`）與同步狀態（最新帳單
  月份、明細／每日通知各到哪天、是否落後，見 `status.py`），手機上不用切
  分頁就能看到要繳多少、帳單有沒有都同步進來；沒有任何資料就整段不出現。
  版面見 `build_bill_status_format_requests`，刻意不登記成 `blocks` 的一員，
  也不計入矩陣的欄寬計算。
- `預算`（舊名 Budget）：預算跟「預算 vs 實際」併在同一個分頁（原本分兩個分頁要切來切去
  比對，使用者反應很麻煩），A~E 五欄：分類／月預算／近3月平均／實際／差異。
  第 1 列是月份/幣別說明文字（如「月份：2026-08　幣別：TWD」，見
  `budget_info_row`）、第 2 列是表頭，這兩列每次同步都重寫（純文字/格式，
  不算使用者的資料）。A/B 欄（分類、使用者手動填的月預算）從第 3 列開始，
  只在分頁第一次建立時寫入空白骨架（分類清單 + 空的預算欄），之後每次
  同步都刻意不清空重寫，不然使用者填的數字會被洗掉。C/D/E 欄（近3月平均/
  實際/差異）每次同步都重新算重新寫，對齊 Budget 分頁目前實際的分類列
  順序（使用者可能自己加過列，不假設還是 scaffold 當初的順序）：
  - C 欄「近3月平均」寫的是 Sheets 原生公式（`SUMPRODUCT`/`EOMONTH`/
    `TODAY()`，見 `_budget_reference_formula`），不是 Python 算好的靜態值
    ——公式只認 TWD（Amex 等外幣帳戶不計入，這是使用者確認過的取捨），
    分類條件用字串常值嵌進公式（不是 `$A{row}` 儲存格參照——A 欄顯示的
    分類名稱有 emoji 字首裝飾，見 `_with_emoji`，若比對儲存格內容會因為
    Transactions 分頁的純文字分類欄沒有 emoji 而永遠比不中，這是實際發生
    過的 bug），且會隨 TODAY() 往前推移、隨 `Transactions` 分頁內容改變
    自動重算。
  - D/E 欄（實際、差異＝實際－預算，正值代表超支）是 Python 讀
    `Transactions` 分頁「近期資料裡最新一個月」的實際分類花費算好的數字，
    只挑一種幣別（近期交易筆數最多的那個），跟預算欄位單一數字對應。

  A:E 主表右邊隔一欄（F）並排一個「三大目標」小區塊（G:K，`GOAL_START_COL`，
  表頭跟主表對齊放在第 2 列），是這個使用者個人設定的月收入分配目標（生活
  開銷/主動投資/風險預備，比例寫死在 `GOAL_PERCENTAGES`，不是通用邏輯——見
  `GOAL_CATEGORIES` 附近的說明）。月收入本身刻意不放在這個分頁——使用者
  不想讓收入數字出現在 Sheet 上，改放在本地 `config.yaml` 的
  `goal_monthly_income`（見 config.py），由 `sync_sheets` 的呼叫端傳進來，
  只在同步當下讀一次，不進 git、不進 Sheet。G2:K2 起是表頭＋三個目標分類，
  每次同步都重寫（`write_goal_rows`）：目標金額＝月收入 x 目標%；「生活
  開銷」對得到既有 category_l1 分類（見 `GOAL_CATEGORY_MAPPING`），實際欄
  用近 3 個完整月的月均花費（見 `_goal_actual_formula`，Housing 一樣套
  房租下限）自動算的 Sheets 公式；「主動投資」「風險預備」是儲蓄/投資
  目標不是消費，mailquill 沒有交易資料能直接算，改成用「月收入－生活開銷
  實際」的剩餘，依這兩項目標%的比例（20:15）拆開，一樣是 Sheets 公式，
  會隨生活開銷實際花費自動重算（見 `build_goal_rows`）。
- `明細查詢`（舊名 Month Detail）：B1 月份、D1 帳戶兩個下拉選單（都有
  「全部」），下面 QUERY 公式即時篩出交易分頁的符合列（見
  `month_detail_formula`）——選單一換馬上換內容，不需要重新 sync。選到的值
  是使用者狀態，只在分頁第一次建立時寫入；表頭與公式每次同步都重寫。下拉
  選項清單也每次重寫，新月份/新帳戶才會即時出現。範圍跟交易分頁一致，更
  舊的請到歸檔分頁找。

spreadsheet_id／各分頁的 sheetId 記錄進本地小狀態檔（state_path，
對應 config 的 sheets_state_path）；帳戶清單會隨 CSV 變動（新增卡、
新增月份），所以每次同步都會檢查、把缺少的分頁補上；不再需要的分頁
（如帳戶關掉了、或改版前殘留的 `Overview-<currency>` 舊分頁）會被刪掉，
其餘沿用同一個 sheetId（不重建，保留使用者可能手動調整過的欄寬等）。

每次同步都會先清掉分頁上既有的條件式格式規則（負紅正綠）、banding（表頭
深色底＋資料列斑馬紋）、合併儲存格，再重新加一次——`AddConditionalFormatRuleRequest`
／`AddBandingRequest` 都是「插入」不是「取代」，若不先清掉，規則/banding
會每跑一次同步就多疊一份（banding 甚至會直接報 API 錯誤，因為同一分頁
不能有重疊的 banded range）。這個清除動作特意搶在任何分頁寫值之前，用
獨立的一次 `batchUpdate` 先送出（不是跟其他版面 request 一起排到最後）：
分頁上如果還殘留舊版面留下的合併儲存格，之後 `_write_tab` 寫入的新值會被
合併範圍吃掉、只留合併範圍左上角那一格，等所有分頁都寫完值才清除合併就
已經來不及救回被吃掉的值了。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from mailquill.bill_due import BillDue
from mailquill.bills_store import read_bills
from mailquill.report import _to_float
from mailquill.status import bank_statuses, status_mark
from mailquill.fees import merge_foreign_fees
from mailquill.store import read_transactions

SHEET_TITLE = "交易"
ARCHIVE_TITLE = "歸檔"
OVERVIEW_TITLE = "總覽"
BUDGET_TITLE = "預算"
DETAIL_TITLE = "明細查詢"
# 改名前的英文分頁名稱 -> 新名稱：同步時原地改名（保留 sheetId，預算分頁
# 使用者填的月預算、明細查詢選到的月份才不會因為刪掉重建而消失）。
LEGACY_TITLES = {"Transactions": SHEET_TITLE, "Archive": ARCHIVE_TITLE,
                 "Overview": OVERVIEW_TITLE, "Budget": BUDGET_TITLE,
                 "Month Detail": DETAIL_TITLE}
# 分頁由左到右的順序（最常看的在前）；歸檔平常用不到，預設隱藏。
TAB_ORDER = [OVERVIEW_TITLE, BUDGET_TITLE, DETAIL_TITLE, SHEET_TITLE, ARCHIVE_TITLE]
HIDDEN_TITLES = {ARCHIVE_TITLE}
# 公式裡引用交易分頁：中文分頁名一律加單引號，不依賴 Sheets 對非 ASCII
# 名稱的寬鬆解析。
_TX_REF = f"'{SHEET_TITLE}'"
ALL_OPTION = "全部"
BILL_STATUS_TITLE = "帳單"
HEADER = ["date", "merchant", "amount", "currency", "category", "subcategory", "bank", "last4",
          "account"]
# 明細查詢分頁 QUERY 撈出來的欄位：交易分頁去掉 bank/last4，留帳戶名稱（I 欄）
DETAIL_HEADER = ["date", "merchant", "amount", "currency", "category", "subcategory", "account"]
BUDGET_REF_HEADER = "近3月平均"
BUDGET_REF_MONTHS = 3
# 分類/預算/近3月平均/實際/差異，五欄合一（原本「預算」跟「實際 vs 預算」
# 是分成 Budget／Budget vs Actual 兩個分頁，使用者反應要切分頁比對很麻煩，
# 改成單一分頁：A/B（分類、月預算）使用者手動填、只在第一次建立分頁時
# 寫入；C/D/E（近3月平均、實際、差異）每次同步都重新算重新寫，見
# `budget_scaffold_rows`/`build_budget_reference_and_actual_rows`）。
BUDGET_HEADER = ["分類", "月預算", BUDGET_REF_HEADER, "實際", "差異"]
BUDGET_CATEGORIES = ["Food", "Housing", "Transport", "Education", "Entertainment",
                     "Insurance", "Medical", "Shopping", "Other", "Uncategorized"]
TOTAL_LABEL = "合計"

# 使用者自己設定的三大類別月度目標配置（65/20/15，2026-08-17 定案；歷史：
# 55/5/10/15/15 → 60/30/10，都是 2026-08-16 那天調的），跟 A:E 的逐分類
# 月預算表併排放在同一個 Budget 分頁（隔一欄空隙，見 GOAL_START_COL）。
# 這是這個使用者個人的理財目標，不是通用邏輯——只有「生活開銷」對得到
# 既有 category_l1 分類（見 GOAL_CATEGORY_MAPPING）；「主動投資」「風險
# 預備」是儲蓄/投資目標，不是消費，mailquill 只追蹤消費交易，這兩類量
# 不到「實際」，見 `build_goal_rows` 的說明。若之後要讓其他使用者自訂
# 這組目標，這裡要改成可設定，不是寫死常數。
#
# 月收入（算目標金額的基數）刻意不放在這個分頁——使用者不想讓收入數字
# 出現在 Google Sheet 上，只想看目標金額/實際/差異，改放在本地 config.yaml
# 的 `goal_monthly_income`（見 config.py），只在同步當下讀一次、不進 git、
# 不同步進 Sheet。
GOAL_HEADER = ["目標分類", "目標%", "目標金額", "實際", "差異"]
GOAL_CATEGORIES = ["生活開銷", "主動投資", "風險預備"]
GOAL_PERCENTAGES: dict[str, float] = {
    "生活開銷": 65, "主動投資": 20, "風險預備": 15,
}
GOAL_CATEGORY_MAPPING: dict[str, list[str]] = {
    "生活開銷": ["Food", "Housing", "Transport", "Medical", "Education", "Insurance", "Clothing",
              "Entertainment", "Shopping", "Other"],
}
GOAL_START_COL = 6  # G 欄，跟 A:E 的月預算表隔一欄（F）
GOAL_DEFAULT_INCOME = 100000.0  # config.yaml 沒設定 goal_monthly_income 時的預設基數

# 分類 emoji：純顯示層裝飾，不動 categories.yaml/資料裡實際存的分類字串
# （那些字串還要拿去跟 BUDGET_CATEGORIES、實際花費分類比對，加了 emoji 會
# 配不到）。只在寫進 Sheet 給人看的欄位（Budget 分頁的分類欄、Budget vs
# Actual 分頁的分類欄、Overview 圓餅圖圖例）套用；從 Budget 分頁讀回來的
# 分類文字（`read_budget`/`read_budget_categories`）一律先去掉 emoji 字首
# 還原成純文字，兩邊比對才對得上。
_CATEGORY_EMOJI = {
    "Food": "🍔", "Transport": "🚗", "Shopping": "🛍️", "Entertainment": "🎬",
    "Education": "📚", "Medical": "🏥", "Housing": "🏠",
    "Donation": "❤️", "Insurance": "🛡️", "Other": "📦", "Uncategorized": "❓",
}


def _with_emoji(category: str) -> str:
    """替顯示用的分類名稱加上 emoji 字首；沒有對應 emoji 的分類（使用者自訂
    的新分類、或已經是 `MysteryCategory` 這種未知分類）原樣不動。
    """
    emoji = _CATEGORY_EMOJI.get(category)
    return f"{emoji} {category}" if emoji else category


def _strip_emoji(label: str) -> str:
    """`_with_emoji` 的反向操作，供讀 Budget 分頁時還原成純文字比對用；不管
    讀到的是舊版（純文字）還是新版（有 emoji 字首）都能正確處理。
    """
    prefix, sep, rest = label.partition(" ")
    if sep and prefix in _CATEGORY_EMOJI.values():
        return rest
    return label
CHARTS_TREND_HEADER = ["月份", TOTAL_LABEL]
CHARTS_CATEGORY_HEADER = ["分類", "金額"]

# Overview 圖表的來源表放在最右邊、隱藏起來的欄（K 欄起；矩陣帳戶很多、
# 寬過 K 欄時自動往右挪，見 `_overview_layout`）——畫面上只看得到圖，
# 圖表設定成隱藏資料照畫（`hiddenDimensionStrategy`）。
OVERVIEW_CHART_DATA_COL = 10
CHARTS_TOP_MERCHANTS = 10
CHARTS_MERCHANT_HEADER = ["商家", "金額"]

# 一次性斷點：這之前的交易移到歸檔分頁，交易分頁只留這之後
# 的，避免手機上要滑很久。要調整涵蓋範圍時改這裡即可。
ARCHIVE_CUTOFF = "2026-01-01"

_AMOUNT_COL = HEADER.index("amount")
_NEG_COLOR = {"red": 0.77, "green": 0.11, "blue": 0.11}
_POS_COLOR = {"red": 0.09, "green": 0.46, "blue": 0.09}
_BAD_TAB_CHARS = str.maketrans({c: "_" for c in "[]:*?/\\"})

# 版面配色：表頭深藍底白粗體字，資料列白/淺灰交錯（banding），跟金額正負的
# 紅/綠是兩套不相關的顏色（後者只上在文字上，不影響底色）。
_HEADER_BG = {"red": 0.106, "green": 0.259, "blue": 0.427}
_HEADER_FG = {"red": 1.0, "green": 1.0, "blue": 1.0}
_BAND_FIRST = {"red": 1.0, "green": 1.0, "blue": 1.0}
_BAND_SECOND = {"red": 0.945, "green": 0.945, "blue": 0.945}
_TEXT_DEFAULT = {"red": 0.0, "green": 0.0, "blue": 0.0}
_TOTAL_BG = {"red": 0.91, "green": 0.94, "blue": 0.98}  # 合計欄淡藍底，跟一般斑馬紋區隔
_DASHBOARD_TAB_COLOR = {"red": 0.106, "green": 0.259, "blue": 0.427}  # 跟表頭同色
# 列高（px）：Sheets 預設 21px 字擠在一起、數字不好掃，拉高一點＋垂直置中。
_DATA_ROW_PX = 26
_HEADER_ROW_PX = 32
# 金額 0 顯示成「–」：Overview 矩陣很多帳戶某月沒消費，一整片 0 很吵。
_AMOUNT_PATTERN_DASH_ZERO = '#,##0;-#,##0;"–"'
_CHART_WIDTH_PX = 600
_CHART_HEIGHT_PX = 340
# Overview 每張圖表佔幾列：圖高＋一點間隔，除以資料列高（Overview 列高固定
# 為 `_DATA_ROW_PX`，見 `build_readability_requests`），無條件進位。
_OVERVIEW_CHART_ROWS = -(-(_CHART_HEIGHT_PX + 16) // _DATA_ROW_PX)


@dataclass
class SyncResult:
    spreadsheet_url: str
    rows: int
    accounts: list[str] = field(default_factory=list)
    archived_rows: int = 0
    budget_month: str | None = None
    created: bool = False


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def account_label(bank: str, last4: str, aliases: dict[str, str] | None = None) -> str:
    """帳戶顯示名稱（交易分頁 account 欄、明細查詢帳戶選單）：預設 bank+last4；`aliases`（見 config.yaml 的
    account_aliases，key 為這個預設名稱）可覆寫成好記的顯示名稱，例如
    {"Fubon-6040": "富邦帳戶"}。一律過濾 Sheets 分頁名稱不允許的字元。
    """
    raw = f"{bank}-{last4}" if last4 else bank
    label = (aliases or {}).get(raw, raw)
    return label.translate(_BAD_TAB_CHARS)[:100]


def _is_recent(t) -> bool:
    """沒有日期就當作近期（不歸檔），避免資料異常時被悄悄藏進 Archive。"""
    return not t.date or t.date >= ARCHIVE_CUTOFF


def _accounts(txns, aliases: dict[str, str] | None = None) -> list[str]:
    return sorted({account_label(t.bank, t.account_last4, aliases)
                   for t in txns if _is_recent(t)})


def list_accounts(csv_path: str, aliases: dict[str, str] | None = None) -> list[str]:
    """近期（未歸檔）交易涉及的帳戶清單，給明細查詢的帳戶下拉選單用。"""
    return _accounts(_read_display(csv_path)[0], aliases)


def _currencies(txns) -> list[str]:
    return sorted({t.currency or "TWD" for t in txns})


def list_currencies(csv_path: str) -> list[str]:
    return _currencies(_read_display(csv_path)[0])


def _read_display(csv_path: str) -> tuple[list, dict[str, float]]:
    """讀 CSV 並把國外手續費併進原消費（見 fees.merge_foreign_fees）。回傳
    (顯示用交易, {原消費 txn_id: 併入的手續費})，後者給交易清單在商家名稱
    後拆出「消費 + 手續費」。"""
    merged = merge_foreign_fees(read_transactions(csv_path))
    txns = [m.txn for m in merged]
    fees = {m.txn.txn_id: m.fee for m in merged
            if m.fee and m.fee != _to_float(m.txn.amount)}
    return txns, fees


def _merchant_cell(t, fees: dict[str, float] | None) -> str:
    fee = (fees or {}).get(t.txn_id)
    if not fee:
        return t.merchant_norm
    base = _to_float(t.amount) - fee
    return f"{t.merchant_norm}（{base:,.0f} + 手續費 {fee:,.0f}）"


def _header_rows(txns, recent: bool, fees: dict[str, float] | None = None,
                 aliases: dict[str, str] | None = None) -> list[list]:
    """`rows_from_csv`/`archive_rows_from_csv` 共用的列組裝邏輯，只差
    `recent`（近期 vs `ARCHIVE_CUTOFF` 之前）——欄位、排序都一樣。
    """
    rows = [HEADER]
    sorted_txns = sorted((t for t in txns if _is_recent(t) == recent),
                         key=lambda t: t.date, reverse=True)
    for t in sorted_txns:
        rows.append([t.date, _merchant_cell(t, fees), _to_float(t.amount), t.currency or "TWD",
                     t.category_l1, t.category_l2, t.bank, t.account_last4,
                     account_label(t.bank, t.account_last4, aliases)])
    return rows


def rows_from_csv(csv_path: str, aliases: dict[str, str] | None = None) -> list[list]:
    """讀 CSV 轉成 Transactions 分頁的列（含表頭）：只含近期交易，依日期新到
    舊排序；`ARCHIVE_CUTOFF` 之前的交易見 `archive_rows_from_csv`。
    """
    txns, fees = _read_display(csv_path)
    return _header_rows(txns, recent=True, fees=fees, aliases=aliases)


def archive_rows_from_csv(csv_path: str, aliases: dict[str, str] | None = None) -> list[list]:
    """讀 CSV 轉成 Archive 分頁的列（含表頭）：`ARCHIVE_CUTOFF` 之前的交易，
    欄位跟 Transactions 一樣（含 bank/last4，因為混合多帳戶），依日期新到
    舊排序。
    """
    txns, fees = _read_display(csv_path)
    return _header_rows(txns, recent=False, fees=fees, aliases=aliases)


def _overview_rows(txns, currency: str, aliases: dict[str, str] | None = None) -> list[list]:
    matching = [t for t in txns
               if t.date and _is_recent(t) and (t.currency or "TWD") == currency]
    months = sorted({t.date[:7] for t in matching}, reverse=True)
    accounts = sorted({account_label(t.bank, t.account_last4, aliases) for t in matching})
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for t in matching:
        totals[(t.date[:7], account_label(t.bank, t.account_last4, aliases))] += _to_float(t.amount)

    rows = [["月份"] + accounts + [TOTAL_LABEL]]
    for m in months:
        row = [m]
        month_total = 0.0
        for acc in accounts:
            v = round(totals.get((m, acc), 0.0), 2)
            row.append(v)
            month_total += v
        row.append(round(month_total, 2))
        rows.append(row)
    return rows


def overview_rows_from_csv(csv_path: str, currency: str,
                           aliases: dict[str, str] | None = None) -> list[list]:
    """單一幣別、月份 x 帳戶的加總矩陣（含每月合計欄），依月份新到舊排序。
    刻意限定單一幣別——不同幣別的金額不能直接相加。只含近期（未歸檔）的
    月份，跟交易分頁一致；更舊的月份不重複列出。不含帳戶
    總計列（跨全部月份的每帳戶總額）——使用者反應這列跟右邊的每月合計欄
    重複、多餘，故拿掉；只保留「每月合計」這一欄。
    """
    return _overview_rows(_read_display(csv_path)[0], currency, aliases)


def _combined_overview_rows_and_blocks(
    txns, aliases: dict[str, str] | None = None,
) -> tuple[list[list], list[dict]]:
    currencies = _currencies(txns)
    rows: list[list] = []
    blocks: list[dict] = []
    for i, currency in enumerate(currencies):
        block_rows = _overview_rows(txns, currency, aliases)
        block_rows[0] = [f"月份 ({currency})"] + block_rows[0][1:]
        blocks.append({"start": len(rows), "num_months": len(block_rows) - 1,
                       "num_accounts": len(block_rows[0]) - 2})
        rows.extend(block_rows)
        if i != len(currencies) - 1:
            rows.append([])
    if not rows:
        rows = [["月份", TOTAL_LABEL]]
        blocks = [{"start": 0, "num_months": 0, "num_accounts": 0}]
    return rows, blocks


def combined_overview_rows_and_blocks(
    csv_path: str, aliases: dict[str, str] | None = None,
) -> tuple[list[list], list[dict]]:
    """把每個幣別的加總矩陣依序疊在同一個 Overview 分頁裡（中間空一列），
    回傳 (整份分頁的列, 每段矩陣的位置資訊)。blocks 的 start 是該段表頭列
    在 rows 裡的 0-based 索引，供版面 request 算絕對範圍用。
    """
    return _combined_overview_rows_and_blocks(_read_display(csv_path)[0], aliases)


def _bill_status_rows(txns, bills: list[BillDue], today: str) -> list[list]:
    """疊在 Overview 分頁幣別矩陣最下面的「帳單」小表：每家銀行一列，把原本
    分開的「本期應繳」跟「同步狀態」兩張表合成一張（使用者反應兩張都是
    一銀行一列、帳單月份還重複一欄）。欄位：狀態（同 `mailquill status`，見
    status.py）、帳單月份、最新一期應繳總額／最低應繳（讀 `bills.csv`，沒有
    應繳資料的銀行如 Amex 留空）、月結單明細到哪天、每日通知到哪天。

    欄位順序：應繳總額／最低應繳緊接銀行名稱（平常最常看的是要繳多少），
    同步狀態（狀態／帳單月份）放最後；狀態文字刻意短，避免欄位被撐寬。表頭帶同步日期（這張表只在同步當下算一次）。以交易估計的月份
    標「(估)」而不是另寫註腳列——註腳放 A 欄會撐寬整份 Overview 的 A 欄。
    沒有任何資料就回空清單，呼叫端據此決定要不要疊加這段。
    """
    statuses = bank_statuses(txns, bills, today)
    if not statuses:
        return []
    latest: dict[str, BillDue] = {}
    for b in bills:
        if b.bank not in latest or b.period > latest[b.bank].period:
            latest[b.bank] = b
    rows = [[BILL_STATUS_TITLE, "應繳總額", "最低應繳", "明細至", "每日通知至",
             f"狀態 {today[5:7]}/{today[8:10]}", "帳單月份"]]
    for st in statuses:
        bill = latest.get(st.bank)
        rows.append([
            st.bank,
            _to_float(bill.amount_due) if bill else "",
            _to_float(bill.min_due) if bill and bill.min_due else "",
            st.statement_last_date or "-", st.notice_last_date or "-",
            status_mark(st.months_behind),
            (st.statement_month or "-") + (" (估)" if st.estimated else ""),
        ])
    return rows


def _bill_month_total_row(txns, bills: list[BillDue], today: str) -> list:
    """疊在「帳單」小表最上面的本月應繳合計：只加帳單月份是本月的那幾期
    （上個月的那期已經繳過了），金額對齊下面應繳總額／最低應繳兩欄，第四格
    註明哪幾家還沒算進來——本月帳單未到的列「未到」，明細已到但沒有應繳
    金額資料的（如 Amex）列「沒有金額」，避免把不完整的合計當成全部。"""
    month = today[:7]
    this_month = {b.bank: b for b in bills if b.period == month}
    missing, no_amount = [], []
    for st in bank_statuses(txns, bills, today):
        if st.bank in this_month:
            continue
        if st.months_behind is not None and st.months_behind <= 0:
            no_amount.append(st.bank)
        else:
            missing.append(st.bank)
    notes = []
    if missing:
        notes.append("未到 " + "、".join(missing))
    if no_amount:
        notes.append("、".join(no_amount) + " 沒有金額")
    return [f"{int(month[5:])}月應繳",
            sum(_to_float(b.amount_due) for b in this_month.values()),
            sum(_to_float(b.min_due) for b in this_month.values() if b.min_due),
            "；".join(notes) or "全部到齊"]


def budget_info_row(month: str | None, currency: str) -> list:
    """Budget 分頁第 1 列的月份/幣別說明文字（如「月份：2026-08　幣別：
    TWD」），對應「實際」欄算的是哪個月、哪個幣別；每次同步都重寫（純文字，
    不算使用者填的資料，不受 scaffold-only 保護）。
    """
    return [f"月份：{month or '（無近期資料）'}　幣別：{currency}"]


def budget_scaffold_rows() -> list[list]:
    """Budget 分頁第一次建立時，A3 起的初始資料列：每個分類一列（A欄分類
    名稱+emoji、B欄預算留空讓使用者自己填）。前兩列（月份說明／表頭，見
    `budget_info_row`/`BUDGET_HEADER`）不算 scaffold 的一部分，每次同步都
    會重寫；只有這裡（A3 起）之後的同步不會再碰，使用者填的數字不會被
    洗掉。
    """
    return [[_with_emoji(c), ""] for c in BUDGET_CATEGORIES]


def _primary_currency(txns) -> str:
    counts: dict[str, int] = defaultdict(int)
    for t in txns:
        if _is_recent(t):
            counts[t.currency or "TWD"] += 1
    if not counts:
        return "TWD"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def primary_currency(csv_path: str) -> str:
    """近期交易裡筆數最多的幣別，當作 Budget/Budget vs Actual 預設對應的
    幣別（預算欄只有單一數字，沒有分幣別）。
    """
    return _primary_currency(_read_display(csv_path)[0])


def _latest_recent_month(txns, currency: str) -> str | None:
    months = {t.date[:7] for t in txns
             if _is_recent(t) and t.date and (t.currency or "TWD") == currency}
    return max(months) if months else None


def latest_recent_month(csv_path: str, currency: str) -> str | None:
    """近期（未歸檔）交易裡，指定幣別最新出現的月份；沒有資料回傳 None。"""
    return _latest_recent_month(_read_display(csv_path)[0], currency)


def _current_month_actuals(txns, month: str, currency: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for t in txns:
        if not _is_recent(t) or not t.date or t.date[:7] != month:
            continue
        if (t.currency or "TWD") != currency:
            continue
        totals[t.category_l1 or "Uncategorized"] += _to_float(t.amount)
    return dict(totals)


def current_month_actuals(csv_path: str, month: str, currency: str) -> dict[str, float]:
    """指定月份＋幣別，依第一層分類加總的實際花費（近期資料）。"""
    return _current_month_actuals(_read_display(csv_path)[0], month, currency)


def _monthly_totals(txns, currency: str) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for t in txns:
        if t.date and _is_recent(t) and (t.currency or "TWD") == currency:
            totals[t.date[:7]] += _to_float(t.amount)
    return sorted(((m, round(v, 2)) for m, v in totals.items()), key=lambda kv: kv[0])


def monthly_totals(csv_path: str, currency: str) -> list[tuple[str, float]]:
    """近期（未歸檔）交易，指定幣別，依月份加總，月份由舊到新排序——供
    Charts 分頁的趨勢折線圖使用。Overview 分頁本身刻意新到舊排序方便手機
    捲動，折線圖要照時間順序讀才不會方向顛倒，不能直接沿用那份資料。
    """
    return _monthly_totals(_read_display(csv_path)[0], currency)


def _category_totals(txns, currency: str, month: str | None = None) -> list[tuple[str, float]]:
    totals: dict[str, float] = defaultdict(float)
    for t in txns:
        if not _is_recent(t) or (t.currency or "TWD") != currency:
            continue
        if month and (t.date or "")[:7] != month:
            continue
        totals[t.category_l1 or "Uncategorized"] += _to_float(t.amount)
    positive = [(l1, round(v, 2)) for l1, v in totals.items() if v > 0]
    return sorted(positive, key=lambda kv: kv[1], reverse=True)


def category_totals(csv_path: str, currency: str, month: str | None = None) -> list[tuple[str, float]]:
    """近期（未歸檔）交易，指定幣別，依第一層分類加總（花費為正、退款為負，
    同分類內可互相沖銷），依金額大到小排序，只留淨額為正（真的有花費）的
    分類——圓餅圖不支援負值切片，跟本地 dashboard `donut()` 的邏輯一致
    （`pos = items.filter(i=>i[1]>0)`）。`month`（"YYYY-MM"）給的話只加總該
    月；不給則加總整個近期（未歸檔）視窗。
    """
    return _category_totals(_read_display(csv_path)[0], currency, month)


@dataclass
class ChartsTables:
    """Overview 三張圖表的來源表（都只取主要幣別）。每張表第一列是表頭、
    都只有一個數值欄——使用者反應堆疊長條（月份 x 帳戶／月份 x 分類）顏色
    太多看不懂，改成三張單一 series、一眼看得懂的圖。沒資料時只剩表頭列。"""
    currency: str
    month: str | None            # 分類／商家看的月份（最新月）；沒近期資料為 None
    trend_rows: list[list]       # 每月總花費，月份舊到新
    category_rows: list[list]    # 最新月各第一層分類（淨額為正），金額大到小
    merchant_rows: list[list]    # 最新月前 N 大商家（淨額為正），金額大到小


def _top_merchants_rows(txns, currency: str, month: str | None,
                        limit: int = CHARTS_TOP_MERCHANTS) -> list[list]:
    """指定月份、淨額為正的商家依金額大到小取前 `limit` 名。同商家同月的
    退款會先跟消費沖銷（淨額），跟分類佔比的做法一致。"""
    totals: dict[str, float] = defaultdict(float)
    if month:
        for t in txns:
            if (t.date or "")[:7] == month and _is_recent(t) and (t.currency or "TWD") == currency:
                name = t.merchant_norm or t.merchant_raw or "(無商家名稱)"
                totals[name] += _to_float(t.amount)
    ranked = sorted(((n, round(v, 2)) for n, v in totals.items() if v > 0),
                    key=lambda kv: kv[1], reverse=True)[:limit]
    return [list(CHARTS_MERCHANT_HEADER)] + [[n, v] for n, v in ranked]


def _charts_page_tables(txns, aliases: dict[str, str] | None = None) -> ChartsTables:
    """Overview 三張圖表來源表的內容。跟幣別矩陣不同，這裡寫的是
    Python 算好的靜態值而不是 Sheets 公式——Sheet 本來就是唯讀鏡像，資料只
    會經由 `sync-sheets` 改變，每次同步整張重算就夠了；月份 x 分類、前 N 大
    商家這種二維/排名表用公式寫會又長又慢。"""
    currency = _primary_currency(txns)
    month = _latest_recent_month(txns, currency)
    return ChartsTables(
        currency=currency, month=month,
        trend_rows=[list(CHARTS_TREND_HEADER)] + [[m, v] for m, v in _monthly_totals(txns, currency)],
        category_rows=[list(CHARTS_CATEGORY_HEADER)] + [
            [_with_emoji(c), v] for c, v in _category_totals(txns, currency, month)] if month
        else [list(CHARTS_CATEGORY_HEADER)],
        merchant_rows=_top_merchants_rows(txns, currency, month),
    )


def _charts_table_starts(tables: ChartsTables) -> tuple[int, int, int]:
    """三張表各自的表頭列（0-based，相對來源區第一列）：依序往下疊、中間空
    一列。寫值（`_chart_data_rows`）跟圖表來源範圍
    （`build_overview_chart_requests`）共用這份算法，位置才對得上。"""
    trend = 0
    cat = trend + len(tables.trend_rows) + 1
    mer = cat + len(tables.category_rows) + 1
    return trend, cat, mer


def _chart_data_rows(tables: ChartsTables) -> list[list]:
    """三張來源表往下疊（中間空一列），不含左邊補位；`_overview_layout` 會把
    它貼到隱藏欄。"""
    rows: list[list] = []
    for i, table in enumerate([tables.trend_rows, tables.category_rows, tables.merchant_rows]):
        if i:
            rows.append([])
        rows.extend(list(r) for r in table)
    return rows


def _charts_to_draw(tables: ChartsTables) -> list[tuple[int, list[list], str, str]]:
    """要畫的圖表 (來源表頭列, 來源表, 標題, 圖型)，依畫面由上到下的順序；
    沒有資料列的表不畫（空來源會報 API 錯誤），也不佔版面。"""
    trend_r, cat_r, mer_r = _charts_table_starts(tables)
    month, cur = tables.month or "", tables.currency
    specs = [
        (trend_r, tables.trend_rows, f"每月總花費 ({cur})", "COLUMN"),
        (cat_r, tables.category_rows, f"{month} 各分類花費 ({cur})", "BAR"),
        (mer_r, tables.merchant_rows, f"{month} 前 {CHARTS_TOP_MERCHANTS} 大商家 ({cur})", "BAR"),
    ]
    return [spec for spec in specs if len(spec[1]) > 1]


@dataclass
class OverviewLayout:
    """Overview 整頁的格子與各區塊位置（0-based 列/欄）。由上往下：本月應繳
    合計列 + 帳單小表（有資料才出現，後面空一列）→ 圖表區（每張 `_OVERVIEW_CHART_ROWS` 列的
    空白列，圖表浮在上面）→ 幣別矩陣。圖表來源表貼在 `data_col` 起的隱藏欄。"""
    rows: list[list]
    blocks: list[dict]           # 幣別矩陣，start 已是整頁的絕對列
    bill_total_row: int | None   # 本月應繳合計列；沒有帳單資料為 None
    bill_start: int | None       # 帳單表頭列；沒有帳單資料為 None
    num_bill_rows: int           # 帳單資料列數（不含表頭）
    chart_row: int               # 第一張圖表的錨點列
    data_col: int                # 圖表來源表起始欄（隱藏）
    tables: ChartsTables


def _overview_layout(txns, bills: list[BillDue], today: str,
                     aliases: dict[str, str] | None = None) -> OverviewLayout:
    bill_rows = _bill_status_rows(txns, bills, today)
    if bill_rows:
        bill_rows = [_bill_month_total_row(txns, bills, today)] + bill_rows
    tables = _charts_page_tables(txns, aliases)
    matrix_rows, blocks = _combined_overview_rows_and_blocks(txns, aliases)

    top = bill_rows + [[]] if bill_rows else []
    chart_row = len(top)
    matrix_start = chart_row + len(_charts_to_draw(tables)) * _OVERVIEW_CHART_ROWS
    rows = top + [[] for _ in range(matrix_start - chart_row)] + matrix_rows
    blocks = [{**b, "start": b["start"] + matrix_start} for b in blocks]

    data_col = max([OVERVIEW_CHART_DATA_COL] + [len(r) + 1 for r in rows])
    for i, extra in enumerate(_chart_data_rows(tables)):
        if not extra:
            continue
        while len(rows) <= i:
            rows.append([])
        rows[i] = list(rows[i]) + [""] * (data_col - len(rows[i])) + extra
    return OverviewLayout(rows=rows, blocks=blocks,
                          bill_total_row=0 if bill_rows else None,
                          bill_start=1 if bill_rows else None,
                          num_bill_rows=max(len(bill_rows) - 2, 0),
                          chart_row=chart_row, data_col=data_col, tables=tables)


def read_budget(service, spreadsheet_id: str) -> dict[str, float]:
    """讀現有 Budget 分頁的數字（分類 -> 月預算）。分頁還沒填、或某一列
    打了非數字，該筆直接跳過，不擋同步。分類資料列從第 3 列開始（前兩列是
    月份說明/表頭，見 `budget_info_row`/`BUDGET_HEADER`）。
    """
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!A3:B").execute()
    budget: dict[str, float] = {}
    for row in resp.get("values", []):
        if len(row) < 2:
            continue
        try:
            budget[_strip_emoji(row[0])] = float(row[1])
        except (ValueError, TypeError):
            continue
    return budget


def read_budget_categories(service, spreadsheet_id: str) -> list[str]:
    """讀 Budget 分頁目前每一列的分類名稱（依現有列順序，從第 3 列開始，見
    `read_budget` 的說明）——近 N 月平均/實際/差異要對齊寫回同一列，不能
    假設還是當初 scaffold 的 `BUDGET_CATEGORIES` 順序（使用者可能自己加了
    列）。
    """
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!A3:A").execute()
    return [_strip_emoji(row[0]) for row in resp.get("values", []) if row]


_BUDGET_REF_ROW_LIMIT = 20000  # 涵蓋範圍遠超實際資料量，SUMPRODUCT 只是留夠 headroom

# 房租是固定金額（目前 38,000/月），但有時候會用沒被 mailquill 追蹤解析的
# 帳戶繳，導致那個月的 Housing 交易金額少算。「近3月平均」對 Housing 這個
# 分類改成逐月取「該月 Housing 實際金額」跟這個下限的較大值再平均，確保
# 平均不會因為某個月房租沒被抓到而被拉低；已經抓到的月份（金額本來就
# ≥ 下限）不受影響，不會被重複加總。這是使用者確認過的取捨，只套用在
# Housing 這一個分類，其他分類仍用原本单純的視窗總額/N。
_HOUSING_CATEGORY = "Housing"
_HOUSING_RENT_FLOOR = 38000


def _quoted_category(category: str) -> str:
    """把分類名稱轉成 Sheets 公式裡的字串常值（雙引號用 `""` 跳脫）。"""
    return category.replace('"', '""')


def _budget_reference_month_sum(month_offset: int) -> str:
    """單一日曆月（`month_offset`＝0 本月、1 上個月…）的 Housing/TWD 加總，
    用 `EOMONTH` 算月份邊界；跟 `_budget_reference_formula` 一樣用
    `SUMPRODUCT` 陣列比較（原因見該函式說明：`SUMIFS` 條件字串對純文字
    日期欄的 `>=`/`<=` 永遠回傳 0）。

    分類條件用字串常值 `"Housing"` 而不是 `$A{row}` 儲存格參照——Budget 分頁
    A 欄顯示的分類名稱有 emoji 字首裝飾（見 `_with_emoji`），`Transactions`
    分頁的分類欄沒有；若直接比對 `$A{row}` 儲存格內容，會拿「🏠 Housing」
    去跟純文字「Housing」比對，永遠比不中，算出來的近 3 月平均會直接變 0
    （這是實際發生過的 bug：加了 emoji 之後 Budget vs Actual 的近 3 月平均
    全部歸零，只有 Housing 因為有 `MAX(...,38000)` 房租下限撐著才看起來正常，
    其實底層加總也一樣是 0）。改用字串常值後不再需要對應到儲存格列號，
    `row` 參數就跟著拿掉了。
    """
    r = _BUDGET_REF_ROW_LIMIT
    start = f'TEXT(EOMONTH(TODAY(),{-month_offset - 1})+1,"yyyy-mm-dd")'
    end = f'TEXT(EOMONTH(TODAY(),{-month_offset}),"yyyy-mm-dd")'
    return (f'SUMPRODUCT(({_TX_REF}!$E$2:$E${r}="{_quoted_category(_HOUSING_CATEGORY)}")*'
            f'({_TX_REF}!$D$2:$D${r}="TWD")*'
            f'({_TX_REF}!$A$2:$A${r}>={start})*'
            f'({_TX_REF}!$A$2:$A${r}<={end})*'
            f'{_TX_REF}!$C$2:$C${r})')


def _budget_reference_formula(category: str) -> str:
    """近 N 個「已完整結束」月份的平均（不含本月）的 Sheets 原生公式（不是
    Python 算好塞值）：加總 `Transactions` 分頁裡 `category`（字串常值，不是
    `$A{row}` 儲存格參照——原因見 `_budget_reference_month_sum` 的說明：
    Budget 分頁 A 欄的分類名稱有 emoji 字首，跟 `Transactions` 分頁的純文字
    分類欄比對不中）、TWD 幣別的金額，除以 N。刻意排除本月：本月才過一半的話，總額
    本來就會偏低，混進 3 個月平均會把平均拉低、失真；且國泰「每日消費彙整
    通知」對非特約商店不會帶店名（要等月結單才補上），本月的資料常常有一
    大塊還是空店名、卡在 Uncategorized，這部分也還沒真的分類完成。改成看
    上個月、上上個月、上上上月這種「已經走完整月、月結單也大機率已經
    寄到」的窗口，數字才穩定、可比較。刻意只認 TWD（跟既有的 Budget vs
    Actual 一樣單一幣別假設，Amex 等外幣帳戶不計入——這是使用者確認過的
    取捨），也只查 `Transactions` 分頁：窗口目前必定落在 `ARCHIVE_CUTOFF`
    之後，跟 Python 版 `_is_recent` 的篩選範圍一致。公式會隨 TODAY() 往前
    推移、隨 Transactions 分頁內容改變自動重算，不用每次同步都重寫數字。

    `category` 是 `Housing` 時走另一條逐月取 `MAX(該月實際, 房租下限)` 的
    公式（見 `_budget_reference_month_sum`，offset 從 1 開始＝排除本月），
    其餘分類維持單一視窗 `SUMPRODUCT` 總額除以 N。分類條件（不論哪個分支）
    都用字串常值嵌進公式，不是儲存格參照，所以不需要知道自己在 Budget 分頁
    第幾列。

    用 `SUMPRODUCT` 陣列比較，不是 `SUMIFS` 的條件字串（如 `">="&日期`）：
    實測過 `SUMIFS` 對「純文字」欄位（`Transactions!A:A` 是 RAW 寫入的日期
    字串，不是真正的日期型別）套用 `>=` 這種比較運算子的條件字串永遠回傳
    0（即使字串本身按字典序比較是對的），但同一個比較直接寫在儲存格公式
    裡（如 `A2>="2026-05-15"`）或用陣列運算（`SUMPRODUCT`）都正常——這是
    Sheets `SUMIFS`/`COUNTIFS` 條件字串比較運算子的已知限制，不是我們資料
    格式錯。
    """
    if category == _HOUSING_CATEGORY:
        parts = [f'MAX({_budget_reference_month_sum(i)},{_HOUSING_RENT_FLOOR})'
                for i in range(1, BUDGET_REF_MONTHS + 1)]
        return f'=({"+".join(parts)})/{BUDGET_REF_MONTHS}'
    r = _BUDGET_REF_ROW_LIMIT
    lower = f'TEXT(EOMONTH(TODAY(),{-BUDGET_REF_MONTHS - 1})+1,"yyyy-mm-dd")'
    upper = 'TEXT(EOMONTH(TODAY(),-1),"yyyy-mm-dd")'
    return (f'=SUMPRODUCT(({_TX_REF}!$E$2:$E${r}="{_quoted_category(category)}")*'
            f'({_TX_REF}!$D$2:$D${r}="TWD")*'
            f'({_TX_REF}!$A$2:$A${r}>={lower})*'
            f'({_TX_REF}!$A$2:$A${r}<={upper})*'
            f'{_TX_REF}!$C$2:$C${r})/{BUDGET_REF_MONTHS}')


def _budget_actual_formula(category: str) -> str:
    """本月至今（`LEFT(日期,7)` 對比 `TEXT(TODAY(),"yyyy-mm")`，不是資料裡
    「最新有資料的月」）某分類的 TWD 加總，Sheets 原生公式——資料一有新增
    就自動反映在這格，不用重新跑 `sync-sheets`（跟 C 欄「近3月平均」一樣
    是公式，不是 Python 算好塞值）。分類條件用字串常值，原因見
    `_budget_reference_month_sum` 的說明（A 欄顯示文字有 emoji 字首，跟
    `Transactions` 純文字分類欄比對不中）。
    """
    r = _BUDGET_REF_ROW_LIMIT
    return (f'=SUMPRODUCT(({_TX_REF}!$E$2:$E${r}="{_quoted_category(category)}")*'
            f'({_TX_REF}!$D$2:$D${r}="TWD")*'
            f'(LEFT({_TX_REF}!$A$2:$A${r},7)=TEXT(TODAY(),"yyyy-mm"))*'
            f'{_TX_REF}!$C$2:$C${r})')


def build_budget_reference_and_actual_rows(categories: list[str], start_row: int = 3) -> list[list]:
    """C:E 欄（近3月平均、實際、差異）每一列的內容，對齊 `categories`（Budget
    分頁目前實際的分類列順序，見 `read_budget_categories`；使用者可能自己
    加過列，不假設還是當初 scaffold 的 `BUDGET_CATEGORIES` 順序）。三欄都是
    Sheets 原生公式字串，不是 Python 算好塞值——差異＝實際－預算，用同一
    列的儲存格參照（`=D{row}-B{row}`），會隨 B 欄（使用者填的預算）跟 D 欄
    一起即時重算，不用等下次 `sync-sheets`。`start_row`（1-indexed）是第一
    個分類資料列在 Budget 分頁的實際列號（目前固定第 3 列，參數化方便
    測試）。

    跟已經併掉的舊版 `build_budget_vs_actual_rows` 不同，這裡不會把「額外」
    分類另外附加在後面——分類列表跟 A/B 欄綁在一起（一列對應一個分類，B 欄
    的預算數字也在那一列），沒有對應 A/B 列的分類沒地方接，只能等使用者
    自己在 Budget 分頁加一列。`BUDGET_CATEGORIES` 已經涵蓋 `categorizer`
    目前所有的第一層分類，實務上不太會出現真的接不到的情況。
    """
    rows = []
    for i, cat in enumerate(categories):
        row = start_row + i
        rows.append([_budget_reference_formula(cat), _budget_actual_formula(cat), f'=D{row}-B{row}'])
    return rows


def write_budget_reference_and_actual_columns(service, spreadsheet_id: str,
                                              categories: list[str]) -> None:
    """把 C:E 欄（近3月平均、實際、差異）寫進 Budget 分頁，只動這三欄，不碰
    使用者填的 A/B 欄；每次同步都重寫同一套公式模板（不是重新計算過的
    數字，見 `build_budget_reference_and_actual_rows` 的說明）。
    """
    rows = build_budget_reference_and_actual_rows(categories)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!C3",
        valueInputOption="USER_ENTERED", body={"values": rows}).execute()


def _goal_actual_month_sum(l1_categories: list[str], month_offset: int) -> str:
    """單一日曆月（`month_offset`＝1 上個月…）裡，一組 category_l1 的 TWD
    加總；`l1_categories` 裡有 Housing 的話，那一項套房租下限（跟
    `_budget_reference_month_sum` 同一套 `MAX(...,38000)` 邏輯）。給
    `_goal_actual_formula` 組合成跨月/跨分類的完整公式用。
    """
    r = _BUDGET_REF_ROW_LIMIT
    start = f'TEXT(EOMONTH(TODAY(),{-month_offset - 1})+1,"yyyy-mm-dd")'
    end = f'TEXT(EOMONTH(TODAY(),{-month_offset}),"yyyy-mm-dd")'
    date_cond = (f'({_TX_REF}!$D$2:$D${r}="TWD")*'
                f'({_TX_REF}!$A$2:$A${r}>={start})*'
                f'({_TX_REF}!$A$2:$A${r}<={end})')
    terms = []
    for cat in l1_categories:
        term = (f'SUMPRODUCT(({_TX_REF}!$E$2:$E${r}="{_quoted_category(cat)}")*'
               f'{date_cond}*{_TX_REF}!$C$2:$C${r})')
        if cat == _HOUSING_CATEGORY:
            term = f'MAX({term},{_HOUSING_RENT_FLOOR})'
        terms.append(term)
    return "+".join(terms)


def _goal_actual_formula(l1_categories: list[str]) -> str:
    """五大目標裡一個對得到 category_l1 的目標分類（見
    `GOAL_CATEGORY_MAPPING`），近 3 個「已完整結束」日曆月（不含本月，
    理由跟 `_budget_reference_formula` 一樣）的月均 TWD 花費，Sheets 原生
    公式——資料一變就自動重算，不用重新跑 `sync-sheets`。同一個目標分類
    常常對到好幾個 category_l1（如「生活開銷」= 7 個分類），公式會相對長，
    但這是機器產生的，不是要給人手動維護。
    """
    parts = [_goal_actual_month_sum(l1_categories, i) for i in range(1, BUDGET_REF_MONTHS + 1)]
    return f'=({"+".join(f"({p})" for p in parts)})/{BUDGET_REF_MONTHS}'


def build_goal_rows(income: float, start_row: int = 2) -> list[list]:
    """三大目標區塊（`GOAL_HEADER` 佔第一列，資料列從 `start_row + 1` 開始）
    的內容：目標分類/目標%/目標金額/實際/差異。目標金額＝月收入 x 目標%
    ——Python 算好的靜態數字，因為月收入本身在本地 config.yaml，不是 Sheet
    上能被公式參照的儲存格（見 `GOAL_CATEGORIES` 附近的說明）。「生活開銷」
    實際欄是 Sheets 原生公式（見 `_goal_actual_formula`），對得到既有
    category_l1（見 `GOAL_CATEGORY_MAPPING`）。「主動投資」「風險預備」是
    儲蓄/投資目標，不是消費，mailquill 沒有交易資料能算——但使用者要的是
    「收入扣掉生活開銷實際花費後，還剩多少可以存」，不是要求 mailquill
    去解析轉帳/申購信件；剩餘（=月收入-生活開銷實際）依這兩項的目標%比例
    （20:15）拆開，一樣是 Sheets 公式，生活開銷的實際花費一變就自動重算。
    差異欄一律用同一列的儲存格參照（`=實際欄{row}-目標金額欄{row}`）。
    """
    actual_col = _col_letter(GOAL_START_COL + GOAL_HEADER.index("實際"))
    target_col = _col_letter(GOAL_START_COL + GOAL_HEADER.index("目標金額"))
    mapped_actual_cells = [f'{actual_col}{start_row + 1 + i}'
                           for i, cat in enumerate(GOAL_CATEGORIES)
                           if GOAL_CATEGORY_MAPPING.get(cat) is not None]
    residual = f'({income}-({"+".join(mapped_actual_cells)}))'
    unmapped_total_pct = sum(GOAL_PERCENTAGES[cat] for cat in GOAL_CATEGORIES
                             if GOAL_CATEGORY_MAPPING.get(cat) is None)
    rows = []
    for i, cat in enumerate(GOAL_CATEGORIES):
        row = start_row + 1 + i  # +1：GOAL_HEADER 佔掉第一列
        pct = GOAL_PERCENTAGES[cat]
        target = round(income * pct / 100, 2)
        l1_categories = GOAL_CATEGORY_MAPPING.get(cat)
        if l1_categories is None:
            actual = f'={residual}*{pct}/{unmapped_total_pct}'
        else:
            actual = _goal_actual_formula(l1_categories)
        rows.append([cat, pct / 100, target, actual, f'={actual_col}{row}-{target_col}{row}'])
    return rows


def write_goal_rows(service, spreadsheet_id: str, income: float) -> None:
    """把五大目標區塊的表頭＋資料列（G2:K7）寫進 Budget 分頁，每次同步都
    重寫——全部是 Sheets 公式或 Python 算好的靜態數字，沒有使用者手動填的
    內容（月收入本身在本地 config.yaml，不進這個分頁，見 `GOAL_CATEGORIES`
    附近的說明）。表頭跟主表表頭（第 2 列）對齊，兩張表並排看起來才整齊。
    """
    rows = [GOAL_HEADER] + build_goal_rows(income)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!{_col_letter(GOAL_START_COL)}2",
        valueInputOption="USER_ENTERED", body={"values": rows}).execute()


def build_goal_format_requests(sheet_id: int) -> list[dict]:
    """五大目標區塊的版面 requests：表頭底色（G2:K2，對齊 A:E 主表表頭）、
    目標%欄（H）百分比格式、目標金額/實際/差異欄（I:K）千分位數字格式、
    差異欄超支(>0)標紅／省下(<0)標綠（跟 A:E 主表差異欄同一套紅綠邏輯）。
    純函式方便測試。
    """
    c0, c1 = GOAL_START_COL, GOAL_START_COL + len(GOAL_HEADER)
    header_row = 1  # 0-indexed；G2 是第 2 列，對齊 A:E 主表表頭
    end_row = header_row + 1 + len(GOAL_CATEGORIES)
    requests: list[dict] = [
        _header_style_request(sheet_id, c0, c1, header_row, header_row + 1),
        # 資料列先 reset 回預設黑字：Budget 分頁不像 Overview 每次整頁清格式，
        # 舊版面殘留的白字曾讓「生活開銷」那格整個看不見（實際發生過）。
        {"repeatCell": {
            "range": _range(sheet_id, header_row + 1, end_row, c0, c1),
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": False, "foregroundColor": _TEXT_DEFAULT}}},
            "fields": "userEnteredFormat.textFormat(bold,foregroundColor)"}},
        _banding_request(sheet_id, header_row, end_row, c0, c1),
        *(_column_width_request(sheet_id, c0 + a, c0 + b, px) for a, b, px in _GOAL_COL_WIDTHS),
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": header_row + 1, "endRowIndex": end_row,
                     "startColumnIndex": c0 + 1, "endColumnIndex": c0 + 2},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": header_row + 1, "endRowIndex": end_row,
                     "startColumnIndex": c0 + 2, "endColumnIndex": c1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
    ]
    diff_col = c0 + GOAL_HEADER.index("差異")
    diff_range = {"sheetId": sheet_id, "startRowIndex": header_row + 1, "endRowIndex": end_row,
                 "startColumnIndex": diff_col, "endColumnIndex": diff_col + 1}
    for op, color in [("NUMBER_GREATER", _NEG_COLOR), ("NUMBER_LESS", _POS_COLOR)]:
        requests.append({"addConditionalFormatRule": {
            "rule": {"ranges": [diff_range],
                     "booleanRule": {"condition": {"type": op, "values": [{"userEnteredValue": "0"}]},
                                     "format": {"textFormat": {"foregroundColor": color}}}},
            "index": 0}})
    return requests


def _detail_default_month(txns) -> str:
    """明細查詢分頁 B1 月份選單第一次建立時的預設值：近期資料裡最新出現的
    月份（不分幣別）；沒有近期資料就預設「全部」。
    """
    months = {t.date[:7] for t in txns if _is_recent(t) and t.date}
    return max(months) if months else ALL_OPTION


def _detail_months(txns) -> list[str]:
    """月份下拉選單的候選清單：「全部」＋近期資料裡出現過的月份（新到舊）；
    每次同步都重新算，讓清單隨資料增減自動更新。"""
    return [ALL_OPTION] + sorted({t.date[:7] for t in txns if _is_recent(t) and t.date},
                                 reverse=True)


def month_detail_formula() -> str:
    """明細查詢的 QUERY：依 B1（月份）、D1（帳戶）篩交易分頁，選「全部」就
    不加該條件；新到舊排序。帳戶比對的是交易分頁 I 欄（`account_label`，
    已套 config 的 account_aliases），所以多張卡共用同一個別名會一起列出。

    B1 先正規化成「yyyy-mm」文字再比對：使用者在 Sheets 上從選單點「2026-08」
    時，Sheets 會把長得像日期的值自動轉成日期序號（純文字格式擋不住——實測
    選取後格式會被清掉），不轉回來的話 QUERY 永遠篩不到。
    """
    month = f'IF(ISNUMBER($B$1),TEXT($B$1,"yyyy-mm"),$B$1)'
    return (f'=IFERROR(QUERY({_TX_REF}!A2:I,'
            f'"select A,B,C,D,E,F,I where A is not null"'
            f'&IF({month}="{ALL_OPTION}",""," and A like \'"&{month}&"-%\'")'
            f'&IF($D$1="{ALL_OPTION}",""," and I = \'"&$D$1&"\'")'
            f'&" order by A desc",0),"")')


def write_month_detail_selectors(service, spreadsheet_id: str, default_month: str) -> None:
    """明細查詢第 1 列：「選月份 [B1]　選帳戶 [D1]」。B1/D1 選到什麼屬於
    使用者互動狀態，只在分頁第一次建立（或從舊版 Month Detail 改名過來、
    還沒有帳戶選單）時寫入，之後同步不再碰，選的值才不會被洗回預設。

    刻意用 `RAW`：`default_month`（如 "2026-08"）長得像日期，`USER_ENTERED`
    會把它解析成日期序號，跟公式裡 `$B$1&"-%"` 期待的純文字對不上。
    """
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{DETAIL_TITLE}!A1",
        valueInputOption="RAW",
        body={"values": [["選月份", default_month, "選帳戶", ALL_OPTION]]}).execute()


def write_month_detail_query(service, spreadsheet_id: str) -> None:
    """表頭（第 3 列）與 QUERY 公式（A4）每次同步都重寫：都不是使用者
    狀態，而且欄位/篩選條件改版時，已建立過的分頁才會跟上（表頭曾經卡在
    舊名稱，是實際發生過的 bug）。公式用 `USER_ENTERED` 才會被解析成公式。
    """
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{DETAIL_TITLE}!A3",
        valueInputOption="RAW", body={"values": [DETAIL_HEADER]}).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{DETAIL_TITLE}!A4",
        valueInputOption="USER_ENTERED", body={"values": [[month_detail_formula()]]}).execute()


def _dropdown_requests(sheet_id: int, col: int, options: list[str]) -> list[dict]:
    """第 1 列某一格的下拉選單：純文字格式（`@`）＋ `ONE_OF_LIST` 固定清單
    （`ONE_OF_RANGE` 指向陣列公式展開的範圍時，選單選值仍會被判違反驗證，
    是已知的 Sheets 限制）。

    刻意 `strict: False`：從選單點「2026-08」這種長得像日期的值時，Sheets
    仍可能自動轉成日期序號（實測選取後純文字格式會被清掉），嚴格驗證會把
    轉換後的值當成「不在清單內」直接拒絕寫入，使用者看到的就是選了沒反應
    （實際發生過）。寬鬆驗證讓值一定寫得進去，日期序號再交給
    `month_detail_formula` 轉回 yyyy-mm。"""
    cell = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
            "startColumnIndex": col, "endColumnIndex": col + 1}
    return [
        {"repeatCell": {
            "range": cell,
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT", "pattern": "@"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"setDataValidation": {
            "range": cell,
            "rule": {"condition": {
                "type": "ONE_OF_LIST",
                "values": [{"userEnteredValue": o} for o in options]},
                     "showCustomUi": True, "strict": False}}},
    ]


def build_month_detail_format_requests(sheet_id: int, months: list[str], accounts: list[str],
                                       column_widths: list[int] | None = None) -> list[dict]:
    """明細查詢分頁版面：凍結上面三列（選單/空列/表頭）、表頭深色底白字、
    B1（月份）與 D1（帳戶）下拉選單、金額欄數字格式、欄寬。下拉選單的候選
    清單每次同步都重寫，新月份/新帳戶才會即時出現。QUERY 展開的列數會變，
    故意不加 banding（需要固定範圍）。"""
    return [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 3}},
            "fields": "gridProperties.frozenRowCount"}},
        _bold(sheet_id, 0, 1, 0, 1),
        _bold(sheet_id, 0, 1, 2, 3),
        _header_style_request(sheet_id, 0, len(DETAIL_HEADER), r0=2, r1=3),
        *_dropdown_requests(sheet_id, 1, months),
        *_dropdown_requests(sheet_id, 3, [ALL_OPTION] + accounts),
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 1000,
                     "startColumnIndex": _AMOUNT_COL, "endColumnIndex": _AMOUNT_COL + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        *_column_width_requests(sheet_id, len(DETAIL_HEADER), column_widths),
    ]


def _header_style_request(sheet_id: int, c0: int, c1: int, r0: int = 0, r1: int = 1) -> dict:
    """表頭列：深藍底、白色粗體字、垂直置中——跟純粹 `_bold` 的差別是多上底色。"""
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                 "startColumnIndex": c0, "endColumnIndex": c1},
        "cell": {"userEnteredFormat": {
            "backgroundColor": _HEADER_BG,
            "textFormat": {"bold": True, "foregroundColor": _HEADER_FG},
            "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}}


def _banding_request(sheet_id: int, r0: int, r1: int, c0: int, c1: int,
                     footer: bool = False) -> dict:
    """表頭深色底＋資料列白/淺灰交錯的 banding；`footer=True` 時最後一列（如
    合計列）也套表頭色，跟斑馬紋的交錯色區隔開來。
    """
    row_props = {"headerColor": _HEADER_BG,
                "firstBandColor": _BAND_FIRST, "secondBandColor": _BAND_SECOND}
    if footer:
        row_props["footerColor"] = _HEADER_BG
    return {"addBanding": {
        "bandedRange": {
            "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                     "startColumnIndex": c0, "endColumnIndex": c1},
            "rowProperties": row_props}}}


def _foreign_currency_blocks(currencies: list[str]) -> list[tuple[int, int]]:
    """把 currencies（每列一個，"TWD" 或空字串代表台幣）裡連續非台幣的區段
    合併成 (start, end) 區間（0-based，相對資料列，不含表頭），減少要開的
    repeatCell 數量——同一分頁裡外幣列通常連續，不用逐列各開一個 request。
    """
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for i, cur in enumerate(currencies):
        is_foreign = (cur or "TWD") != "TWD"
        if is_foreign and start is None:
            start = i
        elif not is_foreign and start is not None:
            blocks.append((start, i))
            start = None
    if start is not None:
        blocks.append((start, len(currencies)))
    return blocks


def build_format_requests(sheet_id: int, num_data_rows: int, num_cols: int = len(HEADER),
                          currencies: list[str] | None = None,
                          column_widths: list[int] | None = None) -> list[dict]:
    """整理版面用的 batchUpdate requests：凍結表頭、深色底白字表頭、資料列
    banding 斑馬紋、金額欄位數字格式、篩選器、自動欄寬，資料列存在時再加
    負紅正綠的條件式格式（純函式方便測試，不含任何 API 呼叫）。用於
    交易與歸檔分頁。

    金額欄預設整欄套台幣格式（無小數）；`currencies`（每列一個幣別，順序
    對齊資料列）給的話，非台幣的列會疊一層固定兩位小數的格式蓋過去——刻意
    用「先整欄鋪台幣、再疊外幣區段」而不是逐列各自判斷，因為多數列都是
    台幣，這樣只需要為數不多的外幣區段多開幾個 request。

    `column_widths`（呼叫端用 `_table_column_widths` 依實際內容算好）給的話，
    每欄明確設定寬度（含左右留白）；沒給才退回 autoResize＋日期欄固定寬。
    """
    end_row = 1 + max(num_data_rows, 0)
    requests: list[dict] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        _header_style_request(sheet_id, 0, num_cols),
        _banding_request(sheet_id, 0, end_row, 0, num_cols),
        *_column_width_requests(sheet_id, num_cols, column_widths),
    ]
    if num_data_rows <= 0:
        return requests

    amount_range = {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
                    "startColumnIndex": _AMOUNT_COL, "endColumnIndex": _AMOUNT_COL + 1}
    requests.append({"repeatCell": {
        "range": amount_range,
        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        "fields": "userEnteredFormat.numberFormat"}})
    if currencies:
        for start, end in _foreign_currency_blocks(currencies):
            requests.append({"repeatCell": {
                "range": {**amount_range, "startRowIndex": 1 + start, "endRowIndex": 1 + end},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
                "fields": "userEnteredFormat.numberFormat"}})
    requests.append({"setBasicFilter": {
        "filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": end_row,
                             "startColumnIndex": 0, "endColumnIndex": num_cols}}}})
    # 正數＝消費（絕大多數列），維持預設黑字；只有負數（退款/回饋）標綠。
    # 原本正綠負紅，整頁幾乎全綠，顏色反而失去提示作用。
    requests.append({"addConditionalFormatRule": {
        "rule": {"ranges": [amount_range],
                 "booleanRule": {"condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
                                 "format": {"textFormat": {"foregroundColor": _POS_COLOR}}}},
        "index": 0}})
    return requests


def _bold(sheet_id: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
                 "startColumnIndex": c0, "endColumnIndex": c1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
        "fields": "userEnteredFormat.textFormat.bold"}}


# (起欄, 迄欄(不含), px)：Budget 主表 A:E、三大目標 G:K（相對 GOAL_START_COL）
# 日期欄（YYYY-MM-DD）autoResize 只抓剛好塞得下的寬度，整頁置中後左右
# 沒留白、看起來很擠，固定給寬一點。
_DATE_COL_PX = 110
_BUDGET_COL_WIDTHS = [(0, 1, 160), (1, 5, 100)]
_GOAL_COL_WIDTHS = [(0, 1, 110), (1, 2, 70), (2, 5, 100)]


_FILTER_ICON_PX = 24   # 表頭篩選器圖示佔掉的寬度
_MAX_COL_PX = 320      # 單欄上限：極長的商家名稱不要把整欄撐爆（超出部分照樣截斷顯示）


def _table_column_widths(rows: list[list], num_cols: int) -> list[int]:
    """逐筆資料分頁（交易/歸檔/明細查詢）每欄的像素寬度：依實際
    內容算（`_column_pixel_widths` 已含左右留白），表頭另加篩選器圖示的
    寬度，上限 `_MAX_COL_PX`，日期欄至少 `_DATE_COL_PX`。autoResize 只抓
    剛好塞得下的寬度，整頁置中後左右沒留白，看起來很擠。"""
    data = _column_pixel_widths(rows[1:], num_cols)
    header = rows[0] if rows else []
    widths = []
    for i in range(num_cols):
        head = _text_pixel_width(str(header[i])) + _FILTER_ICON_PX if i < len(header) else 0
        widths.append(min(max(data[i], head), _MAX_COL_PX))
    if widths:
        widths[0] = max(widths[0], _DATE_COL_PX)
    return widths


def _column_width_requests(sheet_id: int, num_cols: int,
                           widths: list[int] | None) -> list[dict]:
    """有算好的欄寬就逐欄明確設定；沒有就 autoResize，日期欄再補固定寬
    （排在 autoResize 之後才蓋得過）。"""
    if widths:
        return [_column_width_request(sheet_id, i, i + 1, w)
                for i, w in enumerate(widths[:num_cols]) if w]
    return [{"autoResizeDimensions": {
                "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS",
                               "startIndex": 0, "endIndex": num_cols}}},
            _column_width_request(sheet_id, 0, 1, _DATE_COL_PX)]


def _column_width_request(sheet_id: int, c0: int, c1: int, px: int) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": c0, "endIndex": c1},
        "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def build_readability_requests(sheet_id: int, num_rows: int, header_rows: list[int] = (0,),
                               dashboard: bool = False) -> list[dict]:
    """全分頁共用的可讀性設定：整頁水平＋垂直置中（使用者要求所有格子一律
    置中，數字也是）、資料列拉高到 `_DATA_ROW_PX`、
    表頭列（`header_rows`，0-based）拉高到 `_HEADER_ROW_PX`。`dashboard=True`
    （Overview/Budget/Month Detail 這類看報表的分頁）另外隱藏格線、分頁
    標籤上色，跟逐筆資料分頁區隔開來。純函式方便測試。"""
    requests: list[dict] = [{"repeatCell": {
        "range": {"sheetId": sheet_id},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                       "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment)"}}]
    if num_rows > 0:
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": num_rows},
            "properties": {"pixelSize": _DATA_ROW_PX}, "fields": "pixelSize"}})
        for r in header_rows:
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": r, "endIndex": r + 1},
                "properties": {"pixelSize": _HEADER_ROW_PX}, "fields": "pixelSize"}})
    if dashboard:
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"hideGridlines": True},
                           "tabColorStyle": {"rgbColor": _DASHBOARD_TAB_COLOR}},
            "fields": "gridProperties.hideGridlines,tabColorStyle"}})
    return requests


def _text_pixel_width(text: str) -> int:
    """粗略估算文字寬度（px）：CJK/emoji 這種全形字元比 ASCII 半形字元寬，
    `autoResizeDimensions` 對這件事常常估得不夠準（表頭被截斷的教訓——「月份
    (TWD)」結尾括號被切掉、帳戶別名「富邦JCB卡」被切掉、分類佔比表的
    emoji+英文分類名稱如「🎬 Entertainment」被切掉，都是同一個根因），欄寬
    需要保證裝得下這類文字時改自己算、明確設定，不只靠 autoResize 猜。
    """
    wide = sum(1 for ch in text if ord(ch) > 0x2E80)
    narrow = len(text) - wide
    return wide * 15 + narrow * 8 + 24  # 抓寬一點的 padding


def _column_pixel_widths(rows: list[list], num_cols: int) -> list[int]:
    """算 `rows` 每一欄目前需要多寬（px，見 `_text_pixel_width`），掃過所有
    列、不只表頭——資料本身比表頭寬的情況（如分類佔比表的分類欄，emoji+
    英文分類名稱比「分類」兩個字寬）也要顧到。只看字串儲存格：數字欄（如
    金額）也要算進來（依 `#,##0` 顯示的字串）——這裡算出的寬度會「明確
    設定」、蓋過 autoResize，只看短表頭（如「富邦」）的話大金額會被擠成
    ###（實際發生過）。0 代表這欄沒有需要保證的寬度，維持給
    autoResizeDimensions 自己猜就好。
    """
    widths = [0] * num_cols
    for row in rows:
        for i, cell in enumerate(row[:num_cols]):
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                cell = f"{cell:,.0f}"  # 跟金額欄 #,##0 顯示格式同寬
            if not isinstance(cell, str) or not cell:
                continue
            widths[i] = max(widths[i], _text_pixel_width(cell))
    return widths


def build_combined_overview_format_requests(sheet_id: int, blocks: list[dict],
                                            column_widths: list[int] | None = None) -> list[dict]:
    """Overview 分頁（多幣別疊在同一分頁）的版面 requests：每段各自的表頭/
    月份欄粗體、金額欄數字格式、負紅正綠；欄寬預設用所有段落裡最寬的一段
    給 `autoResizeDimensions` 猜（純函式方便測試時不用準備真的列內容）。
    `column_widths`（呼叫端如 `sync_sheets` 有實際列內容時，用
    `_column_pixel_widths` 算出來傳進來）會針對每一欄明確設定像素寬度、蓋過
    autoResize 的猜測——月份/帳戶別名這類全形字混英數的表頭，autoResize 常常
    估得不夠準（見 `_text_pixel_width` 的說明）。分頁裡不同段落的欄數可能
    不同，凍結列/欄在多段疊放時沒有單一段落能對整份分頁有意義，所以不設
    frozenRowCount（純函式方便測試）。
    """
    max_cols = max((b["num_accounts"] + 2 for b in blocks), default=2)
    requests: list[dict] = [
        {"autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS",
                           "startIndex": 0, "endIndex": max_cols}}},
    ]
    for i in range(max_cols):
        width = column_widths[i] if column_widths and i < len(column_widths) else 0
        if width:
            requests.append({"updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                         "startIndex": i, "endIndex": i + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize"}})
    for b in blocks:
        start, num_months, num_accounts = b["start"], b["num_months"], b["num_accounts"]
        num_cols = num_accounts + 2
        header_row = start
        requests.append(_header_style_request(sheet_id, 0, num_cols, header_row, header_row + 1))
        if num_months <= 0 or num_accounts <= 0:
            continue
        end_row = header_row + num_months + 1  # 資料列結束（不含）；不含帳戶合計列
        requests.append(_banding_request(sheet_id, header_row, end_row, 0, num_cols))
        # 這個矩陣的列會隨月份增加往下位移，同一個列位置在不同次同步可能對到
        # 不同月份；下面的 numberFormat repeatCell 的 fields 只含 numberFormat，
        # 不會動到文字色/粗體，任何一列如果曾經意外套過其他格式（如粗體、白字，
        # 曾經發生過：改版留下的舊格式卡住某一列，數字看起來像消失了），不會
        # 被後續同步自動蓋掉。每次同步先整段 reset 回預設（不粗體、黑字），
        # 下面的月份欄粗體/正紅負綠再依序疊上去。
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": header_row + 1,
                     "endRowIndex": end_row, "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": False, "foregroundColor": _TEXT_DEFAULT}}},
            "fields": "userEnteredFormat.textFormat(bold,foregroundColor)"}})
        requests.append(_bold(sheet_id, header_row, end_row, 0, 1))
        value_range = {"sheetId": sheet_id, "startRowIndex": header_row + 1,
                       "endRowIndex": end_row, "startColumnIndex": 1, "endColumnIndex": num_cols}
        requests.append({"repeatCell": {
            "range": value_range,
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER",
                                                            "pattern": _AMOUNT_PATTERN_DASH_ZERO}}},
            "fields": "userEnteredFormat.numberFormat"}})
        # 矩陣裡全是消費金額，不再上正綠負紅（整片綠色只是雜訊）；改成最右邊
        # 合計欄粗體＋淡藍底，一眼找到每月總額。
        total_col = num_cols - 1
        requests.append({"repeatCell": {
            "range": _range(sheet_id, header_row + 1, end_row, total_col, total_col + 1),
            "cell": {"userEnteredFormat": {"backgroundColor": _TOTAL_BG,
                                           "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}})
    return requests


_STATUS_COLORS = {
    "✅": {"red": 0.85, "green": 0.94, "blue": 0.85},
    "⚠️": {"red": 1.0, "green": 0.95, "blue": 0.8},
    "❌": {"red": 0.98, "green": 0.85, "blue": 0.85},
}
_BILL_STATUS_COLS = 7
# 帳單小表欄位位置（見 `_bill_status_rows`）：應繳金額兩欄緊接銀行名稱，
# 同步狀態（狀態／帳單月份）放最後面——平常要看的是「要繳多少」。
_BILL_AMOUNT_COLS = (1, 3)
_BILL_STATUS_COL = 5


def build_bill_status_format_requests(sheet_id: int, start_row: int, num_data_rows: int) -> list[dict]:
    """Overview「帳單」小表的版面：表頭深色底白字、banding、應繳金額兩欄千分位，
    跟上面幣別矩陣同一套視覺語言；狀態欄依 ✅／⚠️／❌ 上淺綠／淺黃／淺紅底
    （條件式格式，每次同步由 `_clear_stale_decorations_requests` 清掉重加）。
    `start_row` 是表頭列的 0-based 列索引；`num_data_rows` 為 0 時回空清單。"""
    if num_data_rows <= 0:
        return []
    end_row = start_row + 1 + num_data_rows
    requests = [
        _header_style_request(sheet_id, 0, _BILL_STATUS_COLS, start_row, start_row + 1),
        _banding_request(sheet_id, start_row, end_row, 0, _BILL_STATUS_COLS),
        {"repeatCell": {
            "range": _range(sheet_id, start_row + 1, end_row, *_BILL_AMOUNT_COLS),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
    ]
    status_range = _range(sheet_id, start_row + 1, end_row, _BILL_STATUS_COL, _BILL_STATUS_COL + 1)
    for mark, color in _STATUS_COLORS.items():
        requests.append({"addConditionalFormatRule": {
            "rule": {"ranges": [status_range],
                     "booleanRule": {"condition": {"type": "TEXT_CONTAINS",
                                                   "values": [{"userEnteredValue": mark}]},
                                     "format": {"backgroundColor": color}}},
            "index": 0}})
    return requests


def build_bill_total_format_requests(sheet_id: int, row: int) -> list[dict]:
    """本月應繳合計列（見 `_bill_month_total_row`）：整列粗體、金額千分位，
    第四格的備註合併到表尾（D:G），長備註不會撐寬「明細至」那一欄。"""
    return [
        _bold(sheet_id, row, row + 1, 0, _BILL_STATUS_COLS),
        {"repeatCell": {
            "range": _range(sheet_id, row, row + 1, *_BILL_AMOUNT_COLS),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}},
        {"mergeCells": {"range": _range(sheet_id, row, row + 1, _BILL_AMOUNT_COLS[1],
                                        _BILL_STATUS_COLS),
                        "mergeType": "MERGE_ALL"}},
    ]


def build_budget_format_requests(sheet_id: int, num_data_rows: int = len(BUDGET_CATEGORIES)) -> list[dict]:
    """Budget 分頁的版面 requests：第 1 列是月份/幣別說明（見 `budget_info_row`，
    每次同步都重寫但只是文字說明，不是使用者填的資料）、第 2 列是表頭
    （`BUDGET_HEADER`）、第 3 列起每個分類一列。表頭底色/banding 每次同步
    都重新套用；`num_data_rows` 用現有分類列數（使用者可能自己加過列，不是
    每次都等於 `BUDGET_CATEGORIES` 的數量）。差異欄（E）超支（>0）標紅、
    省下（<0）標綠——這裡的紅/綠是看「差異」的正負，跟其他分頁看「金額」
    正負剛好相反，因為差異為正代表超支、是壞消息；A/B 欄（分類/預算）是
    使用者自己填的原始輸入，不套這個。
    """
    num_cols = len(BUDGET_HEADER)
    header_row = 1
    end_row = header_row + 1 + max(num_data_rows, 0)
    requests: list[dict] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 2}},
            "fields": "gridProperties.frozenRowCount"}},
        # 第 1 列說明文字跨 A:E 合併：整頁置中時，只放 A1 的長字串會往兩邊溢出、
        # 左半邊被切掉（「月份」只剩半個字）。合併儲存格每次同步會先被
        # `_clear_stale_decorations_requests` 解除，這裡重新合併。
        {"mergeCells": {"range": _range(sheet_id, 0, 1, 0, num_cols), "mergeType": "MERGE_ALL"}},
        _bold(sheet_id, 0, 1, 0, 1),
        _header_style_request(sheet_id, 0, num_cols, header_row, header_row + 1),
    ]
    # 欄寬明確設定、不靠 autoResize：C:E 是公式，autoResize 當下常常估不到
    # 算出來的數字寬度，表頭「近3月平均」也會被截成「近3月平」（實際發生過）。
    for c0, c1, px in _BUDGET_COL_WIDTHS:
        requests.append(_column_width_request(sheet_id, c0, c1, px))
    if num_data_rows <= 0:
        return requests

    requests.append(_banding_request(sheet_id, header_row, end_row, 0, num_cols))
    # B 欄（月預算）是使用者手填的數字，一樣要千分位（原本漏掉，「14447」
    # 跟旁邊「27,278」格式不一致）；numberFormat 只改顯示、不動使用者的值。
    number_range = {"sheetId": sheet_id, "startRowIndex": header_row + 1, "endRowIndex": end_row,
                    "startColumnIndex": 1, "endColumnIndex": num_cols}
    requests.append({"repeatCell": {
        "range": number_range,
        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        "fields": "userEnteredFormat.numberFormat"}})
    diff_col = BUDGET_HEADER.index("差異")
    diff_range = {"sheetId": sheet_id, "startRowIndex": header_row + 1, "endRowIndex": end_row,
                 "startColumnIndex": diff_col, "endColumnIndex": diff_col + 1}
    for op, color in [("NUMBER_GREATER", _NEG_COLOR), ("NUMBER_LESS", _POS_COLOR)]:
        requests.append({"addConditionalFormatRule": {
            "rule": {"ranges": [diff_range],
                     "booleanRule": {"condition": {"type": op, "values": [{"userEnteredValue": "0"}]},
                                     "format": {"textFormat": {"foregroundColor": color}}}},
            "index": 0}})
    return requests


_CHART_BLUE = {"red": 0.26, "green": 0.52, "blue": 0.96}
_CHART_GRAY = {"red": 0.74, "green": 0.76, "blue": 0.79}


def _range(sheet_id: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1,
            "startColumnIndex": c0, "endColumnIndex": c1}


def _basic_chart_request(sheet_id: int, title: str, chart_type: str, domain: dict,
                         series: list[dict], anchor_row: int, anchor_col: int,
                         stacked: bool = False, legend: str = "BOTTOM_LEGEND",
                         axis_titles: tuple[str, str] = ("", ""),
                         colors: list[dict] | None = None, data_labels: bool = False,
                         width: int = 480, height: int = 300, show_hidden: bool = False) -> dict:
    """一張 basicChart（COLUMN/BAR）的 addChart request；`domain`/`series` 都是
    `_range` 產生的來源範圍，第一列視為表頭（headerCount=1）。`colors` 依序
    對應每個 series；`data_labels` 在柱子上直接標數值（格式沿用來源格）。"""
    # 橫條圖（BAR）的數值軸在下方，API 規定 series 只能指到 BOTTOM_AXIS；
    # 直條圖（COLUMN）數值軸在左邊。
    value_axis = "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS"
    spec: dict = {
        "chartType": chart_type,
        "legendPosition": legend,
        "axis": [{"position": "BOTTOM_AXIS", "title": axis_titles[0]},
                 {"position": "LEFT_AXIS", "title": axis_titles[1]}],
        "domains": [{"domain": {"sourceRange": {"sources": [domain]}}}],
        "series": [{"series": {"sourceRange": {"sources": [src]}}, "targetAxis": value_axis}
                   for src in series],
        "headerCount": 1,
    }
    for i, s in enumerate(spec["series"]):
        if colors and i < len(colors):
            s["colorStyle"] = {"rgbColor": colors[i]}
        if data_labels:
            s["dataLabel"] = {"type": "DATA", "placement": "OUTSIDE_END"}
    if stacked:
        spec["stackedType"] = "STACKED"
    chart_spec: dict = {"title": title, "basicChart": spec}
    if show_hidden:  # 來源欄被隱藏時照樣畫（預設會略過隱藏的列/欄）
        chart_spec["hiddenDimensionStrategy"] = "SHOW_ALL"
    return {"addChart": {"chart": {
        "spec": chart_spec,
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sheet_id, "rowIndex": anchor_row, "columnIndex": anchor_col},
            "widthPixels": width, "heightPixels": height}},
    }}}


def build_overview_chart_requests(sheet_id: int, layout: OverviewLayout) -> list[dict]:
    """Overview 圖表區：三張浮動圖表（每月總花費直條、最新月各分類花費橫條、
    最新月前 N 大商家橫條），單一 series、柱子上直接標金額、不需要圖例，
    從 `layout.chart_row` 起在 A 欄往下疊、每張 `_OVERVIEW_CHART_ROWS` 列。
    來源表所在的欄整段隱藏，金額欄仍套千分位（資料標籤沿用來源格格式）。
    純函式方便測試。"""
    c0, tables = layout.data_col, layout.tables
    requests: list[dict] = [{"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                  "startIndex": c0, "endIndex": c0 + 2},
        "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}}]
    for i, (r, table, title, chart_type) in enumerate(_charts_to_draw(tables)):
        n = len(table) - 1
        requests.append({"repeatCell": {
            "range": _range(sheet_id, r + 1, r + n + 1, c0 + 1, c0 + 2),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}})
        requests.append(_basic_chart_request(
            sheet_id, title, chart_type,
            domain=_range(sheet_id, r, r + n + 1, c0, c0 + 1),
            series=[_range(sheet_id, r, r + n + 1, c0 + 1, c0 + 2)],
            anchor_row=layout.chart_row + i * _OVERVIEW_CHART_ROWS, anchor_col=0,
            legend="NO_LEGEND", colors=[_CHART_BLUE], data_labels=True,
            width=_CHART_WIDTH_PX, height=_CHART_HEIGHT_PX, show_hidden=True))
    return requests


def build_budget_chart_requests(sheet_id: int, num_categories: int) -> list[dict]:
    """Budget 分頁「預算 vs 實際」橫條圖：直接以 A:D 主表為來源（表頭在第 2
    列，分類列從第 3 列起 `num_categories` 列），只畫兩個 series＝月預算（灰）
    ／實際（藍）——原本還有「近3月平均」第三根，使用者反應三根柱子看不懂，
    拿掉（數字仍在 C 欄）。錨在三大目標區塊右邊隔一欄、對齊表頭列。
    沒有分類列就不畫。純函式方便測試。"""
    if num_categories <= 0:
        return []
    header_row = 1
    r1 = header_row + 1 + num_categories
    return [_basic_chart_request(
        sheet_id, "本月 預算 vs 實際", "BAR",
        domain=_range(sheet_id, header_row, r1, 0, 1),
        series=[_range(sheet_id, header_row, r1, c, c + 1) for c in (1, 3)],
        anchor_row=header_row, anchor_col=GOAL_START_COL + len(GOAL_HEADER) + 1,
        colors=[_CHART_GRAY, _CHART_BLUE], width=560, height=380)]


def _reset_format_requests(sheet_ids: list[int]) -> list[dict]:
    """整頁清掉格式、欄寬回預設 100px。只給每次同步都整頁重寫、之後會重新套
    版面的分頁（Overview）用：`_write_tab` 只清值不清格式（凍結列也一併歸零），改版面後
    舊位置的格式（例如移除的圖表來源表表頭深色底、舊欄寬）會一直殘留。
    Budget 等有使用者手填資料的分頁不能用。"""
    requests: list[dict] = []
    for sid in sheet_ids:
        requests.append({"updateCells": {"range": {"sheetId": sid}, "fields": "userEnteredFormat"}})
        # 舊版面留下的凍結列（Overview 曾停在第 2 列，把 TWD 矩陣切成兩半）
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 0}},
            "fields": "gridProperties.frozenRowCount"}})
        # 欄寬回預設、隱藏欄（圖表來源表）也先取消隱藏，版面改了不會殘留
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0},
            "properties": {"pixelSize": 100, "hiddenByUser": False},
            "fields": "pixelSize,hiddenByUser"}})
    return requests


def _clear_stale_decorations_requests(service, spreadsheet_id: str, sheet_ids: list[int]) -> list[dict]:
    """先清掉這些分頁上既有的條件式格式規則、banding、合併儲存格、圖表物件，
    避免每次同步都疊加一份重複（`AddConditionalFormatRuleRequest`／
    `AddBandingRequest`／`AddChartRequest` 都是插入，不是取代既有的）。
    banding 甚至若不清掉會直接報「重疊的 banded range」API 錯誤，不是單純
    疊加而已。合併儲存格也要清——不清的話，改版面版型（例如欄位排法從並排
    改回疊放）之後舊的合併範圍還在，新資料寫進去會跟舊的合併邊界對不上、
    整個跑版。圖表不清掉的話 Overview 會每同步一次就多長出一組折線圖/
    圓餅圖疊在原本那組上面。
    """
    if not sheet_ids:
        return []
    resp = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties.sheetId,conditionalFormats,bandedRanges,merges,charts.chartId)"
    ).execute()
    requests: list[dict] = []
    for sheet in resp.get("sheets", []):
        sid = sheet["properties"]["sheetId"]
        if sid not in sheet_ids:
            continue
        n = len(sheet.get("conditionalFormats") or [])
        requests.extend({"deleteConditionalFormatRule": {"sheetId": sid, "index": 0}} for _ in range(n))
        for band in sheet.get("bandedRanges") or []:
            requests.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})
        if sheet.get("merges"):
            requests.append({"unmergeCells": {"range": {"sheetId": sid}}})
        for chart in sheet.get("charts") or []:
            requests.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
    return requests


def _prune_stale_tabs(service, spreadsheet_id: str, state: dict, state_path: str,
                      wanted_titles: list[str]) -> None:
    """刪掉狀態檔裡記得、但這次不再需要的分頁（帳戶關掉了、幣別分表改版前
    殘留的舊版 `Overview` 等），避免留著顯示過期或算錯的數字。"""
    sheet_ids = state.get("sheet_ids", {})
    stale = [t for t in sheet_ids if t not in wanted_titles]
    if not stale:
        return
    requests = [{"deleteSheet": {"sheetId": sheet_ids[t]}} for t in stale]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    for t in stale:
        del sheet_ids[t]
    state["sheet_ids"] = sheet_ids
    save_state(state_path, state)


def _rename_legacy_tabs(service, spreadsheet_id: str, state: dict,
                        state_path: str) -> set[str]:
    """把狀態檔記得的舊英文分頁（`LEGACY_TITLES`）原地改成中文名稱，回傳
    這次改名後的新標題集合。改名而不是刪掉重建：sheetId 不變，預算分頁
    使用者填的月預算、手動調過的欄寬都保留。新名稱已存在就不動（避免撞名）。"""
    sheet_ids = dict(state.get("sheet_ids") or {})
    renames = {old: new for old, new in LEGACY_TITLES.items()
               if old in sheet_ids and new not in sheet_ids}
    if not renames:
        return set()
    requests = [{"updateSheetProperties": {
        "properties": {"sheetId": sheet_ids[old], "title": new}, "fields": "title"}}
        for old, new in renames.items()]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    for old, new in renames.items():
        sheet_ids[new] = sheet_ids.pop(old)
    state["sheet_ids"] = sheet_ids
    save_state(state_path, state)
    return set(renames.values())


def build_tab_order_requests(sheet_ids: dict[str, int]) -> list[dict]:
    """分頁依 `TAB_ORDER` 由左到右排好，`HIDDEN_TITLES`（歸檔）隱藏、其餘顯示。"""
    return [{"updateSheetProperties": {
        "properties": {"sheetId": sheet_ids[t], "index": i, "hidden": t in HIDDEN_TITLES},
        "fields": "index,hidden"}}
        for i, t in enumerate(TAB_ORDER) if t in sheet_ids]


def _ensure_tabs(service, spreadsheet_id: str, state: dict, state_path: str,
                 wanted_titles: list[str]) -> tuple[dict, set[str]]:
    """確保 wanted_titles 裡的每個分頁都存在，回傳 ({title: sheetId}, 這次
    真的用 addSheet 新建出來的分頁標題集合)——後者用來判斷 Budget 這種
    「只在真的第一次建立時寫入骨架」的分頁要不要動它。缺的先查試算表現況
    （這樣使用者手動建立、但我們還沒追蹤過的分頁不會被誤判成新建），還缺
    才用 addSheet 補上，結果存回 state。
    """
    sheet_ids = state.get("sheet_ids")
    if sheet_ids is None and state.get("sheet_id") is not None:
        sheet_ids = {SHEET_TITLE: state.pop("sheet_id")}
    sheet_ids = dict(sheet_ids or {})

    missing = [t for t in wanted_titles if t not in sheet_ids]
    if missing:
        resp = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)").execute()
        existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                   for s in resp.get("sheets", [])}
        for t in missing:
            if t in existing:
                sheet_ids[t] = existing[t]

    still_missing = [t for t in wanted_titles if t not in sheet_ids]
    # 「首次設定」（預算骨架、明細查詢下拉選單）要等真的寫成功才算完成，不能用
    # 「分頁剛建立」判斷：建分頁後 state 立刻存檔，後面 API 一出錯，重跑時分頁
    # 已存在，首次設定就永遠被跳過。所以另外記 scaffolded；沒有這個欄位的舊版
    # state，已追蹤／已存在的分頁視為早就設定過（不能把使用者填的預算洗掉）。
    if "scaffolded" not in state:
        state["scaffolded"] = sorted(sheet_ids)
    scaffolded = set(state["scaffolded"])
    newly_created = set(still_missing) | {t for t in wanted_titles if t not in scaffolded}
    if still_missing:
        add_requests = [{"addSheet": {"properties": {"title": t}}} for t in still_missing]
        resp = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": add_requests}).execute()
        for reply in resp.get("replies", []):
            props = reply["addSheet"]["properties"]
            sheet_ids[props["title"]] = props["sheetId"]

    state["sheet_ids"] = sheet_ids
    save_state(state_path, state)
    return sheet_ids, newly_created


def _mark_scaffolded(state: dict, state_path: str, title: str) -> None:
    done = set(state.get("scaffolded", ()))
    if title not in done:
        done.add(title)
        state["scaffolded"] = sorted(done)
        save_state(state_path, state)


def _write_tab(service, spreadsheet_id: str, title: str, rows: list[list]) -> None:
    sheet = service.spreadsheets()
    sheet.values().clear(spreadsheetId=spreadsheet_id, range=title).execute()
    sheet.values().update(
        spreadsheetId=spreadsheet_id, range=f"{title}!A1",
        valueInputOption="RAW", body={"values": rows},
    ).execute()


def _col_letter(idx: int) -> str:
    """0-based 欄位索引轉 A1 欄位字母（0->A, 25->Z, 26->AA, ...）。"""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def sync_sheets(csv_path: str, state_path: str, service, title: str = "mailquill",
                aliases: dict[str, str] | None = None,
                goal_monthly_income: float = GOAL_DEFAULT_INCOME,
                bills_path: str = "bills.csv", today: str | None = None) -> SyncResult:
    state = load_state(state_path)
    spreadsheet_id = state.get("spreadsheet_id")
    created = False
    if not spreadsheet_id:
        body = {"properties": {"title": title},
                "sheets": [{"properties": {"title": SHEET_TITLE}}]}
        resp = service.spreadsheets().create(
            body=body,
            fields="spreadsheetId,spreadsheetUrl,sheets.properties(sheetId,title)").execute()
        spreadsheet_id = resp["spreadsheetId"]
        state["spreadsheet_id"] = spreadsheet_id
        state["spreadsheet_url"] = resp["spreadsheetUrl"]
        state["sheet_ids"] = {SHEET_TITLE: resp["sheets"][0]["properties"]["sheetId"]}
        save_state(state_path, state)
        created = True

    # 整份同步只讀一次 CSV，後面全部吃這份 in-memory txns（不再逐個 helper
    # 各自重讀重解析一次整份 CSV）。
    txns, fees = _read_display(csv_path)
    # 帳戶不再各開分頁（改在明細查詢用下拉選單篩），清單只給選單與回報用。
    accounts = _accounts(txns, aliases)
    wanted_titles = list(TAB_ORDER)

    renamed = _rename_legacy_tabs(service, spreadsheet_id, state, state_path)
    _prune_stale_tabs(service, spreadsheet_id, state, state_path, wanted_titles)
    sheet_ids, newly_created = _ensure_tabs(service, spreadsheet_id, state, state_path, wanted_titles)

    decorated_sheet_ids = [sheet_ids[SHEET_TITLE], sheet_ids[ARCHIVE_TITLE],
                           sheet_ids[OVERVIEW_TITLE], sheet_ids[BUDGET_TITLE]]
    # 清舊裝飾（含合併儲存格）要搶在任何 _write_tab 寫值之前執行：分頁上如果
    # 還殘留舊版面的合併儲存格，這次要寫的新值會被合併範圍吃掉/對不上欄位
    # （只留合併範圍左上角那格的值），等所有分頁都寫完才清就已經來不及了。
    pre_requests = (_reset_format_requests([sheet_ids[OVERVIEW_TITLE]])
                    + _clear_stale_decorations_requests(service, spreadsheet_id, decorated_sheet_ids))
    if pre_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": pre_requests}).execute()

    requests: list[dict] = []
    main_rows: list[list] = []
    archive_rows: list[list] = []
    main_widths = None
    for title, recent in ((SHEET_TITLE, True), (ARCHIVE_TITLE, False)):
        rows = _header_rows(txns, recent=recent, fees=fees, aliases=aliases)
        _write_tab(service, spreadsheet_id, title, rows)
        widths = _table_column_widths(rows, len(HEADER))
        requests += build_format_requests(sheet_ids[title], len(rows) - 1,
                                          currencies=[r[_AMOUNT_COL + 1] for r in rows[1:]],
                                          column_widths=widths)
        requests += build_readability_requests(sheet_ids[title], len(rows))
        if recent:
            main_rows, main_widths = rows, widths
        else:
            archive_rows = rows

    # Overview：帳單 → 圖表 → 幣別矩陣，單欄往下疊（見 `_overview_layout`）。
    layout = _overview_layout(txns, read_bills(bills_path),
                              today or datetime.now().strftime("%Y-%m-%d"), aliases)
    currency = layout.tables.currency
    _write_tab(service, spreadsheet_id, OVERVIEW_TITLE, layout.rows)
    overview_max_cols = max([b["num_accounts"] + 2 for b in layout.blocks]
                            + ([_BILL_STATUS_COLS] if layout.bill_start is not None else []))
    # 合計列的備註格合併到表尾，不拿來算欄寬
    width_rows = [r[:_BILL_AMOUNT_COLS[1]] if i == layout.bill_total_row else r
                  for i, r in enumerate(layout.rows)]
    requests += build_combined_overview_format_requests(
        sheet_ids[OVERVIEW_TITLE], layout.blocks,
        column_widths=_column_pixel_widths(width_rows, overview_max_cols))
    if layout.bill_start is not None:
        requests += build_bill_total_format_requests(
            sheet_ids[OVERVIEW_TITLE], layout.bill_total_row)
        requests += build_bill_status_format_requests(
            sheet_ids[OVERVIEW_TITLE], layout.bill_start, layout.num_bill_rows)
    requests += build_overview_chart_requests(sheet_ids[OVERVIEW_TITLE], layout)
    requests += build_readability_requests(
        sheet_ids[OVERVIEW_TITLE], len(layout.rows),
        header_rows=([layout.bill_start] if layout.bill_start is not None else [])
        + [b["start"] for b in layout.blocks],
        dashboard=True)

    # Budget 分頁 A/B 欄（分類、月預算）只在第一次建立時寫入，之後同步不再
    # 碰，使用者填的數字才不會被洗掉；已存在的分頁改讀現有的分類列順序
    # （使用者可能自己加過列）。
    if BUDGET_TITLE in newly_created:
        budget_categories = list(BUDGET_CATEGORIES)
    else:
        budget_categories = read_budget_categories(service, spreadsheet_id)
    requests += build_budget_format_requests(sheet_ids[BUDGET_TITLE], len(budget_categories))
    requests += build_budget_chart_requests(sheet_ids[BUDGET_TITLE], len(budget_categories))
    requests += build_readability_requests(sheet_ids[BUDGET_TITLE], 2 + len(budget_categories),
                                           header_rows=[1], dashboard=True)

    # 只給第 1 列的月份/幣別說明文字用（純顯示），C:E 欄本身已經是 Sheets
    # 原生公式（見 write_budget_reference_and_actual_columns），不用 Python
    # 再算一次「最新月的實際花費」。
    month = _latest_recent_month(txns, currency)

    # 第 1-2 列（月份/幣別說明＋表頭）純文字/純格式，每次同步都重寫，不像
    # A/B 欄資料列受 scaffold-only 保護。
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!A1",
        valueInputOption="RAW",
        body={"values": [budget_info_row(month, currency), BUDGET_HEADER]}).execute()

    if BUDGET_TITLE in newly_created:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=f"{BUDGET_TITLE}!A3",
            valueInputOption="RAW", body={"values": budget_scaffold_rows()}).execute()
        _mark_scaffolded(state, state_path, BUDGET_TITLE)

    if budget_categories:
        write_budget_reference_and_actual_columns(service, spreadsheet_id, budget_categories)

    # 五大目標區塊（G:K，跟 A:E 主表併排）：月收入來自本地 config（呼叫端
    # 傳進來的 goal_monthly_income，不進 Sheet），表頭每次同步都重寫；實際/
    # 差異欄本身是 Sheets 原生公式（見 build_goal_rows），不用 Python 算好
    # 塞值。
    write_goal_rows(service, spreadsheet_id, goal_monthly_income)
    requests += build_goal_format_requests(sheet_ids[BUDGET_TITLE])

    # 選單（B1 月份、D1 帳戶）是使用者狀態：只在新建、或從舊版 Month Detail
    # 改名過來（還沒有帳戶選單）時寫入；表頭與 QUERY 每次重寫。
    if DETAIL_TITLE in newly_created or DETAIL_TITLE in renamed:
        write_month_detail_selectors(service, spreadsheet_id, _detail_default_month(txns))
        _mark_scaffolded(state, state_path, DETAIL_TITLE)
    write_month_detail_query(service, spreadsheet_id)
    # 明細查詢是 QUERY 從交易分頁撈的，欄寬沿用交易分頁對應欄位的算法
    detail_cols = [HEADER.index(h) for h in DETAIL_HEADER]
    requests += build_month_detail_format_requests(
        sheet_ids[DETAIL_TITLE], _detail_months(txns), accounts,
        column_widths=[main_widths[i] for i in detail_cols] if main_widths else None)
    # QUERY 展開列數依選的月份而定，列高抓「最多交易的那個月」的量。
    busiest_month = max(Counter(t.date[:7] for t in txns if _is_recent(t) and t.date).values(),
                        default=0)
    requests += build_readability_requests(sheet_ids[DETAIL_TITLE], 3 + busiest_month,
                                           header_rows=[2], dashboard=True)

    requests += build_tab_order_requests(sheet_ids)

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    return SyncResult(spreadsheet_url=state["spreadsheet_url"], rows=len(main_rows) - 1,
                      accounts=accounts, archived_rows=len(archive_rows) - 1,
                      budget_month=month, created=created)
