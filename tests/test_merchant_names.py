from mailquill.merchant_names import (
    Alias, apply_display_names, clean_merchant, load_aliases, aliases_path_for,
)
from mailquill.schema import Transaction


def test_amex_fixed_width_drops_city_and_state():
    assert clean_merchant("AplPay CALVIN KLEIN CABAZON CA", "Amex") == "CALVIN KLEIN"
    assert clean_merchant("AplPay HUGO BOSS RETNEW YORK NY", "Amex") == "HUGO BOSS RET"
    assert clean_merchant("LAS VEGAS CANDLEWOODLAS VEGAS NV", "Amex") == "LAS VEGAS CANDLEWOOD"
    assert clean_merchant("AplPay ANTHROPIC* CLSAN FRANCISCO CA", "Amex") == "ANTHROPIC* CL"
    # 非 Amex 不套固定欄寬
    assert clean_merchant("LAS VEGAS CANDLEWOODLAS VEGAS NV", "Cathay") == "LAS VEGAS CANDLEWOODLAS VEGAS NV"


def test_strips_payment_prefixes():
    assert clean_merchant("連支＊紅豆食府", "CTBC") == "紅豆食府"
    assert clean_merchant("連支> POKE十夏威夷", "CTBC") == "POKE十夏威夷"
    assert clean_merchant("連支POKE十夏威夷", "CTBC") == "POKE十夏威夷"
    assert clean_merchant("SQ*NADOYA NO KATTE", "Cathay") == "NADOYA NO KATTE"
    assert clean_merchant("AplPay TST* WETZEL'SCABAZON CA", "Amex") == "WETZEL'S"


def test_strips_hsbc_ocr_flag_noise():
    assert clean_merchant("APE7Z844LIFECORPORAT", "HSBC") == "LIFECORPORAT"
    assert clean_merchant("APE7/844SEVEN-ELEVEN", "HSBC") == "SEVEN-ELEVEN"
    assert clean_merchant("APE7844SQ* BLUEBOTTL", "HSBC") == "BLUEBOTTL"


def test_strips_urls_and_trailing_codes():
    assert clean_merchant("UBER TRIP HTTPS://HELP.UBER.COTW", "Cathay") == "UBER TRIP"
    assert clean_merchant("UBER *TRIP HELP.UBER.COM", "Cathay") == "UBER *TRIP"
    assert clean_merchant("POVO CHARGES 100001", "Fubon") == "POVO CHARGES"
    assert clean_merchant("STEAMGAMES.COM 4259522985", "Cathay") == "STEAMGAMES.COM"
    assert clean_merchant("WWW.DAZN.COM", "Cathay") == "WWW.DAZN.COM"


def test_fullwidth_to_halfwidth_and_fallback():
    assert clean_merchant("ＪｕＪｕＳｐａｎｉｓ", "Cathay") == "JuJuSpanis"
    assert clean_merchant("", "Cathay") == ""
    assert clean_merchant("連支", "CTBC") == "連支"  # 清完變空 → 退回原字串


def _txn(merchant, bank="Cathay", amount="100", tid=None):
    return Transaction(
        txn_id=tid or merchant, date="2026-07-20", post_date="", amount=amount,
        currency="TWD", merchant_raw=merchant, merchant_norm="", category_l1="",
        category_l2="", bank=bank, account_last4="1234", source_type="pdf",
        source_msg_id="m", raw_ref="", imported_at="",
    )


def test_alias_wins_over_cleaning_and_fee_uses_display_name():
    aliases = [Alias("mobile suica", "Suica 儲值"), Alias("FUKUTAKE", "福武財團（直島）")]
    out = apply_display_names([
        _txn("MOBILE SUICA APPLE V", amount="3998"),
        _txn("國外交易手續費 -MOBILE", amount="60"),
        _txn("FUKUTAKE FOUNDATION"),
        _txn("SQ*NADOYA NO KATTE"),
    ], aliases)
    assert [t.merchant_norm for t in out] == [
        "Suica 儲值", "國外手續費 · Suica 儲值", "福武財團（直島）", "NADOYA NO KATTE",
    ]


def test_alias_not_applied_to_fee_line_itself():
    # 別名 FUKUTAKE 不能把「國外交易手續費 -FUKUTA...」整行蓋掉
    out = apply_display_names([_txn("國外交易手續費 -FUKUTAKE")], [Alias("FUKUTAKE", "福武")])
    assert out[0].merchant_norm == "國外手續費（FUKUTAKE）"


def test_load_aliases(tmp_path):
    p = tmp_path / "merchants.yaml"
    p.write_text('aliases:\n  - {keyword: "FUKUTAKE", name: "福武財團"}\n', encoding="utf-8")
    assert load_aliases(str(p)) == [Alias("FUKUTAKE", "福武財團")]
    assert load_aliases(str(tmp_path / "missing.yaml")) == []


def test_aliases_path_sits_next_to_categories(tmp_path):
    assert aliases_path_for(str(tmp_path / "categories.yaml")) == str(tmp_path / "merchants.yaml")
    assert aliases_path_for("categories.yaml") == "merchants.yaml"


def test_strips_taiwan_and_korean_processor_prefixes():
    assert clean_merchant("連加＊真心奉茶－二聖店", "Cathay") == "真心奉茶-二聖店"
    assert clean_merchant("全支付－全聯", "Cathay") == "全聯"
    assert clean_merchant("藍新－ＫＫＴＩＸ售票報", "Cathay") == "KKTIX售票報"
    assert clean_merchant("NICE*GRACERY", "Cathay") == "GRACERY"
    assert clean_merchant("KICC*HAEWOONDAE_BLU130201", "Cathay") == "HAEWOONDAE_BLU"
    assert clean_merchant("優步－麥當勞Ｓ００１", "Cathay") == "優步-麥當勞S001"  # 優步保留（看得出是外送）


def test_strips_repeated_prefixes():
    assert clean_merchant("連加＊連加＊Ｇｒｅａｔ", "Cathay") == "Great"


def test_keeps_prefix_when_amex_truncated_merchant_to_a_stub():
    assert clean_merchant("AplPay PADDLE.NET* TASTORIA NY", "Amex") == "PADDLE.NET* T"


def test_hsbc_strips_glued_tokyo_japan_tail():
    assert clean_merchant("APE7Z844LAWSONTOKYOJ", "HSBC") == "LAWSON"
    assert clean_merchant("BICCAMERATOKYOJPN", "HSBC") == "BICCAMERA"
    assert clean_merchant("TOKYUSTORETO", "HSBC") == "TOKYUSTORE"
    assert clean_merchant("SAGATANLITOKY", "HSBC") == "SAGATANLI"
    assert clean_merchant("GOTANDATOKYU", "HSBC") == "GOTANDATOKYU"
    assert clean_merchant("SQO* KINTAROUS", "HSBC") == "KINTAROUS"
    assert clean_merchant("PHOTO", "HSBC") == "PHOTO"  # 剩不到 5 字就不剝
    assert clean_merchant("LAWSONTOKYOJ", "Cathay") == "LAWSONTOKYOJ"


def test_strips_glued_acquirer_code():
    assert clean_merchant("ONIMARU SANJOKAWARA100001", "Fubon") == "ONIMARU SANJOKAWARA"
    assert clean_merchant("SHINSEGAE DEPARTMEN130201", "Cathay") == "SHINSEGAE DEPARTMEN"
    assert clean_merchant("SHINJIYUKUNEGISHI K100001", "Fubon") == "SHINJIYUKUNEGISHI K"
    assert clean_merchant("Nintendo CC1581624755", "Cathay") == "Nintendo CC"
    # 前面太短的不剝（可能是店號本身）
    assert clean_merchant("樂購蝦皮－385701", "Cathay") == "樂購蝦皮-385701"
    assert clean_merchant("台灣大車隊19411", "Cathay") == "台灣大車隊19411"


def test_replace_alias_only_swaps_keyword_and_keeps_store():
    aliases = [Alias("富胖達", "Foodpanda", replace=True)]
    out = apply_display_names([_txn("富胖達－ＹＡＹＯＩ彌生")], aliases)
    assert out[0].merchant_norm == "Foodpanda-YAYOI彌生"


def test_load_aliases_reads_replace_flag(tmp_path):
    p = tmp_path / "merchants.yaml"
    p.write_text('aliases:\n  - {keyword: "富胖達", name: "Foodpanda", replace: true}\n', encoding="utf-8")
    assert load_aliases(str(p)) == [Alias("富胖達", "Foodpanda", replace=True)]
