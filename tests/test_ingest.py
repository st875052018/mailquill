"""本地 PDF 匯入測試。用 reportlab+pikepdf 產生加密的合成帳單。"""
import io

import pikepdf
from reportlab.pdfgen import canvas

from mailquill.config import Config
from mailquill.store import read_transactions
from mailquill.bills_store import read_bills
from mailquill.ingest import ingest_paths, collect_pdfs, collect_csvs, select_parser


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
    out = io.BytesIO()
    src.save(out, encryption=pikepdf.Encryption(owner=password, user=password))
    return out.getvalue()


def _cfg(tmp_path):
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
        passwords_path=str(pwd),
        bills_path=str(tmp_path / "bills.csv"),
    )


def test_collect_pdfs_file_and_dir(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.PDF").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.pdf").write_bytes(b"x")
    found = collect_pdfs([str(tmp_path)])
    assert sum(p.lower().endswith(".pdf") for p in found) == 3
    assert all(not p.endswith(".txt") for p in found)


def test_select_parser_picks_by_content():
    # example-bank 格式：YYYY-MM-DD 商家 金額
    parser, txns = select_parser("2026-06-01 PXMART 1,200\n2026-06-02 UBER 250\n")
    assert parser is not None
    assert len(txns) == 2


def test_ingest_processes_encrypted_pdf(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.files == 1
    assert r.added == 1
    assert len(r.parsed) == 1
    rows = read_transactions(cfg.csv_path)
    assert rows[0].merchant_norm == "PXMART"
    assert rows[0].category_l1 == "Food"
    assert rows[0].source_type == "pdf"


def test_ingest_dedups_on_reingest(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    r2 = ingest_paths([str(f)], cfg, "2026-06-25T11:00:00")   # 再匯入一次
    assert r2.added == 0 and r2.skipped == 1                  # 去重，不重複入帳
    assert len(read_transactions(cfg.csv_path)) == 1


def test_ingest_unlock_failure(tmp_path):
    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "WRONG")  # passwords.txt 只有 PW1
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.added == 0
    assert len(r.unlock_failures) == 1


def test_collect_csvs_file_and_dir(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"x")
    (tmp_path / "b.CSV").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    found = collect_csvs([str(tmp_path)])
    assert sum(p.lower().endswith(".csv") for p in found) == 2


def test_ingest_processes_amex_csv(tmp_path):
    csv_text = (
        "Date,Description,Card Member,Account #,Amount,Extended Details,"
        "Appears On Your Statement As,Address,City/State,Zip Code,Country,"
        "Reference,Category\n"
        '07/27/2026,PXMART TEST STORE,JANE DOE,-73006,15.50,"",PXMART,,,,,,Merchandise\n'
        '07/11/2026,AUTOPAY PAYMENT - THANK YOU,JANE DOE,-73006,-213.10,"",,,,,,,\n'
    )
    f = tmp_path / "activity.csv"
    f.write_text(csv_text, encoding="utf-8")
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")

    assert r.files == 1
    assert r.added == 1  # 繳款列被略過，不計入
    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1
    assert rows[0].bank == "Amex"
    assert rows[0].currency == "USD"
    assert rows[0].source_type == "csv"


def test_ingest_processes_ctbc_csv(tmp_path):
    csv_text = (
        "帳單明細查詢\n資料時間：2026/08/17 23:01:13,單位：元\n\n"
        "消費日,入帳起息日,摘要,幣別,消費地金額,外幣折算日,消費地,末四碼,新臺幣金額\n"
        "2026/06/18,2026/06/22,連支＊統一超商,,,,TW,9646,75\n"
        '2026/07/06,2026/07/07,本行扣繳　　　　　　　,,,,,,"-4,983"\n'
    )
    f = tmp_path / "帳單明細查詢_test.csv"
    f.write_bytes(csv_text.encode("big5"))
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")

    assert r.files == 1
    assert r.added == 1  # 扣繳列被略過，不計入
    rows = read_transactions(cfg.csv_path)
    assert len(rows) == 1
    assert rows[0].bank == "CTBC"
    assert rows[0].currency == "TWD"
    assert rows[0].source_type == "csv"


def test_ingest_needs_parser(tmp_path):
    pdf = _encrypted_pdf(["NOT A BANK STATEMENT - no parseable lines here"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    r = ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")
    assert r.added == 0
    assert r.needs_parser == ["stmt.pdf"]


def test_ingest_writes_bill_due_from_parse_bill(tmp_path, monkeypatch):
    """驗證 ingest_paths 有把選中的 parser.parse_bill 結果寫進 bills.csv
    （見 ingest.py 對 PDF 路徑的 wiring）；用 monkeypatch 讓 ExampleBankParser
    回傳假的 BillDue，不用真的產出可 CJK 編碼的合成帳單 PDF。"""
    from mailquill.bill_due import BillDue
    from mailquill.parsers.example_bank import ExampleBankParser

    def fake_parse_bill(self, msg, pdf_texts):
        return BillDue(bank="ExampleBank", period="2026-06", amount_due="1200",
                       min_due="100", source_msg_id=msg.msg_id)

    monkeypatch.setattr(ExampleBankParser, "parse_bill", fake_parse_bill)

    pdf = _encrypted_pdf(["2026-06-01 PXMART 1,200"], "PW1")
    f = tmp_path / "stmt.pdf"
    f.write_bytes(pdf)
    cfg = _cfg(tmp_path)
    ingest_paths([str(f)], cfg, "2026-06-25T10:00:00")

    bills = read_bills(cfg.bills_path)
    assert len(bills) == 1
    assert bills[0].bank == "ExampleBank"
    assert bills[0].period == "2026-06"
    assert bills[0].amount_due == "1200"
    assert bills[0].min_due == "100"
    assert bills[0].source_msg_id == "stmt.pdf"


_AMEX_HEADER = (
    "Date,Description,Card Member,Account #,Amount,Extended Details,"
    "Appears On Your Statement As,Address,City/State,Zip Code,Country,"
    "Reference,Category\n"
)


def _amex_row(date, desc, amount):
    return f'{date},{desc},JANE DOE,-73006,{amount},"",{desc},,,,,,Merchandise\n'


def test_ingest_overlapping_csv_exports_do_not_duplicate(tmp_path):
    # 兩次匯出的日期範圍重疊、列順序不同：同一筆消費不能因為在檔案中的位置
    # 不同而被當成新交易重複入帳。
    cfg = _cfg(tmp_path)
    june = tmp_path / "june.csv"
    june.write_text(_AMEX_HEADER
                    + _amex_row("06/20/2026", "SHOP A", "10.00")
                    + _amex_row("06/10/2026", "SHOP B", "20.00"), encoding="utf-8")
    ingest_paths([str(june)], cfg, "2026-07-01T10:00:00")

    wider = tmp_path / "june_july.csv"
    wider.write_text(_AMEX_HEADER
                     + _amex_row("07/05/2026", "SHOP C", "30.00")
                     + _amex_row("06/20/2026", "SHOP A", "10.00")
                     + _amex_row("06/10/2026", "SHOP B", "20.00"), encoding="utf-8")
    r = ingest_paths([str(wider)], cfg, "2026-07-10T10:00:00")

    assert r.added == 1 and r.skipped == 2
    assert len(read_transactions(cfg.csv_path)) == 3


def test_ingest_csv_keeps_genuine_same_day_duplicates(tmp_path):
    cfg = _cfg(tmp_path)
    f = tmp_path / "dup.csv"
    f.write_text(_AMEX_HEADER
                 + _amex_row("06/20/2026", "COFFEE", "5.00")
                 + _amex_row("06/20/2026", "COFFEE", "5.00"), encoding="utf-8")
    r = ingest_paths([str(f)], cfg, "2026-07-01T10:00:00")
    assert r.added == 2
    r2 = ingest_paths([str(f)], cfg, "2026-07-02T10:00:00")
    assert r2.added == 0 and r2.skipped == 2


def _ocr_parser_cls():
    from mailquill.parsers.base import Parser
    from mailquill.raw_txn import RawTxn

    class OcrParser(Parser):
        """純文字抽不到商家，要走自己的 extract_pdf_text（OCR）才有交易。"""
        bank = "OcrBank"

        def matches(self, msg):
            return False

        def extract_pdf_text(self, pdf_bytes):
            return "OCR_MARK\n2026-06-01 OCRSHOP 500"

        def parse(self, msg, pdf_texts):
            out = []
            for block in pdf_texts:
                if "OCR_MARK" not in block:
                    continue
                for line in block.splitlines():
                    if "OCRSHOP" in line:
                        d, m, a = line.split()
                        out.append(RawTxn(date=d, post_date=d, merchant_raw=m, amount=a,
                                          currency="TWD", bank="OcrBank",
                                          account_last4="1111"))
            return out

        def parse_bill(self, msg, pdf_texts):
            return None

    return OcrParser


def test_ingest_pdf_uses_parser_own_text_extraction(monkeypatch, tmp_path):
    import mailquill.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "all_parsers", lambda: [_ocr_parser_cls()()])
    pdf = tmp_path / "ocr.pdf"
    pdf.write_bytes(_encrypted_pdf(["plain text only, no merchant"], "PW1"))
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(pdf)], cfg, "2026-06-25T10:00:00")

    assert r.needs_parser == []
    assert r.added == 1
    assert read_transactions(cfg.csv_path)[0].merchant_raw == "OCRSHOP"


def test_ingest_pdf_ocr_failure_is_reported_not_raised(monkeypatch, tmp_path):
    import mailquill.ingest as ingest_mod
    cls = _ocr_parser_cls()

    def boom(self, pdf_bytes):
        raise RuntimeError("tesseract missing")
    cls.extract_pdf_text = boom
    monkeypatch.setattr(ingest_mod, "all_parsers", lambda: [cls()])
    pdf = tmp_path / "ocr.pdf"
    pdf.write_bytes(_encrypted_pdf(["plain"], "PW1"))
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(pdf)], cfg, "2026-06-25T10:00:00")

    assert r.added == 0
    assert any("tesseract missing" in w for w in r.parse_warnings)


def test_ingest_two_overlapping_csvs_in_one_call_count_occurrences_per_file(tmp_path):
    # seq 是「單一檔案內第幾次出現」；兩個檔案各有一筆同樣的消費是同一筆，
    # 不能因為合併計數而被當成同日第二筆。
    cfg = _cfg(tmp_path)
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    for f in (a, b):
        f.write_text(_AMEX_HEADER + _amex_row("06/20/2026", "SHOP A", "10.00"),
                     encoding="utf-8")
    r = ingest_paths([str(a), str(b)], cfg, "2026-07-01T10:00:00")
    assert r.added == 1 and r.skipped == 1


def test_ingest_pdf_one_failing_ocr_candidate_does_not_block_the_next(monkeypatch, tmp_path):
    import mailquill.ingest as ingest_mod
    bad_cls = _ocr_parser_cls()

    def boom(self, pdf_bytes):
        raise RuntimeError("tesseract missing")
    bad_cls.extract_pdf_text = boom
    bad_cls.bank = "BadBank"
    monkeypatch.setattr(ingest_mod, "all_parsers",
                        lambda: [bad_cls(), _ocr_parser_cls()()])
    pdf = tmp_path / "ocr.pdf"
    pdf.write_bytes(_encrypted_pdf(["plain"], "PW1"))
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(pdf)], cfg, "2026-06-25T10:00:00")

    assert r.added == 1
    assert r.parse_warnings == []


def test_ingest_bank_filter_also_applies_to_csv(tmp_path):
    cfg = _cfg(tmp_path)
    f = tmp_path / "amex.csv"
    f.write_text(_AMEX_HEADER + _amex_row("06/20/2026", "SHOP A", "10.00"),
                 encoding="utf-8")
    r = ingest_paths([str(f)], cfg, "2026-07-01T10:00:00", bank="ctbc")
    assert r.added == 0
    assert any("amex.csv" in w for w in r.parse_warnings)


def test_ingest_parse_bill_failure_does_not_abort_the_batch(monkeypatch, tmp_path):
    import mailquill.ingest as ingest_mod
    cls = _ocr_parser_cls()

    def boom(self, msg, pdf_texts):
        raise ValueError("bad bill")
    cls.parse_bill = boom
    monkeypatch.setattr(ingest_mod, "all_parsers", lambda: [cls()])
    pdf = tmp_path / "ocr.pdf"
    pdf.write_bytes(_encrypted_pdf(["plain"], "PW1"))
    cfg = _cfg(tmp_path)

    r = ingest_paths([str(pdf)], cfg, "2026-06-25T10:00:00")

    assert r.added == 1
    assert any("bad bill" in w for w in r.parse_warnings)
