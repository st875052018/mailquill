"""從 Amex CSV 的 Category 欄位，自動產生 categories.yaml 的 keyword 規則。

大量一次性美國商家（如一趟美國旅程）逐筆手寫關鍵字規則不划算；Amex 自己
的 Category（Travel-Lodging、Restaurant-Bar & Café…）覆蓋率很高，拿來當
fallback：只對「目前規則完全沒命中」且「Category 查得到對照
（`amex_csv.AMEX_CATEGORY_MAP`）」的商家，產生一條 keyword＝完整商家字串
的新規則。寫入交給 `categorizer.upsert_rules`（插到規則清單最前面），不是
單純附加到尾端——避免新規則被更早、比對範圍更廣的既有規則蓋掉。
"""
from __future__ import annotations

from mailquill.amex_csv import AMEX_CATEGORY_MAP, parse_amex_csv
from mailquill.categorizer import Rule, generate_rules_from_map


def generate_rules(csv_paths: list[str], existing_rules: list[Rule]) -> list[Rule]:
    """回傳需要新增的規則清單（不修改 existing_rules）。"""
    pairs: list[tuple[str, str]] = []
    for path in csv_paths:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        pairs.extend(
            (raw_txn.merchant_raw, raw_txn.source_category)
            for raw_txn in parse_amex_csv(text)
        )
    return generate_rules_from_map(pairs, AMEX_CATEGORY_MAP, existing_rules)
