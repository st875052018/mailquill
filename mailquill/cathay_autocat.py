"""從國泰「每日消費彙整通知」信自帶的「消費類別」欄位，自動產生
categories.yaml 的 keyword 規則。

跟 `amex_autocat.py` 同樣的想法：大量一次性商家逐筆手寫關鍵字不划算；
國泰自己的消費類別（餐飲、超市∕量販…）覆蓋率不錯，拿來當 fallback。
`source_category` 目前只在解析每日通知時短暫存在（見 `parsers/cathay.py`
的 `_parse_daily_notice`），不會落地存進 CSV/SQLite——這裡直接重新掃 Gmail
上的每日通知信取得這個欄位，不動既有的 ingest pipeline／CSV schema。

只對「目前規則完全沒命中」且「消費類別查得到對照
（`CATHAY_CATEGORY_MAP`）」的商家，產生一條 keyword＝完整商家字串的新規則，
附加到 categories.yaml 規則清單尾端——用法跟手寫規則、跟 amex-autocat
完全一樣。故意不對太籠統、猜了可能反而更亂的類別（其他／線上付款／
線上繳費）建規則，猜錯比留著不分類還糟。
"""
from __future__ import annotations

from mailquill.categorizer import Rule, generate_rules_from_map
from mailquill.gmail_client import extract_messages, get_message_metadata, list_labels_messages
from mailquill.gmail_message import EmailMessage
from mailquill.parsers.cathay import CathayParser

CATHAY_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    "餐飲": ("Food", "Dining"),
    "超市∕量販": ("Food", "Groceries"),
    "交通∕運輸": ("Transport", "PublicTransit"),
    "百貨公司": ("Shopping", "Department"),
    "休閒用品": ("Shopping", "Department"),
    "一般購物": ("Shopping", "Department"),
    "家電∕３Ｃ通訊": ("Shopping", "Electronics"),
    "旅行社": ("Entertainment", "Travel"),
    "醫療救護∕整型": ("Medical", "Care"),
    "家具家飾裝潢": ("Shopping", "Department"),
    "服飾∕鞋∕精品": ("Shopping", "Apparel"),
    "飯店∕住宿": ("Entertainment", "Lodging"),
    "禮品店": ("Shopping", "Department"),
    "劇場戲院": ("Entertainment", "Movies"),
    "郵購∕直銷": ("Shopping", "Online"),
    "體育用品": ("Entertainment", "Fitness"),
    "旅遊": ("Entertainment", "Travel"),
}


def fetch_merchant_categories(service, label_names: list[str],
                              query: str | None = None) -> dict[str, str]:
    """掃描每日通知信，回傳 {商店名稱: 消費類別}。標籤底下有各家銀行的信
    （還帶月結單 PDF 附件），所以先只抓標頭（寄件者）篩出國泰的信，只對這些
    才用 `extract_messages` 一次 batch 取完整內容；解析不出類別的信（如月結
    單信）自然被忽略。
    """
    msg_ids, _missing = list_labels_messages(service, label_names, query=query)
    parser = CathayParser()
    cathay_ids = []
    for msg_id in msg_ids:
        sender, subject, date = get_message_metadata(service, msg_id)
        if parser.matches(EmailMessage(msg_id, sender, subject, date, "", [])):
            cathay_ids.append(msg_id)
    messages = extract_messages(service, cathay_ids)
    out: dict[str, str] = {}
    for msg_id in cathay_ids:
        msg = messages.get(msg_id)
        if msg is None:
            continue
        for txn in parser.parse(msg, []):
            if txn.source_category and txn.merchant_raw:
                out[txn.merchant_raw] = txn.source_category
    return out


def generate_rules(merchant_categories: dict[str, str],
                   existing_rules: list[Rule]) -> list[Rule]:
    """回傳需要新增的規則清單（不修改 existing_rules）。"""
    return generate_rules_from_map(
        merchant_categories.items(), CATHAY_CATEGORY_MAP, existing_rules
    )
