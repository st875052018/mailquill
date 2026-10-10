"""CTBC（中國信託）信用卡消費明細 parser。

CTBC 沒有像其他銀行一樣寄結構化電子帳單到 Gmail，只能到官網「帳單明細
查詢」頁面手動匯出 CSV（`mailquill ingest` 會依內容格式自動辨識，PDF
走原本的 parser registry）。

CSV 為 Big5 編碼，前幾行是標題／匯出時間／空行，之後才是表頭：
    消費日,入帳起息日,摘要,幣別,消費地金額,外幣折算日,消費地,末四碼,新臺幣金額
一卡多附卡會同時出現在同一份匯出檔，用「末四碼」欄位區分卡片；外幣消費
會多填「幣別／消費地金額／外幣折算日／消費地」，但「新臺幣金額」永遠是
已折算好的最終計價金額，所以這裡的 amount 一律讀這欄、currency 固定
TWD，不理會外幣欄位。

「本行扣繳」「現金存款繳」「網銀行動繳」之類的列，是「繳交這期帳單」的
還款動作（把錢從銀行帳戶轉去繳卡費），不是消費，也沒有末四碼（帳戶層級
動作，非卡片層級）；用「無末四碼 + 摘要以'繳'結尾」判斷略過，比寫死列
舉法穩，日後出現新的還款方式字樣也能自動辨識。「現金回饋帳單抵用」則有
末四碼，是該卡的帳單折抵，視為（負向）消費金額保留；「逾期還款違約金」
雖然也沒有末四碼，但不是還款動作而是罰金（摘要不以「繳」結尾），不會被
這條規則誤刪，照樣匯入（落回 Uncategorized 待手動分類）。

新臺幣金額欄位含千分位逗號、負數會用雙引號包住，交給 normalizer 的
normalize_amount 去逗號即可，這裡不用另外處理。
"""
from __future__ import annotations

import csv
import io

from mailquill.raw_txn import RawTxn

BANK = "CTBC"

_PAYMENT_SUFFIX = "繳"


def _to_iso_date(yyyy_mm_dd: str) -> str:
    return yyyy_mm_dd.strip().replace("/", "-")


def parse_ctbc_csv(text: str) -> list[RawTxn]:
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith("消費日,")), None
    )
    if header_idx is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))

    out: list[RawTxn] = []
    for row in reader:
        date = (row.get("消費日") or "").strip()
        amount = (row.get("新臺幣金額") or "").strip()
        if not date or not amount:
            continue
        merchant = " ".join((row.get("摘要") or "").split())
        account_last4 = (row.get("末四碼") or "").strip()
        if not account_last4 and merchant.endswith(_PAYMENT_SUFFIX):
            continue  # 還款動作（扣繳/存款繳/行動繳），非消費
        post_date = (row.get("入帳起息日") or "").strip()
        out.append(RawTxn(
            bank=BANK,
            date=_to_iso_date(date),
            post_date=_to_iso_date(post_date) if post_date else "",
            amount=amount,
            merchant_raw=merchant,
            currency="TWD",
            account_last4=account_last4,
        ))
    return out
