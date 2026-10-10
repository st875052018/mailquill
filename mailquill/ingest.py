"""本地檔案匯入：把手動下載的帳單（如富邦需連結下載、Amex/CTBC 只能
匯出 CSV 者）走同一條 parser→正規化→分類→CSV→SQLite 流程，不經 Gmail。

`.pdf` 走原本的解密＋parser registry；`.csv` 依內容格式自動判斷是
Amex（UTF-8，`amex_csv.py`）還是 CTBC（Big5，`ctbc_csv.py`）——兩種
編碼互斥（CTBC 的中文 Big5 位元組組合不是合法 UTF-8），先試 UTF-8 解
Amex 格式，失敗或解不出交易再試 Big5 解 CTBC 格式。

與 `run` 共用 txn_id 去重，所以同一份帳單即使先前已由 Gmail 抓過，
再以本地檔匯入也不會重複入帳。

PDF 路徑額外呼叫 `parser.parse_bill`（跟 `run_pipeline` 一致，見
`pipeline.py`）把應繳總額/最低應繳寫進 `bills.csv`；CSV 匯入（Amex/CTBC
手動匯出）沒有這類彙總欄位，不呼叫。
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

from mailquill.config import Config, load_passwords
from mailquill.pdf_unlocker import unlock_pdf
from mailquill.pdf_text import extract_pdf_text
from mailquill.parsers import all_parsers
from mailquill.parsers.base import Parser
from mailquill.amex_csv import BANK as AMEX_BANK, parse_amex_csv
from mailquill.ctbc_csv import BANK as CTBC_BANK, parse_ctbc_csv
from mailquill.normalizer import normalize
from mailquill.categorizer import load_categories, apply_categories
from mailquill.merchant_names import aliases_path_for, apply_display_names, load_aliases
from mailquill.store import append_transactions, rebuild_sqlite, rekey_csv_rows
from mailquill.bills_store import append_bills
from mailquill.gmail_message import EmailMessage


@dataclass
class IngestResult:
    files: int = 0
    added: int = 0
    skipped: int = 0
    parsed: list[str] = field(default_factory=list)         # "檔名 -> Bank (n筆)"
    unlock_failures: list[str] = field(default_factory=list)
    needs_parser: list[str] = field(default_factory=list)   # 無 parser 能解析的檔
    parse_warnings: list[str] = field(default_factory=list)


def _collect_by_ext(paths: list[str], ext: str) -> list[str]:
    out: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()          # 子目錄也排序，確保跨平台/跨次序一致
                for f in sorted(files):
                    if f.lower().endswith(ext):
                        out.append(os.path.join(root, f))
        elif p.lower().endswith(ext):
            out.append(p)
    return out


def collect_pdfs(paths: list[str]) -> list[str]:
    """把檔案/資料夾參數展開成 PDF 檔清單（資料夾遞迴、排序）。"""
    return _collect_by_ext(paths, ".pdf")


def collect_csvs(paths: list[str]) -> list[str]:
    """把檔案/資料夾參數展開成 CSV 檔清單（資料夾遞迴、排序）。"""
    return _collect_by_ext(paths, ".csv")


# (編碼, parser, 銀行名稱)：依序嘗試，第一個能解出交易的就是該家銀行
_CSV_FORMATS = (
    ("utf-8-sig", parse_amex_csv, AMEX_BANK),
    ("big5", parse_ctbc_csv, CTBC_BANK),
)


def _load_csv_txns(raw_bytes: bytes) -> tuple[str, list]:
    """依內容格式判斷 CSV 屬於哪家銀行的手動匯出，回傳 (bank名稱, RawTxn 清單)。

    都解不出交易時回 ("", [])。
    """
    for encoding, parse, bank in _CSV_FORMATS:
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        raws = parse(text)
        if raws:
            return bank, raws

    return "", []


def select_parser(text: str, bank: str | None = None):
    """依 PDF 文字內容挑 parser（無寄件者可比對時用）。

    對每個 parser 試解，取解析出最多筆者；bank 有給時只限該銀行的 parser。
    回傳 (parser, raw_txns)；都解不出回 (None, [])。
    """
    msg = EmailMessage("", "", "", "", "", [])
    best = None
    best_txns: list = []
    for parser in all_parsers():
        if bank and bank.lower() not in parser.bank.lower():
            continue
        try:
            txns = parser.parse(msg, [text])
        except Exception:
            txns = []
        if len(txns) > len(best_txns):
            best, best_txns = parser, txns
    return best, best_txns


def _overrides_pdf_text(parser) -> bool:
    return type(parser).extract_pdf_text is not Parser.extract_pdf_text


def select_parser_for_pdf(pdf_bytes: bytes, bank: str | None = None):
    """挑 parser 並回傳 (parser, raw_txns, 該 parser 實際吃的文字)。

    先用一般文字抽取挑；挑不到、或挑到的 parser 有自己的抽字方式（HSBC／CTBC
    的商家欄是圖片，要 OCR）時，改用該 parser 自己的 `extract_pdf_text` 重
    解——跟 `run` 走同一條路，同一份帳單兩種匯入方式才會得到同樣的資料。
    OCR 比較慢，所以只對有覆寫的 parser 才跑。
    """
    text = extract_pdf_text(pdf_bytes)
    parser, raws = select_parser(text, bank)
    if parser is not None and not _overrides_pdf_text(parser):
        return parser, raws, text

    msg = EmailMessage("", "", "", "", "", [])
    candidates = [parser] if parser is not None else [
        p for p in all_parsers()
        if _overrides_pdf_text(p) and (not bank or bank.lower() in p.bank.lower())]
    best, best_raws, best_text = parser, raws, text
    for cand in candidates:
        own_text = cand.extract_pdf_text(pdf_bytes)
        try:
            cand_raws = cand.parse(msg, [own_text])
        except Exception:
            cand_raws = []
        if len(cand_raws) > len(best_raws):
            best, best_raws, best_text = cand, cand_raws, own_text
    return best, best_raws, best_text


def ingest_paths(paths: list[str], cfg: Config, imported_at: str,
                 bank: str | None = None, on_progress=None) -> IngestResult:
    result = IngestResult()
    passwords = load_passwords(cfg.passwords_path)
    category_rules = load_categories(cfg.categories_path)
    pdfs = collect_pdfs(paths)
    csvs = collect_csvs(paths)
    result.files = len(pdfs) + len(csvs)

    collected = []
    bill_dues = []
    for i, path in enumerate(pdfs):
        if on_progress:
            on_progress(i + 1, len(pdfs))
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            result.unlock_failures.append(f"{name}: 讀檔失敗 {e}")
            continue
        res = unlock_pdf(raw, passwords)
        if not res.ok:
            result.unlock_failures.append(f"{name}: {res.error}")
            continue
        try:
            parser, raws, text = select_parser_for_pdf(res.data, bank)
        except Exception as e:
            result.parse_warnings.append(f"{name}: 文字抽取失敗 {e}")
            continue
        if not raws:
            result.needs_parser.append(name)
            continue
        result.parsed.append(f"{name} -> {parser.bank} ({len(raws)}筆)")
        for seq, raw_txn in enumerate(raws):
            txn = normalize(raw_txn, name, "pdf", path, imported_at, seq=seq)
            if txn.amount == "":
                result.parse_warnings.append(f"{name}:{raw_txn.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

        bill_due = parser.parse_bill(EmailMessage(name, "", "", "", "", []), [text])
        if bill_due:
            bill_dues.append(bill_due)

    for path in csvs:
        name = os.path.basename(path)
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read()
        except OSError as e:
            result.unlock_failures.append(f"{name}: 讀檔失敗 {e}")
            continue
        bank, raws = _load_csv_txns(raw_bytes)
        if not raws:
            result.needs_parser.append(name)
            continue
        result.parsed.append(f"{name} -> {bank} ({len(raws)}筆)")
        # CSV 是依日期範圍匯出，重疊的兩份檔案裡同一筆的列位置會不同，所以 seq
        # 不能用列序號；改用「同日同店同額在檔內第幾次出現」，兩份檔案才會算出
        # 同樣的 txn_id 而被去重。
        occurrences: Counter = Counter()
        for raw_txn in raws:
            txn = normalize(raw_txn, name, "csv", path, imported_at)
            key = (txn.bank, txn.account_last4, txn.date, txn.amount, txn.merchant_raw)
            seq = occurrences[key]
            occurrences[key] += 1
            if seq:
                txn = normalize(raw_txn, name, "csv", path, imported_at, seq=seq)
            if txn.amount == "":
                result.parse_warnings.append(f"{name}:{raw_txn.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

    if csvs:
        rekey_csv_rows(cfg.csv_path)  # 舊版 seq 編號的 CSV 列先轉成新編號再去重
    collected = apply_display_names(
        collected, load_aliases(aliases_path_for(cfg.categories_path)))
    append_res = append_transactions(cfg.csv_path, collected)
    result.added = append_res.added
    result.skipped = append_res.skipped
    rebuild_sqlite(cfg.csv_path, cfg.db_path)
    append_bills(cfg.bills_path, bill_dues)
    return result
