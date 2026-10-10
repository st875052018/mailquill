from mailquill.fees import link_foreign_fees
from mailquill.schema import Transaction


def _txn(merchant, amount, date="2026-07-20", bank="Cathay", last4="1234", tid=None):
    return Transaction(
        txn_id=tid or f"{merchant}|{amount}|{date}", date=date, post_date="",
        amount=amount, currency="TWD", merchant_raw=merchant, merchant_norm=merchant,
        category_l1="Other", category_l2="Fee", bank=bank, account_last4=last4,
        source_type="pdf", source_msg_id="m", raw_ref="", imported_at="",
    )


def _norm(txns, merchant_raw):
    return next(t.merchant_norm for t in txns if t.merchant_raw == merchant_raw)


def test_links_cathay_fee_to_parent_by_name_prefix():
    txns = [
        _txn("MOBILE SUICA APPLE V", "3998"),
        _txn("JR EAST SHOPPING CENTER", "2000"),
        _txn("國外交易手續費 -MOBILE", "60"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易手續費 -MOBILE") == "國外手續費 · MOBILE SUICA APPLE V"
    assert _norm(out, "MOBILE SUICA APPLE V") == "MOBILE SUICA APPLE V"


def test_links_hsbc_fee_despite_ocr_noise_in_parent():
    txns = [
        _txn("APE7Z844FAMILYMART TOKYO", "400", bank="HSBC"),
        _txn("國外交易手續費FAMILYMART", "6", bank="HSBC"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易手續費FAMILYMART") == "國外手續費 · APE7Z844FAMILYMART TOKYO"


def test_links_fubon_fee_by_amount_hint():
    txns = [
        _txn("AMAZON.CO.JP", "1012", bank="Fubon"),
        _txn("UNIQLO", "1500", bank="Fubon"),
        _txn("國外交易服務費（簽帳 1,012 )", "15", bank="Fubon"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易服務費（簽帳 1,012 )") == "國外手續費 · AMAZON.CO.JP"


def test_links_ctbc_fee_without_hint_by_ratio():
    txns = [
        _txn("店家A", "33000", bank="CTBC"),
        _txn("店家B", "3100", bank="CTBC"),
        _txn("國外交易手續費", "47", bank="CTBC"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易手續費") == "國外手續費 · 店家B"


def test_refund_fee_label_and_negative_amounts():
    txns = [
        _txn("AIRBNB * HMXYZ", "-5000"),
        _txn("國外退貨手續費 -AIRBNB", "-75"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外退貨手續費 -AIRBNB") == "國外退貨手續費 · AIRBNB * HMXYZ"


def test_unmatched_fee_keeps_hint():
    txns = [
        _txn("MOBILE SUICA APPLE V", "3998", date="2026-07-01"),
        _txn("國外交易手續費 -MOBILE", "60", date="2026-07-20"),  # 不同天
        _txn("國外交易手續費", "47", bank="CTBC"),  # 同天沒其他交易
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易手續費 -MOBILE") == "國外手續費（MOBILE）"
    assert _norm(out, "國外交易手續費") == "國外手續費"


def test_does_not_match_other_card_or_other_fee():
    txns = [
        _txn("MOBILE SUICA APPLE V", "3998", last4="9999"),
        _txn("國外交易手續費 -MOBILE", "60"),
    ]
    out = link_foreign_fees(txns)
    assert _norm(out, "國外交易手續費 -MOBILE") == "國外手續費（MOBILE）"


def test_idempotent():
    txns = [_txn("MOBILE SUICA APPLE V", "3998"), _txn("國外交易手續費 -MOBILE", "60")]
    once = link_foreign_fees(txns)
    assert link_foreign_fees(once) == once


def test_merge_folds_fee_into_parent_amount_and_category():
    from mailquill.fees import merge_foreign_fees
    parent = _txn("MOBILE SUICA APPLE V", "3998")
    parent.category_l1, parent.category_l2 = "Transport", "Suica"
    txns = [parent, _txn("國外交易手續費 -MOBILE", "60"), _txn("UNIQLO", "500")]
    out = merge_foreign_fees(txns)
    assert [(m.txn.merchant_raw, m.txn.amount, m.fee) for m in out] == [
        ("MOBILE SUICA APPLE V", "4058", 60.0),
        ("UNIQLO", "500", 0.0),
    ]
    assert out[0].txn.category_l1 == "Transport"


def test_merge_keeps_unlinked_fee_as_own_row_with_fee_equal_amount():
    from mailquill.fees import merge_foreign_fees
    out = merge_foreign_fees([_txn("國外交易手續費", "47", bank="CTBC")])
    assert [(m.txn.amount, m.fee) for m in out] == [("47", 47.0)]


def test_merge_skips_parent_in_other_currency():
    from mailquill.fees import merge_foreign_fees
    parent = _txn("MOBILE SUICA APPLE V", "3998")
    parent.currency = "JPY"
    out = merge_foreign_fees([parent, _txn("國外交易手續費 -MOBILE", "60")])
    assert [(m.txn.amount, m.fee) for m in out] == [("3998", 0.0), ("60", 60.0)]


def test_merge_decimal_amounts():
    from mailquill.fees import merge_foreign_fees
    out = merge_foreign_fees([_txn("MOBILE SUICA APPLE V", "100.5"),
                              _txn("國外交易手續費 -MOBILE", "1.5")])
    assert out[0].txn.amount == "102"
