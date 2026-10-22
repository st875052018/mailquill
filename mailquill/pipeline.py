"""串接：抓信 → 解密 PDF → parser → 正規化 → 分類 → CSV → SQLite。
順帶收集各家信件附帶的帳單應繳金額（`parser.parse_bill`，有彙總金額可抓的
銀行才實作），寫進獨立的 `bills.csv`（見 `bills_store.py`）。

`cfg.import_label` 底下的信是使用者把手動下載的帳單寄給自己：不看寄件人、
不套 rules，附件交給 `ingest` 依內容挑 parser（見 `_import_attachments`）。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from mailquill import parsers as parsers_pkg
from mailquill.config import Config, load_passwords
from mailquill.rules import load_rules, matches
from mailquill.gmail_client import (
    list_labels_messages, extract_message, get_message_metadata,
)
from mailquill.pdf_unlocker import unlock_pdf
from mailquill.parsers import get_parser
from mailquill.normalizer import normalize
from mailquill.categorizer import load_categories, apply_categories
from mailquill.merchant_names import aliases_path_for, apply_display_names, load_aliases
from mailquill.store import (
    append_transactions, rebuild_sqlite, read_transactions,
    read_registry, write_seen_ids, rekey_csv_rows,
)
from mailquill.ingest import IngestResult, ingest_pdf_bytes, ingest_csv_bytes
from mailquill.bills_store import append_bills


@dataclass
class RunResult:
    fetched: int = 0
    matched: int = 0
    added: int = 0
    skipped: int = 0
    already_seen: int = 0
    needs_parser: list[str] = field(default_factory=list)
    unlock_failures: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)
    fetch_failures: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)  # import_label：「檔名 -> Bank (n筆)」


def _is_pdf(att) -> bool:
    return att.mime_type == "application/pdf" or att.filename.lower().endswith(".pdf")


def parser_fingerprint() -> str:
    """所有 parser 原始碼的雜湊。解不出交易的信會連同它一起記下，parser 一改
    （可能就能解了）指紋就不同，下次 run 自動重抓。"""
    h = hashlib.sha1()
    for path in sorted(Path(parsers_pkg.__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def _import_attachments(msg, passwords, imported_at, category_rules):
    """import_label 信件：PDF/CSV 附件依內容挑 parser。

    回傳 (交易清單, 應繳資料清單, IngestResult, 是否有 CSV)。
    """
    sub = IngestResult()
    txns, bill_dues, had_csv = [], [], False
    for att in msg.attachments:
        ref = f"{msg.msg_id}:{att.filename}"
        if _is_pdf(att):
            t, bill_due = ingest_pdf_bytes(att.data, att.filename, msg.msg_id, ref,
                                           passwords, None, imported_at,
                                           category_rules, sub)
            txns.extend(t)
            if bill_due:
                bill_dues.append(bill_due)
        elif att.filename.lower().endswith(".csv"):
            had_csv = True
            txns.extend(ingest_csv_bytes(att.data, att.filename, msg.msg_id, ref,
                                         None, imported_at, category_rules, sub))
        else:
            continue
        sub.files += 1
    return txns, bill_dues, sub, had_csv


def run_pipeline(service, cfg: Config, imported_at: str,
                 query: str | None = None, on_progress=None,
                 force_refetch: bool = False) -> RunResult:
    result = RunResult()
    rules = load_rules(cfg.rules_path)
    passwords = load_passwords(cfg.passwords_path)
    category_rules = load_categories(cfg.categories_path)
    # 已經處理過的信不用再重抓/重解密 PDF——內容不變，重解一定得到同樣的
    # txn_id，只是白花 API/CPU。force_refetch 給「剛改過 parser、要讓舊信重跑」時用。
    #
    # 「處理過」不能只看 CSV 目前還存不存在對應的 source_msg_id：reconcile
    # 會把每日通知信跟月結單的重複記錄合併、丟掉其中一筆，被丟掉那筆的
    # source_msg_id 就從 CSV 消失了，若還是只靠 CSV 判斷，下次 run 會把它當
    # 新信重抓一次，等於把 reconcile 併掉的重複又整批加回來。所以額外維護
    # 一份只增不減的 seen_msg_ids 登記檔（seen_ids_path），不受 reconcile
    # 影響。
    # 不管是不是 force_refetch 都先把既有登記檔讀出來——force_refetch 只是
    # 「這次不要拿它來跳過重抓」，寫回時仍要跟舊記錄合併，不能整份覆蓋掉。
    # 上次只處理到一半的信（PDF 解不開、抽字失敗）即使本文那幾筆已經在 CSV
    # 裡，也要排除在「已處理」之外，補上密碼／環境後才能重跑補回。
    registry = read_registry(cfg.seen_ids_path)
    existing_registry_ids = registry.seen
    existing_retry_ids = registry.retry
    existing_unparsed = registry.unparsed
    fingerprint = parser_fingerprint()
    # parser 當時解不出東西、且 parser 之後沒改過的信，重抓也只會得到一樣的結果
    unchanged_unparsed = {m for m, fp in existing_unparsed.items() if fp == fingerprint}
    seen_msg_ids: set[str] = (
        set() if force_refetch
        else ({t.source_msg_id for t in read_transactions(cfg.csv_path)}
              | existing_registry_ids | unchanged_unparsed) - existing_retry_ids
    )
    new_unparsed: dict[str, str] = {}
    newly_processed_ids: set[str] = set()
    retry_ids: set[str] = set()

    import_ids: list[str] = []
    if cfg.import_label:
        import_ids, missing = list_labels_messages(service, [cfg.import_label], query=query)
        result.missing_labels.extend(missing)
    ids, missing = list_labels_messages(service, cfg.label_names(), query=query)
    result.missing_labels.extend(missing)
    import_set = set(import_ids)
    ids = [m for m in ids if m not in import_set]   # 兩邊都有的信只走匯入那條
    result.fetched = len(ids) + len(import_ids)

    collected = []
    bill_dues = []
    imported_csv = False
    total = result.fetched
    for i, mid in enumerate(import_ids):
        if on_progress:
            on_progress(i + 1, total)
        result.matched += 1
        if mid in seen_msg_ids:
            result.already_seen += 1
            continue
        try:
            msg = extract_message(service, mid)
        except Exception as e:
            result.fetch_failures.append(f"{mid}: fetch: {e}")
            continue
        txns, dues, sub, had_csv = _import_attachments(msg, passwords, imported_at,
                                                       category_rules)
        collected.extend(txns)
        bill_dues.extend(dues)
        imported_csv = imported_csv or had_csv
        result.imported.extend(sub.parsed)
        result.unlock_failures.extend(f"{mid}:{x}" for x in sub.unlock_failures)
        result.parse_warnings.extend(f"{mid}:{x}" for x in sub.parse_warnings)
        result.needs_parser.extend(f"{msg.sender} | {msg.subject} | {x}"
                                   for x in sub.needs_parser)
        if sub.files == 0:
            result.needs_parser.append(
                f"{msg.sender} | {msg.subject}（沒有可匯入的 PDF/CSV 附件）")
        if sub.retryable:
            retry_ids.add(mid)
        elif txns or dues:
            newly_processed_ids.add(mid)
        else:
            new_unparsed[mid] = fingerprint

    for i, mid in enumerate(ids, start=len(import_ids)):
        if on_progress:
            on_progress(i + 1, total)
        # 先用輕量標頭過濾，不符合規則的就不下載完整信件與附件。
        # 單封信抓取失敗(網路逾時等)不應中斷整批 — 記錄後跳過，重跑可靠去重補回。
        try:
            sender, subject, _ = get_message_metadata(service, mid)
        except Exception as e:
            result.fetch_failures.append(f"{mid}: metadata: {e}")
            continue
        if not matches(rules, sender, subject):
            continue
        result.matched += 1
        if mid in seen_msg_ids:
            result.already_seen += 1
            continue

        try:
            msg = extract_message(service, mid)
        except Exception as e:
            result.fetch_failures.append(f"{mid}: fetch: {e}")
            continue
        parser = get_parser(msg)
        if parser is None:
            result.needs_parser.append(f"{msg.sender} | {msg.subject}")
            continue

        pdf_texts = []
        had_unlock_failure = False
        for att in msg.attachments:
            if not _is_pdf(att):
                continue
            res = unlock_pdf(att.data, passwords)
            if not res.ok:
                result.unlock_failures.append(f"{msg.msg_id}:{att.filename}")
                had_unlock_failure = True
                continue
            try:
                pdf_texts.append(parser.extract_pdf_text(res.data))
            except Exception as e:  # 如 OCR 的 tesseract 沒裝：記錄後繼續處理其他信
                result.unlock_failures.append(
                    f"{msg.msg_id}:{att.filename} 文字抽取失敗: {e}")
                had_unlock_failure = True

        source_type = "pdf" if pdf_texts else "email_body"
        raws = parser.parse(msg, pdf_texts)
        for seq, raw in enumerate(raws):
            txn = normalize(raw, msg.msg_id, source_type, msg.msg_id, imported_at, seq=seq)
            if txn.amount == "":
                result.parse_warnings.append(f"{msg.msg_id}:{raw.merchant_raw}")
            collected.append(apply_categories(txn, category_rules))

        bill_due = parser.parse_bill(msg, pdf_texts)
        if bill_due:
            bill_dues.append(bill_due)

        # 解密／抽字失敗的信不算處理完成——補上密碼後應該要能重跑補回（跟
        # fetch_failures 的「重跑可補回」一致）。parser 一筆都解不出來的信也
        # 不算處理完成：多半是版型還沒支援。連同 parser 指紋另外記一份，parser
        # 沒變就不重抓，一改就自動補回。
        if had_unlock_failure:
            retry_ids.add(mid)
        elif raws or bill_due:
            newly_processed_ids.add(mid)
        else:
            new_unparsed[mid] = fingerprint

    if imported_csv:
        rekey_csv_rows(cfg.csv_path)  # 同 ingest：舊版 seq 編號的 CSV 列先轉新編號再去重
    collected = apply_display_names(
        collected, load_aliases(aliases_path_for(cfg.categories_path)))
    append_res = append_transactions(cfg.csv_path, collected)
    result.added = append_res.added
    result.skipped = append_res.skipped
    rebuild_sqlite(cfg.csv_path, cfg.db_path)
    append_bills(cfg.bills_path, bill_dues)
    final_retry = (existing_retry_ids - newly_processed_ids) | retry_ids
    final_unparsed = {m: fp for m, fp in existing_unparsed.items()
                      if m not in newly_processed_ids and m not in retry_ids}
    final_unparsed.update(new_unparsed)
    if (newly_processed_ids or final_retry != existing_retry_ids
            or final_unparsed != existing_unparsed):
        write_seen_ids(cfg.seen_ids_path, existing_registry_ids | newly_processed_ids,
                       retry=final_retry, unparsed=final_unparsed)
    return result
