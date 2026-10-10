"""滙豐(HSBC) parser 測試（完全合成資料，非真實帳單）。

輸入文字模仿 `mailquill.hsbc_ocr_text.extract_pdf_text_ocr` 的輸出版型：
商家名稱已經 OCR 接在該列文字後面，parser 本身只需要用正則比對，不碰 OCR。
"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.hsbc import HSBCParser

# 還款轉帳、國外交易手續費、海外原始消費(含 OCR 商家)、海外原始消費(OCR 沒認出商家)
_STATEMENT = """\
1/2
HSBC Traveller's Infinite Credit Card
08/05 08/06 -15,335 他行提款機繳款(轉帳 )
07/19 07/22 6 國外交易手續費SOME SHOP
07/19 07/22 JPN JPY 2,112.00 07/19 422 SOME SHOP
07/20 07/22 JPN JPY 15,292.00 07/20 3,057
"""


def _msg(sender="HSBC Card Services <cards@estatements.hsbc.com.tw>",
         subject="115年08月滙豐旅人無限卡信用卡帳單"):
    return EmailMessage("m1", sender, subject, "", "", [])


def test_hsbc_parser_matches():
    assert isinstance(get_parser(_msg()), HSBCParser)


def test_hsbc_parses_fee_and_detail_lines():
    txns = HSBCParser().parse(_msg(), [_STATEMENT])
    # 還款轉帳略過；其餘 3 筆(1 手續費 + 2 海外消費)
    assert len(txns) == 3
    assert all(t.bank == "HSBC" for t in txns)
    assert not any("他行提款機" in t.merchant_raw for t in txns)
    total = sum(int(t.amount.replace(",", "")) for t in txns)
    assert total == 3485          # 6 + 422 + 3,057


def test_hsbc_fee_line_keeps_label_prefix():
    by_amount = {t.amount: t for t in HSBCParser().parse(_msg(), [_STATEMENT])}
    fee = by_amount["6"]
    assert fee.merchant_raw == "國外交易手續費SOME SHOP"
    assert fee.date == "2026-07-19"
    assert fee.post_date == "2026-07-22"


def test_hsbc_detail_line_amount_and_merchant():
    by_amount = {t.amount: t for t in HSBCParser().parse(_msg(), [_STATEMENT])}
    t = by_amount["422"]
    assert t.merchant_raw == "SOME SHOP"    # OCR 商家
    assert t.currency == "TWD"              # 記最終台幣折算金額，非外幣 2,112.00
    assert t.date == "2026-07-19"


def test_hsbc_detail_line_missing_ocr_merchant_is_blank_not_dropped():
    by_amount = {t.amount: t for t in HSBCParser().parse(_msg(), [_STATEMENT])}
    t = by_amount["3,057"]
    assert t.merchant_raw == ""             # OCR 沒認出來，留空而非丟棄該筆
    assert t.amount == "3,057"


def test_hsbc_no_period_returns_empty():
    m = _msg(subject="滙豐旅人無限卡信用卡帳單")   # 主旨無期別
    assert HSBCParser().parse(m, ["07/19 07/22 6 國外交易手續費X\n"]) == []


def test_hsbc_extract_pdf_text_delegates_to_ocr_module(monkeypatch):
    import mailquill.hsbc_ocr_text as ocr_mod
    calls = []
    monkeypatch.setattr(ocr_mod, "extract_pdf_text_ocr", lambda data: calls.append(data) or "OCR TEXT")
    result = HSBCParser().extract_pdf_text(b"%PDF-fake")
    assert result == "OCR TEXT"
    assert calls == [b"%PDF-fake"]


def test_hsbc_parse_bill_delegates_to_hsbc_bill_module(monkeypatch):
    import mailquill.hsbc_bill as bill_mod
    calls = []
    monkeypatch.setattr(bill_mod, "extract_hsbc_bill_due",
                        lambda msg: calls.append(msg) or "BILL")
    msg = _msg()
    result = HSBCParser().parse_bill(msg, [])
    assert result == "BILL"
    assert calls == [msg]
