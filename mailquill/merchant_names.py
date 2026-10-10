"""商家顯示名稱（merchant_norm）：別名表優先，其次自動清洗 merchant_raw。

merchant_raw 保留銀行原文（分類規則、txn_id 都靠它），merchant_norm 只給
人看。清洗只拿掉「確定是雜訊」的部分：
- 全形英數 → 半形（NFKC）
- Amex 描述是固定欄寬：前 20 字商家 + 城市 + 州別，只留前 20 字
- 付款方式/收單前綴：`AplPay `、`連支*`、`連加*`、`全支付-`、`藍新-`、`SQ*`、`TST*`…
- 滙豐 OCR 把國旗小圖讀成的 `APE7Z844` 之類前綴，與黏在店名後的 `TOKYOJPN`
- 結尾的客服網址（`HTTPS://HELP.UBER.COTW`）與收單代碼（`100001`，含黏在店名後的）

清不乾淨或看不懂的，用 merchants.yaml 的別名表（`keyword` 出現在
merchant_raw 內就改顯示成 `name`，比對規則同 categories.yaml；加
`replace: true` 則只替換名稱裡的 keyword，例如「富胖達-店名」→「Foodpanda-店名」）。
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, replace

import yaml

from mailquill.categorizer import _fold
from mailquill.fees import is_foreign_fee, link_foreign_fees
from mailquill.schema import Transaction

_AMEX_WIDTH = 20
_AMEX_STATE_RE = re.compile(r" [A-Z]{2}$")
_PREFIX_RES = [
    re.compile(r"^AplPay\s+", re.I),
    re.compile(r"^(連支|連加)\s*[*>]?\s*"),  # LINE Pay 連加/連支
    re.compile(r"^(全支付|藍新)-"),
    re.compile(r"^APE[7Z/]*8/?44"),  # 滙豐 OCR 國旗雜訊
    re.compile(r"^(SQ[O0]?|TST|PADDLE\.NET|PAYPAL|KICC|NICE)\s*\*\s*", re.I),
]
# 滙豐 OCR 把城市/國別黏在店名後（LAWSONTOKYOJ、BICCAMERATOKYOJPN、TOKYUSTORETO）
_HSBC_TAIL_RE = re.compile(r"(TO(K(YO?)?)?(JPN?|J)?|JPN)$")
_SUFFIX_RES = [
    re.compile(r"\s+(HTTPS?://)?HELP\.\S*$", re.I),
    re.compile(r"\s+HTTPS?://\S*$", re.I),
    re.compile(r"\s+\d{5,}$"),
]
# 富邦/國泰的日韓商家：店名截 19 字後直接黏 6 碼以上收單代碼（…SANJOKAWARA100001）
_GLUED_CODE_RE = re.compile(r"^(.{7,}\D)\d{6,}$")


@dataclass(frozen=True)
class Alias:
    keyword: str
    name: str
    replace: bool = False  # True：只把清洗後名稱裡的 keyword 換成 name，其餘（店名）保留


def aliases_path_for(categories_path: str) -> str:
    return os.path.join(os.path.dirname(categories_path), "merchants.yaml")


def load_aliases(path: str) -> list[Alias]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [Alias(str(a["keyword"]), str(a["name"]), bool(a.get("replace", False)))
            for a in (data.get("aliases") or [])]


def clean_merchant(raw: str, bank: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "").strip()
    if bank == "Amex" and len(s) > _AMEX_WIDTH + 2 and _AMEX_STATE_RE.search(s):
        s = s[:_AMEX_WIDTH]
    prev = None
    while prev != s:  # 前綴可能疊好幾層（連加＊連加＊…、APE7844SQ*…）
        prev = s
        for rx in _PREFIX_RES:
            stripped = rx.sub("", s).lstrip()
            if len(stripped) >= 2:  # 剝完只剩一兩個殘字（Amex 截斷）就留著前綴
                s = stripped
    for rx in _SUFFIX_RES:
        s = rx.sub("", s)
    s = _GLUED_CODE_RE.sub(r"\1", s)
    if bank == "HSBC":
        tail = _HSBC_TAIL_RE.search(s)
        if tail and tail.start() >= 5:
            s = s[:tail.start()]
    s = re.sub(r"\s+", " ", s).strip(" .,-_/")
    return s or (raw or "").strip()


def display_name(txn: Transaction, aliases: list[Alias]) -> str:
    folded = _fold(txn.merchant_raw)
    for a in aliases:
        if _fold(a.keyword) in folded:
            if not a.replace:
                return a.name
            cleaned = clean_merchant(txn.merchant_raw, txn.bank)
            kw = unicodedata.normalize("NFKC", a.keyword)
            return re.sub(re.escape(kw), lambda _: a.name, cleaned, flags=re.I)
    return clean_merchant(txn.merchant_raw, txn.bank)


def apply_display_names(txns: list[Transaction], aliases: list[Alias]) -> list[Transaction]:
    """重算所有交易的 merchant_norm；手續費行交給 link_foreign_fees，
    它會引用原交易「清洗/別名後」的名稱。"""
    named = [
        t if is_foreign_fee(t.merchant_raw) else replace(t, merchant_norm=display_name(t, aliases))
        for t in txns
    ]
    return link_foreign_fees(named)
