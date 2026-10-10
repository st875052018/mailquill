"""txn_id 遷移：把加入 seq 之前的舊式 id 重算成現行公式，並去掉因此產生的重複列。

背景：`make_txn_id` 後來加入 `seq`（同一份帳單內的出現序號）。既有 CSV 的 id
是舊公式（無 seq）算的，沒有一併遷移，於是同一封信被重抓時，同一筆交易會拿到
兩種 id，`append_transactions` 只比對 txn_id 就當成兩筆不同交易寫進去。
"""
import hashlib

from mailquill.migrate import legacy_txn_id, plan_migration
from mailquill.schema import Transaction, make_txn_id


def _txn(merchant, *, date="2026-06-01", amount="100", seq="", txn_id=None,
         id_seq=None, msg_id="m1", imported_at="2026-06-29T15:52:20",
         bank="Cathay", last4="6224"):
    """造一筆交易；txn_id 不給時用現行公式算。

    `seq` 是存進欄位的值，`id_seq` 是拿來算 id 的 seq（不給就等於 seq）；
    兩者分開才造得出「欄位沒存 seq、id 卻是用 seq=5 算的」這類列。"""
    if txn_id is None:
        txn_id = make_txn_id(bank, last4, date, amount, merchant,
                             int(seq or 0) if id_seq is None else id_seq)
    return Transaction(
        txn_id=txn_id, date=date, post_date="", amount=amount, currency="TWD",
        merchant_raw=merchant, merchant_norm=merchant, category_l1="Uncategorized",
        category_l2="", bank=bank, account_last4=last4, source_type="pdf",
        source_msg_id=msg_id, raw_ref=msg_id, imported_at=imported_at, seq=seq,
    )


def _legacy(merchant, **kw):
    """造一筆用舊公式（無 seq）算 id 的交易。"""
    t = _txn(merchant, **kw)
    t.txn_id = legacy_txn_id(t)
    t.seq = ""
    return t


def test_legacy_txn_id_matches_pre_seq_formula():
    t = _txn("全聯", date="2026-06-16", amount="2184")
    expected = hashlib.sha1(
        "Cathay:6224|2026-06-16|2184|全聯".encode("utf-8")
    ).hexdigest()[:16]
    assert legacy_txn_id(t) == expected


def test_legacy_row_id_recomputed_and_seq_filled():
    plan = plan_migration([_legacy("A"), _legacy("B")])
    assert plan.changed == 2
    assert [t.seq for t in plan.txns] == ["0", "1"]
    assert plan.txns[0].txn_id == make_txn_id("Cathay", "6224", "2026-06-01",
                                              "100", "A", 0)
    assert plan.unexplained == []


def test_legacy_and_refetched_duplicate_collapse_to_one_row():
    """同一封信先以舊公式匯入、後又被重抓成新式 id —— 遷移後應只剩一列。"""
    old = [_legacy("A"), _legacy("B")]
    new = [_txn("A", seq="0", imported_at="2026-09-02T09:41:41"),
           _txn("B", seq="1", imported_at="2026-09-02T09:41:41")]
    plan = plan_migration(old + new)

    assert len(plan.txns) == 2, "重複列沒有被收斂"
    assert len(plan.removed) == 2
    # 保留較早匯入的那批
    assert {t.imported_at for t in plan.txns} == {"2026-06-29T15:52:20"}
    assert {t.imported_at for t in plan.removed} == {"2026-09-02T09:41:41"}


def test_genuine_same_day_same_merchant_same_amount_rows_are_kept():
    """真實的同日、同店、同額兩筆交易靠 seq 區分，不能被誤刪。"""
    plan = plan_migration([_legacy("優步－皇冠大車隊"),
                           _legacy("優步－皇冠大車隊")])
    assert len(plan.txns) == 2
    assert plan.removed == []
    assert [t.seq for t in plan.txns] == ["0", "1"]
    assert plan.txns[0].txn_id != plan.txns[1].txn_id


def test_migration_is_idempotent():
    once = plan_migration([_legacy("A"), _legacy("B")])
    twice = plan_migration(once.txns)
    assert twice.changed == 0
    assert twice.removed == []
    assert [t.txn_id for t in twice.txns] == [t.txn_id for t in once.txns]


def test_seq_is_scoped_per_message_and_import():
    """seq 是「同一封信、同一次匯入」內的序號，不是全表流水號。"""
    plan = plan_migration([
        _legacy("A", msg_id="m1"), _legacy("B", msg_id="m1"),
        _legacy("C", msg_id="m2"),
    ])
    assert [t.seq for t in plan.txns] == ["0", "1", "0"]


def test_row_with_stored_seq_matching_its_id_is_already_current_even_if_position_differs():
    # CSV 匯入的列用「同日同店同額第幾次出現」當 seq 並存進 CSV，跟列在群組內的位置
    # 無關；id 與存下來的 seq 吻合就是現行公式，不能被歸成「無法歸類」。
    a = _txn("A", seq="0")
    b = _txn("B", seq="0")      # 位置是 1，但 seq 存的是 0
    plan = plan_migration([a, b])
    assert plan.unexplained == []
    assert plan.changed == 0
    assert [t.seq for t in plan.txns] == ["0", "0"]
    assert [t.txn_id for t in plan.txns] == [a.txn_id, b.txn_id]


def test_seq_is_recovered_from_the_id_when_position_in_group_does_not_match():
    # 同一份帳單分兩批寫進 CSV、或同封信的前面幾筆被對帳合併掉：這列在群組內的位置是 0，
    # 但 id 其實是用 seq=5 算的。id 是現行公式的雜湊，直接試出 seq 比用位置猜可靠。
    row = _txn("A", id_seq=5)                # id 由 seq=5 算出，欄位沒存 seq
    plan = plan_migration([row])
    assert plan.unexplained == []
    assert plan.changed == 0
    assert plan.txns[0].txn_id == row.txn_id
    assert plan.txns[0].seq == "5"


def test_row_whose_id_matches_no_known_formula_is_reported_and_left_untouched():
    """id 既非舊公式、也無法用位置或雜湊重現現行公式的列，不動它，只回報。"""
    bogus = _txn("A", txn_id="deadbeefdeadbeef")
    plan = plan_migration([bogus])
    assert plan.unexplained == [bogus]
    assert plan.txns == [bogus]
    assert plan.txns[0].seq == "", "無法歸類的列不應被填入猜測的 seq"
    assert plan.changed == 0


def test_non_numeric_or_mismatched_stored_seq_does_not_crash_and_is_recovered():
    # 欄位裡的 seq 壞掉（非數字）或跟 id 對不上時，不能拿來信任也不能丟例外；
    # id 本身仍是現行公式，從雜湊試出真正的 seq。
    junk = _txn("A", seq="abc", id_seq=2)
    wrong = _txn("B", seq="9", id_seq=1)
    plan = plan_migration([junk, wrong])
    assert plan.unexplained == []
    assert [t.seq for t in plan.txns] == ["2", "1"]
    assert [t.txn_id for t in plan.txns] == [junk.txn_id, wrong.txn_id]


def test_seq_beyond_search_limit_stays_unexplained():
    from mailquill.migrate import MAX_SEQ_SEARCH
    far = _txn("A", id_seq=MAX_SEQ_SEARCH + 1)
    plan = plan_migration([far])
    assert plan.unexplained == [far]


def test_unexplained_rows_are_never_deduped_against_normal_rows():
    normal = _txn("A", seq="0")
    twin = _txn("A", seq="0", txn_id=normal.txn_id)   # 同 id 的另一列照樣算正常、會被收斂
    weird = _txn("A", txn_id="deadbeefdeadbeef")
    weird2 = _txn("A", txn_id="deadbeefdeadbeef")      # 兩筆一樣的怪 id 也都保留
    plan = plan_migration([normal, twin, weird, weird2])
    assert len(plan.removed) == 1
    assert plan.unexplained == [weird, weird2]
    assert len(plan.txns) == 3
