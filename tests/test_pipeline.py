import io

import pikepdf
from reportlab.pdfgen import canvas

from mailquill.config import Config
from mailquill.rules import Rules
from mailquill.gmail_message import EmailMessage, Attachment
from mailquill.store import read_transactions
import mailquill.pipeline as pipeline


def _encrypted_pdf(lines, password):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 750
    for ln in lines:
        c.drawString(72, y, ln)
        y -= 20
    c.save()
    buf.seek(0)
    src = pikepdf.open(buf)
    enc = io.BytesIO()
    src.save(enc, encryption=pikepdf.Encryption(owner=password, user=password))
    return enc.getvalue()


def _cfg(tmp_path):
    # 寫一份只含一條分類規則的 categories.yaml
    cats = tmp_path / "categories.yaml"
    cats.write_text('rules:\n  - {keyword: "PXMART", l1: "Food", l2: "Groceries"}\n',
                    encoding="utf-8")
    pwd = tmp_path / "passwords.txt"
    pwd.write_text("PW1\n", encoding="utf-8")
    return Config(
        label="財務",
        csv_path=str(tmp_path / "t.csv"),
        db_path=str(tmp_path / "t.db"),
        categories_path=str(cats),
        rules_path=str(tmp_path / "rules.yaml"),
        passwords_path=str(pwd),
        seen_ids_path=str(tmp_path / "seen_msg_ids.txt"),
    )


def _setup_common(monkeypatch, tmp_path, messages, parser_for):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=["@example-bank.test", "@unknown.test"],
                                           subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda service, labels, query=None: (list(messages.keys()), []))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda service, mid: (messages[mid].sender,
                                              messages[mid].subject, messages[mid].date))
    monkeypatch.setattr(pipeline, "extract_message",
                        lambda service, mid: messages[mid])
    monkeypatch.setattr(pipeline, "get_parser", parser_for)
    return cfg


def test_run_pipeline_parses_encrypted_pdf_and_writes_csv(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.fetched == 1
    assert result.matched == 1
    assert result.added == 1
    assert result.needs_parser == []
    assert result.unlock_failures == []

    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1
    assert rows[0].merchant_norm == "PXMART"
    assert rows[0].amount == "1200"
    assert rows[0].category_l1 == "Food"
    assert rows[0].source_type == "pdf"


def test_run_pipeline_records_needs_parser(monkeypatch, tmp_path):
    msg = EmailMessage(msg_id="m1", sender="x@unknown.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg}, parser_for=lambda m: None)
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.matched == 1
    assert result.added == 0
    assert len(result.needs_parser) == 1
    assert "unknown.test" in result.needs_parser[0]


def test_run_pipeline_records_unlock_failure(monkeypatch, tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "RIGHT")  # 清單裡只有 PW1
    msg = EmailMessage(
        msg_id="m1", sender="ebill@example-bank.test", subject="帳單", date="",
        body_text="", attachments=[Attachment("stmt.pdf", "application/pdf", pdf)],
    )
    from mailquill.parsers.example_bank import ExampleBankParser
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert len(result.unlock_failures) == 1
    assert result.added == 0  # PDF 解不開、body 也無交易


def test_run_pipeline_email_body_source_type(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.added == 1
    rows = read_transactions(cfg.csv_path)
    assert rows[0].source_type == "email_body"


def test_run_pipeline_records_parse_warning_and_keeps_row(monkeypatch, tmp_path):
    from mailquill.raw_txn import RawTxn
    from mailquill.parsers.base import Parser

    class _NoAmountParser(Parser):
        bank = "B"
        def matches(self, msg):
            return True
        def parse(self, msg, pdf_texts):
            return [RawTxn(bank="B", date="2026-06-01", amount="無金額",
                           merchant_raw="商家X")]

    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: _NoAmountParser())
    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert len(result.parse_warnings) == 1
    assert result.added == 1  # 警告不丟資料：原始列仍寫入
    rows = read_transactions(cfg.csv_path)
    assert rows[0].amount == ""  # 解不出金額存空字串，原始 merchant 保留
    assert rows[0].merchant_raw == "商家X"


def test_run_pipeline_writes_bill_due_from_parse_bill(monkeypatch, tmp_path):
    from mailquill.bill_due import BillDue
    from mailquill.bills_store import read_bills
    from mailquill.parsers.base import Parser

    class _BillParser(Parser):
        bank = "B"
        def matches(self, msg):
            return True
        def parse(self, msg, pdf_texts):
            return []
        def parse_bill(self, msg, pdf_texts):
            return BillDue(bank="HSBC", period="2026-08", amount_due="19711",
                           min_due="2233", source_msg_id=msg.msg_id)

    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg}, parser_for=lambda m: _BillParser())
    cfg.bills_path = str(tmp_path / "bills.csv")

    pipeline.run_pipeline(service=object(), cfg=cfg, imported_at="2026-06-24T10:00:00")

    bills = read_bills(cfg.bills_path)
    assert len(bills) == 1
    assert bills[0] == BillDue(bank="HSBC", period="2026-08", amount_due="19711",
                               min_due="2233", source_msg_id="m1")


def test_run_pipeline_reports_missing_labels(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    # 一個 label 找到（回傳 m1），另一個找不到
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda service, labels, query=None: (["m1"], ["不存在的標籤"]))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda service, mid: (msg.sender, msg.subject, msg.date))
    monkeypatch.setattr(pipeline, "extract_message", lambda service, mid: msg)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")
    assert result.missing_labels == ["不存在的標籤"]
    assert result.added == 1  # 找得到的 label 照常處理


def test_run_pipeline_threads_query(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    captured = {}

    def fake_list(service, labels, query=None):
        captured["query"] = query
        return [], []

    monkeypatch.setattr(pipeline, "load_rules",
                        lambda path: Rules(senders=[], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages", fake_list)
    pipeline.run_pipeline(service=object(), cfg=cfg,
                          imported_at="2026-06-24T10:00:00", query="after:2026/01/01")
    assert captured["query"] == "after:2026/01/01"


def test_run_pipeline_reports_progress(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msgs = {
        "m1": EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                           date="", body_text="2026-06-01 PXMART 1,200", attachments=[]),
        "m2": EmailMessage(msg_id="m2", sender="ebill@example-bank.test", subject="帳單",
                           date="", body_text="2026-06-02 PXMART 50", attachments=[]),
    }
    cfg = _setup_common(monkeypatch, tmp_path, msgs,
                        parser_for=lambda m: ExampleBankParser())
    seen = []
    pipeline.run_pipeline(service=object(), cfg=cfg, imported_at="2026-06-24T10:00:00",
                          on_progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_run_pipeline_skips_fetch_failure_and_continues(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    good = EmailMessage("ok", "ebill@example-bank.test", "帳單", "",
                        "2026-06-01 PXMART 1,200", [])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda p: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda s, l, query=None: (["bad", "ok"], []))

    def meta(s, mid):
        if mid == "bad":
            raise TimeoutError("read timed out")
        return (good.sender, good.subject, good.date)

    monkeypatch.setattr(pipeline, "get_message_metadata", meta)
    monkeypatch.setattr(pipeline, "extract_message", lambda s, mid: good)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    r = pipeline.run_pipeline(object(), cfg, "2026-06-24T10:00:00")
    assert r.added == 1                      # 壞的一封被跳過，好的照常寫入
    assert len(r.fetch_failures) == 1
    assert "bad" in r.fetch_failures[0]


def test_run_pipeline_skips_refetch_for_already_seen_message(monkeypatch, tmp_path):
    # 已在 CSV 裡的信（source_msg_id 已存在）不該再呼叫 extract_message，省下重抓/重解密。
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    first = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:00:00")
    assert first.added == 1 and first.already_seen == 0

    def boom(service, mid):
        raise AssertionError("已入帳的信不該再被 extract_message")

    monkeypatch.setattr(pipeline, "extract_message", boom)
    second = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:05:00")
    assert second.matched == 1
    assert second.already_seen == 1
    assert second.added == 0

    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1  # 沒有重複寫入


def test_run_pipeline_force_refetch_bypasses_seen_cache(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    pipeline.run_pipeline(service=object(), cfg=cfg, imported_at="2026-06-24T10:00:00")
    second = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:05:00", force_refetch=True)
    assert second.already_seen == 0
    assert second.matched == 1
    assert second.added == 0        # 內容沒變，txn_id 一樣 → append 時仍視為重複
    assert second.skipped == 1      # 但這次是「重抓後在 append 才發現重複」，不是跳過重抓


def test_run_pipeline_does_not_reprocess_message_dropped_by_reconcile(monkeypatch, tmp_path):
    # reconcile 把某筆交易從 CSV 裡丟掉後，對應的 source_msg_id 就不在 CSV
    # 裡了；若 run 只看 CSV 判斷「抓過沒」，會把這封信當新信重抓一次，等於
    # 把 reconcile 併掉的重複又加回來。seen_ids_path 登記檔要擋住這件事。
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-01 PXMART 1,200", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    first = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:00:00")
    assert first.added == 1

    # 模擬 reconcile：把這筆交易從 CSV 裡拿掉（source_msg_id 因此從 CSV 消失）。
    from mailquill.store import write_transactions_atomic
    write_transactions_atomic(cfg.csv_path, [])

    def boom(service, mid):
        raise AssertionError("已登記過的信不該再被 extract_message")

    monkeypatch.setattr(pipeline, "extract_message", boom)
    second = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:05:00")
    assert second.matched == 1
    assert second.already_seen == 1
    assert second.added == 0

    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 0  # 沒有被重新加回來


def test_run_pipeline_skips_extract_failure(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage("m1", "ebill@example-bank.test", "帳單", "", "", [])
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(pipeline, "load_rules",
                        lambda p: Rules(senders=["@example-bank.test"], subject_keywords=[]))
    monkeypatch.setattr(pipeline, "list_labels_messages",
                        lambda s, l, query=None: (["m1"], []))
    monkeypatch.setattr(pipeline, "get_message_metadata",
                        lambda s, mid: (msg.sender, msg.subject, msg.date))

    def boom(s, mid):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(pipeline, "extract_message", boom)
    monkeypatch.setattr(pipeline, "get_parser", lambda m: ExampleBankParser())

    r = pipeline.run_pipeline(object(), cfg, "2026-06-24T10:00:00")
    assert r.matched == 1
    assert r.added == 0
    assert len(r.fetch_failures) == 1 and "fetch" in r.fetch_failures[0]


def test_run_pipeline_ocr_failure_is_recorded_and_other_mail_still_saved(monkeypatch, tmp_path):
    from mailquill.parsers.example_bank import ExampleBankParser

    class OcrBoom(ExampleBankParser):
        def extract_pdf_text(self, pdf_bytes):
            raise RuntimeError("tesseract not installed")

    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    bad = EmailMessage(msg_id="bad", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="",
                       attachments=[Attachment("s.pdf", "application/pdf", pdf)])
    good = EmailMessage(msg_id="good", sender="ebill@example-bank.test", subject="帳單",
                        date="", body_text="2026-06-02 PXMART 300", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"bad": bad, "good": good},
                        parser_for=lambda m: OcrBoom() if m.msg_id == "bad" else ExampleBankParser())

    result = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:00:00")

    assert result.added == 1                       # good 那封照樣入帳
    assert any("tesseract" in f for f in result.unlock_failures)
    # bad 沒處理完成 → 不能進 seen 登記，修好環境後重跑要能補回
    from mailquill.store import read_seen_ids
    assert read_seen_ids(cfg.seen_ids_path) == {"good"}


def test_run_pipeline_zero_transaction_mail_is_retried_only_after_parser_changes(
        monkeypatch, tmp_path):
    # parser 暫時解不出東西（版型沒支援）的信：parser 沒變就不用每次 run 都重抓，
    # 但 parser 程式碼一改（指紋變了）就要自動重試，不必手動 --refetch。
    from mailquill.parsers.example_bank import ExampleBankParser
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="unrecognised layout", attachments=[])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())
    monkeypatch.setattr(pipeline, "parser_fingerprint", lambda: "v1")

    first = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:00:00")
    assert first.added == 0

    unchanged = pipeline.run_pipeline(service=object(), cfg=cfg,
                                      imported_at="2026-06-24T10:02:00")
    assert unchanged.already_seen == 1               # parser 沒變：不重抓

    msg.body_text = "2026-06-01 PXMART 1,200"       # parser 修好了
    monkeypatch.setattr(pipeline, "parser_fingerprint", lambda: "v2")
    second = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:05:00")
    assert second.already_seen == 0
    assert second.added == 1

    third = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:10:00")
    assert third.already_seen == 1                   # 解出來了就算處理完成


def test_run_pipeline_retries_mail_whose_pdf_was_locked_even_if_body_was_saved(
        monkeypatch, tmp_path):
    # 本文先入帳、PDF 因密碼錯誤沒解開：補上密碼後重跑要能把 PDF 內容補回，
    # 不能因為 CSV 已有該信的 source_msg_id 就被當成已處理。
    from mailquill.parsers.example_bank import ExampleBankParser
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "SECRET2")
    msg = EmailMessage(msg_id="m1", sender="ebill@example-bank.test", subject="帳單",
                       date="", body_text="2026-06-05 PXMART 50",
                       attachments=[Attachment("s.pdf", "application/pdf", pdf)])
    cfg = _setup_common(monkeypatch, tmp_path, {"m1": msg},
                        parser_for=lambda m: ExampleBankParser())

    first = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:00:00")
    assert first.added == 1 and len(first.unlock_failures) == 1

    with open(cfg.passwords_path, "a", encoding="utf-8") as f:
        f.write("SECRET2\n")
    second = pipeline.run_pipeline(service=object(), cfg=cfg,
                                   imported_at="2026-06-24T10:05:00")
    assert second.already_seen == 0
    assert second.unlock_failures == []
    assert second.added == 1                        # PDF 那筆補進來
    # 這次完整處理過了，第三次才會被跳過
    third = pipeline.run_pipeline(service=object(), cfg=cfg,
                                  imported_at="2026-06-24T10:10:00")
    assert third.already_seen == 1
