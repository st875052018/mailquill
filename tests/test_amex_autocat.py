"""Amex Category 自動產生 categories.yaml 規則測試。"""
from mailquill.amex_autocat import generate_rules
from mailquill.categorizer import Rule

HEADER = (
    "Date,Description,Card Member,Account #,Amount,Extended Details,"
    "Appears On Your Statement As,Address,City/State,Zip Code,Country,"
    "Reference,Category\n"
)


def _row(date, desc, amount, category):
    return f'{date},{desc},JANE DOE,-73006,{amount},"",{desc},,,,,,{category}\n'


def _write_csv(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(HEADER + "".join(rows), encoding="utf-8")
    return str(p)


def test_generates_rule_for_unmatched_merchant_with_known_category(tmp_path):
    csv_path = _write_csv(tmp_path, "a.csv", [
        _row("07/27/2026", "TEST HOTEL LAS VEGAS NV", "200.00", "Travel-Lodging"),
    ])
    new_rules = generate_rules([csv_path], existing_rules=[])
    assert len(new_rules) == 1
    assert new_rules[0].keyword == "TEST HOTEL LAS VEGAS NV"
    assert (new_rules[0].l1, new_rules[0].l2) == ("Entertainment", "Lodging")


def test_repeated_merchant_only_generates_one_rule(tmp_path):
    csv_path = _write_csv(tmp_path, "a.csv", [
        _row("07/27/2026", "TEST HOTEL LAS VEGAS NV", "200.00", "Travel-Lodging"),
        _row("07/28/2026", "TEST HOTEL LAS VEGAS NV", "150.00", "Travel-Lodging"),
        _row("07/29/2026", "TEST HOTEL LAS VEGAS NV", "150.00", "Travel-Lodging"),
    ])
    new_rules = generate_rules([csv_path], existing_rules=[])
    assert len(new_rules) == 1


def test_merchant_already_matched_by_existing_rule_is_skipped(tmp_path):
    csv_path = _write_csv(tmp_path, "a.csv", [
        _row("07/27/2026", "STARBUCKS SEATTLE WA", "5.50", "Restaurant-Bar & Café"),
    ])
    existing = [Rule(keyword="STARBUCKS", l1="Food", l2="Dining")]
    new_rules = generate_rules([csv_path], existing_rules=existing)
    assert new_rules == []


def test_unmapped_category_generates_no_rule(tmp_path):
    csv_path = _write_csv(tmp_path, "a.csv", [
        _row("07/27/2026", "MYSTERY MERCHANT CA", "10.00", "Some-Unknown-Category"),
    ])
    new_rules = generate_rules([csv_path], existing_rules=[])
    assert new_rules == []
