import textwrap
from dataclasses import replace

from mailquill.schema import Transaction
from mailquill.categorizer import (
    load_categories, categorize, apply_categories, upsert_rule, upsert_rules, Rule,
)


def _write_rules(tmp_path):
    p = tmp_path / "categories.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - keyword: "全聯"
            l1: "Food"
            l2: "Groceries"
          - keyword: "中華電信"
            l1: "Housing"
            l2: "Telecom"
    """), encoding="utf-8")
    return str(p)


def _txn(merchant):
    return Transaction(
        txn_id="x", date="2026-06-01", post_date="", amount="100.00",
        currency="TWD", merchant_raw=merchant, merchant_norm="",
        category_l1="Uncategorized", category_l2="", bank="Cathay",
        account_last4="1234", source_type="email_body",
        source_msg_id="m1", raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def test_load_categories(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert rules[0] == Rule(keyword="全聯", l1="Food", l2="Groceries")
    assert len(rules) == 2


def test_categorize_match_substring(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("全聯福利中心 信義店", rules) == ("Food", "Groceries")


def test_categorize_no_match_returns_uncategorized(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    assert categorize("某不知名商家", rules) == ("Uncategorized", "")


def test_apply_categories_returns_updated_copy(tmp_path):
    rules = load_categories(_write_rules(tmp_path))
    txn = _txn("中華電信")
    out = apply_categories(txn, rules)
    assert (out.category_l1, out.category_l2) == ("Housing", "Telecom")
    assert out.merchant_raw == "中華電信"
    assert txn.category_l1 == "Uncategorized"  # 原物件不被改動


def test_categorize_is_fullwidth_and_case_insensitive(tmp_path):
    import textwrap
    p = tmp_path / "c.yaml"
    p.write_text(textwrap.dedent("""
        rules:
          - {keyword: "yoxi", l1: "Transport", l2: "Taxi"}
          - {keyword: "GlobalMall", l1: "Other", l2: "Department"}
          - {keyword: "Uber", l1: "Transport", l2: "Taxi"}
    """), encoding="utf-8")
    rules = load_categories(str(p))
    assert categorize("ｙｏｘｉ", rules) == ("Transport", "Taxi")          # 全形→半形
    assert categorize("ＧｌｏｂａｌＭａｌｌ", rules) == ("Other", "Department")  # 全形→半形
    assert categorize("UBER EATS", rules) == ("Transport", "Taxi")        # 大小寫


def _flow_rules_file(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_upsert_rule_inserts_new_rule_at_top(tmp_path):
    path = _flow_rules_file(tmp_path, textwrap.dedent("""\
        rules:
          - {keyword: "全聯", l1: "Food", l2: "Groceries"}
    """))
    replaced = upsert_rule(path, Rule(keyword="星巴克", l1="Food", l2="Dining"))
    assert replaced is False
    rules = load_categories(path)
    assert rules[0] == Rule(keyword="星巴克", l1="Food", l2="Dining")
    assert rules[1] == Rule(keyword="全聯", l1="Food", l2="Groceries")


def test_upsert_rule_fixes_shadowing_by_earlier_broader_rule(tmp_path):
    # 「財團法人」在前面先命中，之後才加的「財團法人私立高雄醫」永遠生效
    # 不了——除非新規則插到「財團法人」前面（這就是 upsert_rule 要保證的）。
    path = _flow_rules_file(tmp_path, textwrap.dedent("""\
        rules:
          - {keyword: "財團法人", l1: "Donation", l2: "Donation"}
    """))
    upsert_rule(path, Rule(keyword="財團法人私立高雄醫", l1="Medical", l2="Care"))
    rules = load_categories(path)
    assert categorize("財團法人私立高雄醫院附設...", rules) == ("Medical", "Care")


def test_upsert_rule_replaces_existing_rule_for_same_keyword_without_duplicating(tmp_path):
    path = _flow_rules_file(tmp_path, textwrap.dedent("""\
        rules:
          - {keyword: "財團法人私立高雄醫", l1: "Medical", l2: ''}
          - {keyword: "財團法人私立高雄醫", l1: "Medical", l2: "Checkup"}
    """))
    replaced = upsert_rule(path, Rule(keyword="財團法人私立高雄醫", l1="Medical", l2="Care"))
    assert replaced is True
    rules = load_categories(path)
    matches = [r for r in rules if r.keyword == "財團法人私立高雄醫"]
    assert matches == [Rule(keyword="財團法人私立高雄醫", l1="Medical", l2="Care")]


def test_upsert_rule_preserves_comments_and_unrelated_rules(tmp_path):
    path = _flow_rules_file(tmp_path, textwrap.dedent("""\
        # 說明註解
        rules:
          - {keyword: "全聯", l1: "Food", l2: "Groceries"}
          # 段落註解
          - {keyword: "家樂福", l1: "Food", l2: "Groceries"}
    """))
    upsert_rule(path, Rule(keyword="星巴克", l1="Food", l2="Dining"))
    content = open(path, encoding="utf-8").read()
    assert "# 說明註解" in content
    assert "# 段落註解" in content
    rules = load_categories(path)
    assert {r.keyword for r in rules} == {"全聯", "家樂福", "星巴克"}


def test_upsert_rules_inserts_each_ahead_of_existing_broader_rule(tmp_path):
    # autocat 產生的規則要用這個（而非附加到尾端），避免被更早、比對範圍
    # 更廣的既有規則蓋掉，見 upsert_rule 的 docstring。
    path = _flow_rules_file(tmp_path, textwrap.dedent("""\
        rules:
          - {keyword: "財團法人", l1: "Donation", l2: "Donation"}
    """))
    new_rules = [
        Rule(keyword="財團法人私立高雄醫", l1="Medical", l2="Care"),
        Rule(keyword="財團法人台北會", l1="Donation", l2="Charity"),
    ]
    replaced_count = upsert_rules(path, new_rules)
    assert replaced_count == 0
    rules = load_categories(path)
    assert categorize("財團法人私立高雄醫院附設...", rules) == ("Medical", "Care")
    assert categorize("財團法人台北會附設...", rules) == ("Donation", "Charity")


def test_upsert_rules_noop_for_empty_list(tmp_path):
    path = _flow_rules_file(tmp_path, 'rules:\n  - {keyword: "全聯", l1: "Food", l2: "Groceries"}\n')
    original = open(path, encoding="utf-8").read()
    assert upsert_rules(path, []) == 0
    assert open(path, encoding="utf-8").read() == original


def test_foreign_fee_always_fee_even_if_merchant_rule_matches_first():
    # 手續費行帶著原商家前綴（-STARBU），不能被排在前面的商家規則搶走
    rules = [Rule("STARBU", "Food", "Dining"), Rule("AIRBNB", "Entertainment", "Lodging")]
    assert categorize("國外交易手續費 -STARBU", rules) == ("Other", "Fee")
    assert categorize("國外退貨手續費 -AIRBNB", rules) == ("Other", "Fee")
    assert categorize("國外交易手續費FAMILYMART", rules) == ("Other", "Fee")
    assert categorize("國外交易服務費（簽帳 1,012 )", rules) == ("Other", "Fee")
    assert categorize("STARBUCKS COFFEE", rules) == ("Food", "Dining")


def test_long_keyword_rule_stays_on_one_line_and_is_replaced_on_upsert(tmp_path):
    from mailquill.categorizer import Rule, format_rule_line, upsert_rule, load_categories
    kw = "A VERY LONG MERCHANT NAME THAT KEEPS GOING AND GOING FOR SIXTY CHARS"
    rule = Rule(keyword=kw, l1="Entertainment", l2="Streaming Service Subscription")
    assert format_rule_line(rule).count("\n") == 1

    path = tmp_path / "categories.yaml"
    path.write_text("rules:\n" + format_rule_line(rule), encoding="utf-8")
    replaced = upsert_rule(str(path), Rule(keyword=kw, l1="Other", l2="X"))
    assert replaced is True
    rules = load_categories(str(path))
    assert [(r.keyword, r.l1) for r in rules] == [(kw, "Other")]
