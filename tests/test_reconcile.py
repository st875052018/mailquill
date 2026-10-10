from mailquill.reconcile import reconcile_provisional
from mailquill.schema import Transaction


def _txn(source_type, bank="Cathay", last4="1234", date="2026-06-01",
         amount="100", merchant="x", txn_id=None):
    return Transaction(
        txn_id=txn_id or f"{source_type}-{merchant}", date=date, post_date="",
        amount=amount, currency="TWD", merchant_raw=merchant, merchant_norm=merchant,
        category_l1="Uncategorized", category_l2="", bank=bank, account_last4=last4,
        source_type=source_type, source_msg_id="m1", raw_ref="",
        imported_at="2026-06-24T00:00:00",
    )


def test_merges_single_matching_pair():
    email = _txn("email_body", merchant="")
    pdf = _txn("pdf", merchant="全聯福利中心")
    kept, result = reconcile_provisional([email, pdf])
    assert kept == [pdf]
    assert (result.merged, result.ambiguous) == (1, 0)


def test_no_pdf_counterpart_keeps_email_body():
    email = _txn("email_body", merchant="")
    kept, result = reconcile_provisional([email])
    assert kept == [email]
    assert (result.merged, result.ambiguous) == (0, 0)


def test_no_email_body_counterpart_keeps_pdf():
    pdf = _txn("pdf", merchant="全聯福利中心")
    kept, result = reconcile_provisional([pdf])
    assert kept == [pdf]
    assert (result.merged, result.ambiguous) == (0, 0)


def test_different_amount_not_merged():
    email = _txn("email_body", amount="100", merchant="")
    pdf = _txn("pdf", amount="200", merchant="全聯福利中心")
    kept, result = reconcile_provisional([email, pdf])
    assert len(kept) == 2
    assert (result.merged, result.ambiguous) == (0, 0)


def test_ambiguous_multiple_email_body_for_one_pdf_left_untouched():
    e1 = _txn("email_body", merchant="", txn_id="e1")
    e2 = _txn("email_body", merchant="", txn_id="e2")
    pdf = _txn("pdf", merchant="全聯福利中心")
    kept, result = reconcile_provisional([e1, e2, pdf])
    assert len(kept) == 3
    assert (result.merged, result.ambiguous) == (0, 1)


def test_one_email_body_with_multiple_pdf_candidates_is_dropped():
    # 一封通知配到兩筆月結單：不管它對應哪一筆，月結單都已涵蓋它，留著只會
    # 重複計入——通知筆數 ≤ 月結單筆數時整個分量的通知全部丟掉。
    email = _txn("email_body", merchant="")
    p1 = _txn("pdf", merchant="全聯福利中心", txn_id="p1")
    p2 = _txn("pdf", merchant="家樂福", txn_id="p2")
    kept, result = reconcile_provisional([email, p1, p2])
    assert kept == [p1, p2]
    assert (result.merged, result.ambiguous) == (1, 0)


def test_different_bank_or_account_not_merged():
    email = _txn("email_body", bank="Cathay", last4="1234", merchant="")
    pdf_other_bank = _txn("pdf", bank="Fubon", last4="1234", merchant="某商店")
    pdf_other_acct = _txn("pdf", bank="Cathay", last4="5678", merchant="某商店")
    kept, result = reconcile_provisional([email, pdf_other_bank, pdf_other_acct])
    assert len(kept) == 3
    assert (result.merged, result.ambiguous) == (0, 0)


def test_other_source_types_untouched():
    csv_txn = _txn("csv", merchant="Amex 商店")
    manual_txn = _txn("manual", merchant="手動記帳")
    kept, result = reconcile_provisional([csv_txn, manual_txn])
    assert kept == [csv_txn, manual_txn]
    assert (result.merged, result.ambiguous) == (0, 0)


def test_merges_pair_within_date_tolerance():
    # 授權日期 06-01，月結單消費日 06-03（半夜刷卡跨日入帳常見情境）
    email = _txn("email_body", date="2026-06-01", merchant="")
    pdf = _txn("pdf", date="2026-06-03", merchant="全聯福利中心")
    kept, result = reconcile_provisional([email, pdf])
    assert kept == [pdf]
    assert (result.merged, result.ambiguous, result.voided) == (1, 0, 0)


def test_does_not_merge_beyond_date_tolerance_and_stays_pending_when_not_yet_closed():
    # pdf 是舊的、日期早於這筆信件通知（月結單還沒追上這筆的期間），同金額
    # 只是巧合，不該被誤認成同一筆；也不該被判定成幽靈交易（月結單還沒到）
    email = _txn("email_body", date="2026-07-20", merchant="")
    older_pdf = _txn("pdf", date="2026-07-01", merchant="全聯福利中心")
    kept, result = reconcile_provisional([email, older_pdf])
    assert len(kept) == 2
    assert (result.merged, result.ambiguous, result.voided) == (0, 0, 0)


def test_voids_email_with_no_pdf_match_once_statement_period_closed():
    # 帳戶已有更晚日期的月結單資料（統計期已經涵蓋過這筆日期+容忍天數），
    # 卻完全找不到同金額 → 視為授權後從未真正請款，直接丟掉
    email = _txn("email_body", date="2026-06-01", amount="3000", merchant="")
    later_pdf = _txn("pdf", date="2026-06-20", amount="500",
                     merchant="別筆消費", txn_id="p-later")
    kept, result = reconcile_provisional([email, later_pdf])
    assert kept == [later_pdf]
    assert (result.merged, result.ambiguous, result.voided) == (0, 0, 1)


def test_keeps_email_pending_when_statement_period_not_yet_closed():
    # 該帳戶最新的月結單資料還沒晚到「這筆日期+容忍天數」之後，代表月結單
    # 還沒寄到，不能斷定是幽靈交易，先留著等下次 reconcile
    email = _txn("email_body", date="2026-06-01", amount="3000", merchant="")
    same_period_pdf = _txn("pdf", date="2026-06-02", amount="500",
                           merchant="別筆消費", txn_id="p-same")
    kept, result = reconcile_provisional([email, same_period_pdf])
    assert len(kept) == 2
    assert (result.merged, result.ambiguous, result.voided) == (0, 0, 0)


def test_never_voids_email_with_a_real_merchant_name_even_when_amount_never_matches():
    # 有商店名稱的每日通知（本行簽約特約商店，如 EVA AIRWAYS）即使完全配
    # 不到金額也不能當幽靈交易砍掉——外幣消費授權/請款金額本來就可能因匯率
    # 認列時點不同而對不齊，這是真的消費，不是取消的授權
    email = _txn("email_body", date="2026-01-30", amount="16088", merchant="EVA AIRWAYS")
    later_pdf = _txn("pdf", date="2026-07-13", amount="500",
                     merchant="別筆消費", txn_id="p-later")
    kept, result = reconcile_provisional([email, later_pdf])
    assert email in kept
    assert (result.merged, result.ambiguous, result.voided) == (0, 0, 0)


def test_no_voiding_when_account_has_no_pdf_history_at_all():
    email = _txn("email_body", date="2026-06-01", amount="3000", merchant="")
    kept, result = reconcile_provisional([email])
    assert kept == [email]
    assert (result.merged, result.ambiguous, result.voided) == (0, 0, 0)


def test_two_unrelated_clean_pairs_sharing_a_common_amount_both_merge():
    # 同一個常見小額（如 20/45/75）一年內在完全不相干的月份各自出現一次
    # email+pdf 配對；分組不含日期，兩組必須各自獨立判斷，不能因為金額
    # 相同就被綁成同一組「候選配對數 != 1」的含糊
    jan_email = _txn("email_body", date="2026-01-06", merchant="", txn_id="jan-e")
    jan_pdf = _txn("pdf", date="2026-01-08", merchant="統一超商", txn_id="jan-p")
    feb_email = _txn("email_body", date="2026-02-04", merchant="", txn_id="feb-e")
    feb_pdf = _txn("pdf", date="2026-02-05", merchant="統一超商", txn_id="feb-p")
    kept, result = reconcile_provisional([jan_email, jan_pdf, feb_email, feb_pdf])
    assert kept == [jan_pdf, feb_pdf]
    assert (result.merged, result.ambiguous, result.voided) == (2, 0, 0)


def test_ambiguous_group_participants_are_not_voided():
    e1 = _txn("email_body", date="2026-06-01", merchant="", txn_id="e1")
    e2 = _txn("email_body", date="2026-06-01", merchant="", txn_id="e2")
    pdf = _txn("pdf", date="2026-06-01", merchant="全聯福利中心")
    kept, result = reconcile_provisional([e1, e2, pdf])
    assert len(kept) == 3
    assert (result.merged, result.ambiguous, result.voided) == (0, 1, 0)


def test_exact_date_pairs_resolved_even_when_cross_dates_fall_within_tolerance():
    # 同金額兩組消費剛好日期相近（都在容忍天數內），若不優先比對「日期完全
    # 相同」的那條邊，會被連通分量誤判成同一組 2 email vs 2 pdf 含糊；
    # 但兩組其實日期都精準對上，應該各自安全合併，不該卡在 ambiguous。
    e1 = _txn("email_body", date="2026-06-15", merchant="", txn_id="e1")
    p1 = _txn("pdf", date="2026-06-15", merchant="全家超商A", txn_id="p1")
    e2 = _txn("email_body", date="2026-06-16", merchant="", txn_id="e2")
    p2 = _txn("pdf", date="2026-06-16", merchant="全家超商B", txn_id="p2")
    kept, result = reconcile_provisional([e1, p1, e2, p2])
    assert kept == [p1, p2]
    assert (result.merged, result.ambiguous, result.voided) == (2, 0, 0)


def test_equal_count_tie_on_both_sides_drops_all_emails():
    # 兩邊日期都完全相同（同一天刷了兩筆一樣金額），分不出誰配誰——但
    # 兩封通知對兩筆月結單，不需要知道配對關係也能確定通知全被月結單涵蓋，
    # 全部丟掉，保留月結單那兩筆。
    e1 = _txn("email_body", date="2026-05-08", merchant="", txn_id="e1")
    e2 = _txn("email_body", date="2026-05-08", merchant="", txn_id="e2")
    p1 = _txn("pdf", date="2026-05-08", merchant="Scan2Pay", txn_id="p1")
    p2 = _txn("pdf", date="2026-05-08", merchant="Scan2Pay", txn_id="p2")
    kept, result = reconcile_provisional([e1, e2, p1, p2])
    assert kept == [p1, p2]
    assert (result.merged, result.ambiguous, result.voided) == (2, 0, 0)


def test_more_emails_than_pdfs_stays_ambiguous():
    # 通知比月結單多（如通知重複列了兩次、月結單只有一筆）：多出來的那筆
    # 可能是幽靈也可能是月結單漏列，不猜，整個分量原樣保留。
    e1 = _txn("email_body", date="2026-01-14", merchant="", txn_id="e1")
    e2 = _txn("email_body", date="2026-01-14", merchant="", txn_id="e2")
    p1 = _txn("pdf", date="2026-01-14", merchant="優步", txn_id="p1")
    kept, result = reconcile_provisional([e1, e2, p1])
    assert len(kept) == 3
    assert (result.merged, result.ambiguous) == (0, 1)


def test_keeps_email_when_statement_for_its_month_is_missing():
    # 6 月的月結單沒進來（解鎖失敗/沒抓到），但 5 月和 7 月的有：前後 PDF 交易
    # 之間隔了兩個多月，無法證明 6 月沒有這筆 → 不能當幽靈交易砍掉。
    email = _txn("email_body", date="2026-06-10", amount="3000", merchant="")
    may = _txn("pdf", date="2026-05-05", amount="500", merchant="五月", txn_id="p-may")
    july = _txn("pdf", date="2026-07-10", amount="600", merchant="七月", txn_id="p-jul")
    kept, result = reconcile_provisional([may, email, july])
    assert email in kept
    assert result.voided == 0


def test_voids_email_when_surrounding_statement_data_is_dense():
    email = _txn("email_body", date="2026-06-10", amount="3000", merchant="")
    before = _txn("pdf", date="2026-06-05", amount="500", merchant="前", txn_id="p-b")
    after = _txn("pdf", date="2026-06-20", amount="600", merchant="後", txn_id="p-a")
    kept, result = reconcile_provisional([before, email, after])
    assert email not in kept
    assert result.voided == 1
