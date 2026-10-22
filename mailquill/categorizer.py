"""兩層 rule-based 分類：關鍵字出現在商家字串內即命中。

比對前會先做 Unicode 正規化（NFKC）與大小寫摺疊（casefold），所以
全形/半形（ＱＴａｘｉ↔QTaxi、ＧｌｏｂａｌＭａｌｌ↔GlobalMall）與大小寫
（Uber↔UBER）都視為相同，台灣帳單常見的全形英數字也能正確命中。

所有規則都沒命中時，再忽略空白比一次：OCR 抽出的商家名稱常在字中間多插
空白（SEVEN- ELEVEN、m om o購物網）。只當備援、不改變原本就命中的結果。
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace

import yaml

from mailquill.fees import is_foreign_fee
from mailquill.schema import Transaction

_RULE_LINE_RE = re.compile(r"^(\s*-\s*)(\{.*\})\s*$")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def _squeeze(s: str) -> str:
    return re.sub(r"\s+", "", s)


@dataclass
class Rule:
    keyword: str
    l1: str
    l2: str


def load_categories(path: str) -> list[Rule]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [
        Rule(keyword=r["keyword"], l1=r["l1"], l2=r["l2"])
        for r in (data.get("rules") or [])
    ]


def format_rule_line(rule: Rule) -> str:
    flow = yaml.safe_dump(
        {"keyword": rule.keyword, "l1": rule.l1, "l2": rule.l2},
        allow_unicode=True, default_flow_style=True, sort_keys=False, width=float("inf"),
    ).strip()
    return f"  - {flow}\n"


# (原始文字, 是否為規則列, 規則列的 keyword)；非規則列（註解/空行）keyword 為 None
_Line = tuple[str, bool, "str | None"]


def _parse_lines(lines: list[str]) -> list[_Line]:
    parsed_lines: list[_Line] = []
    for line in lines:
        m = _RULE_LINE_RE.match(line)
        if not m:
            parsed_lines.append((line, False, None))
            continue
        try:
            parsed = yaml.safe_load(m.group(2))
        except yaml.YAMLError:
            parsed = None
        kw = parsed.get("keyword") if isinstance(parsed, dict) else None
        parsed_lines.append((line, True, kw))
    return parsed_lines


def _upsert_in_lines(lines: list[_Line], rule: Rule) -> bool:
    """在已解析的行清單上原地做 upsert（語意見 upsert_rule）。"""
    insert_at = next((i for i, ln in enumerate(lines) if ln[1]), None)
    kept: list[_Line] = []
    replaced = False
    new_insert_at = None
    for line in lines:
        if line[1]:
            if new_insert_at is None:
                new_insert_at = len(kept)
            if line[2] == rule.keyword:
                replaced = True
                continue  # 丟掉這條舊規則，新的會插在最前面
        kept.append(line)

    text = format_rule_line(rule)
    if insert_at is None:
        prefix = "" if not kept or kept[-1][0].endswith("\n") else "\n"
        kept.append((prefix + text, True, rule.keyword))
    else:
        kept.insert(new_insert_at, (text, True, rule.keyword))
    lines[:] = kept
    return replaced


def upsert_rule(categories_path: str, rule: Rule) -> bool:
    """新增/更新一條規則：跟 rule.keyword 完全相同的既有規則（不論在檔案
    哪個位置）先移除，新規則永遠插在 `rules:` 清單最前面（不是附加到
    尾端）——`categorize()` 由上而下取第一個命中，越前面優先權越高。只
    附加到尾端的話，一旦這個 keyword 剛好是某條更早、比對範圍更廣的既有
    規則的子字串（例如「財團法人」比「財團法人○○醫院」更早命中），新
    規則永遠生效不了，使用者在網頁上「編輯」分類會變成看起來什麼都沒
    發生、只在檔案裡越堆越多用不到的重複規則。只動規則列本身，其餘註解/
    空行原樣保留。回傳這次是否真的取代了一條既有規則（供前端顯示用）。
    """
    return upsert_rules(categories_path, [rule]) > 0


def upsert_rules(categories_path: str, rules: list[Rule]) -> int:
    """依序對每條規則做 upsert（各自插到 `rules:` 清單最前面，同 keyword
    的舊規則會被取代）。amex/cathay autocat 用這個而不是直接附加到檔尾，
    避免新產生的規則被更早、比對範圍更廣的既有規則蓋掉（見 upsert_rule
    docstring）。整份檔案只讀、解析、寫各一次（逐條呼叫 upsert_rule 在上百
    條規則時要數秒）。回傳這批規則裡「真的取代了既有規則」的筆數。
    """
    with open(categories_path, encoding="utf-8") as f:
        lines = _parse_lines(f.readlines())
    replaced = sum(_upsert_in_lines(lines, r) for r in rules)
    with open(categories_path, "w", encoding="utf-8") as f:
        f.writelines(text for text, _, _ in lines)
    return replaced


def ensure_rule(categories_path: str, keyword: str, l1: str, l2: str) -> bool:
    """現有規則歸不到 (l1, l2) 才為 keyword 補一條規則；回傳是否有補。"""
    if categorize(keyword, load_categories(categories_path)) == (l1, l2):
        return False
    upsert_rule(categories_path, Rule(keyword=keyword, l1=l1, l2=l2))
    return True


def categorize(merchant_raw: str, rules: list[Rule]) -> tuple[str, str]:
    # 國外手續費行帶著原商家殘片（如「國外交易手續費 -STARBU」），會被排在
    # 前面的商家規則搶走，所以不看規則順序、一律歸手續費。
    if is_foreign_fee(merchant_raw):
        return ("Other", "Fee")
    folded = _fold(merchant_raw)
    for rule in rules:
        if _fold(rule.keyword) in folded:
            return (rule.l1, rule.l2)
    squeezed = _squeeze(folded)
    for rule in rules:
        if _squeeze(_fold(rule.keyword)) in squeezed:
            return (rule.l1, rule.l2)
    return ("Uncategorized", "")


def generate_rules_from_map(
    pairs: Iterable[tuple[str, str]],
    category_map: dict[str, tuple[str, str]],
    existing_rules: list[Rule],
) -> list[Rule]:
    """依 (merchant, source_category) 產生尚未被涵蓋的新規則（不修改
    existing_rules）。只對「目前規則完全沒命中」且「source_category 在
    category_map 查得到對照」的商家產生規則；amex_autocat / cathay_autocat
    共用同一套邏輯，差別只在 pairs 的來源與各自的 category_map。
    """
    rules = list(existing_rules)
    new_rules: list[Rule] = []
    for merchant, source_category in pairs:
        l1, _l2 = categorize(merchant, rules)
        if l1 != "Uncategorized":
            continue  # 已有規則命中（含這次新產生的），不需要
        mapped = category_map.get(source_category)
        if mapped is None:
            continue  # 這個來源類別沒對照，維持未分類
        rule = Rule(keyword=merchant, l1=mapped[0], l2=mapped[1])
        rules.append(rule)
        new_rules.append(rule)
    return new_rules


def apply_categories(txn: Transaction, rules: list[Rule]) -> Transaction:
    l1, l2 = categorize(txn.merchant_raw, rules)
    return replace(txn, category_l1=l1, category_l2=l2)
