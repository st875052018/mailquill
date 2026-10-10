"""中國信託 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.ctbc import CtbcParser

# 模仿真實電子帳單 pdfplumber 抽出的版型：中文商家名被圖片化抽不到文字
# （merchant 留空），英數字商家（如 eTag）殘留文字；金額/末四碼/消費地皆為假資料
_STATEMENT = """\
115 08 1 / 3
X1004192
2 / 3
115/08/17 4,252 7,915 0 4,252 1,476 +6,439
115/08/05 -4,252
115/07/15 115/07/20 eTag ABC-0000 15 9999 TW
115/07/16 115/07/20 190 9999 TW
115/07/27 115/07/29 1,000 9999 TW
115/07/27 115/07/29 1,000 9999 TW
115/07/28 115/07/30 -1,000 9999 TW
115/08/04 115/08/07 eTag ABC-0000 15 9999 TW
X1004192
3 / 3
"""


def _msg(sender="ebill@estats.ctbcbank.com"):
    return EmailMessage(msg_id="m1", sender=sender, subject="中國信託信用卡電子帳單 11508",
                        date="", body_text="", attachments=[])


def test_ctbc_parser_matches_sender():
    assert isinstance(get_parser(_msg()), CtbcParser)
    assert not isinstance(get_parser(_msg("a@example-bank.test")), CtbcParser)


def test_ctbc_skips_summary_and_payment_lines():
    txns = CtbcParser().parse(_msg(), [_STATEMENT])
    # 略過只有單一日期的總表／繳款彙總列；其餘 6 筆消費（含 2 筆同日同店同額）
    assert len(txns) == 6


def test_ctbc_fields_and_blank_merchant():
    txns = CtbcParser().parse(_msg(), [_STATEMENT])
    by_amount = {t.amount: t for t in txns if t.merchant_raw == ""}

    blank = by_amount["190"]
    assert blank.date == "2026-07-16"       # 民國115/07/16 → 西元
    assert blank.post_date == "2026-07-20"
    assert blank.account_last4 == "9999"
    assert blank.bank == "CTBC"
    assert blank.currency == "TWD"
    assert blank.merchant_raw == ""          # 圖片型商家名抽不到文字

    refund = by_amount["-1,000"]
    assert refund.merchant_raw == ""

    with_merchant = [t for t in txns if t.merchant_raw == "eTag ABC-0000"]
    assert len(with_merchant) == 2           # 英數字商家仍能抽到文字
    assert with_merchant[0].amount == "15"

    dup = [t for t in txns if t.amount == "1,000" and t.merchant_raw == ""]
    assert len(dup) == 2                     # 同日同店同額的兩筆真實交易都保留


# 模仿真實彙總頁繳費聯的版型：中文標籤是圖片抽不到文字，只剩「$金額」緊接
# 一段英數字條碼字串（模擬真實條碼數字，非真實帳號/金額），依固定順序（應
# 繳總額在前、最低應繳在後）出現，見 ctbc.py 的 `_BARCODE_AMOUNTS` 說明。
_BILL_SUMMARY = """\
115 09
$ 6,439
150918000006439( )
501231C02( )
$ 1,000
1509BX000001000( )
9999983815381349( )
"""


def test_ctbc_parse_bill_extracts_total_and_min_due():
    bill = CtbcParser().parse_bill(_msg(), [_BILL_SUMMARY])
    assert bill.bank == "CTBC"
    assert bill.period == "2026-08"
    assert bill.amount_due == "6439"
    assert bill.min_due == "1000"
    assert bill.source_msg_id == "m1"


def test_ctbc_parse_bill_no_subject_period_returns_none():
    msg = EmailMessage(msg_id="m1", sender="ebill@estats.ctbcbank.com",
                       subject="中國信託信用卡電子帳單", date="",
                       body_text="", attachments=[])
    assert CtbcParser().parse_bill(msg, [_BILL_SUMMARY]) is None


def test_ctbc_parse_bill_fewer_than_two_amounts_returns_none():
    text = "$ 6,439\n150918000006439( )\n"
    assert CtbcParser().parse_bill(_msg(), [text]) is None


def test_ctbc_parse_bill_amounts_out_of_order_returns_none():
    # 應繳總額理論上一定 ≥ 最低應繳；順序顛倒視為版型跑掉，不硬猜
    text = "$ 1,000\n150918000001000( )\n$ 6,439\n1509BX000006439( )\n"
    assert CtbcParser().parse_bill(_msg(), [text]) is None


# 經 OCR（hsbc_ocr_text.CTBC_LAYOUT）後的版型：商家小圖辨識結果接在消費地後面
_OCR_STATEMENT = """\
115/07/15 115/07/20 eTag ABC-0000 15 9999 TW 臨停
115/07/16 115/07/20 190 9999 TW 連支＊統一超商
115/07/17 115/07/20 40 9999 TW
"""


def test_ctbc_uses_ocr_merchant_after_region():
    txns = CtbcParser().parse(_msg(), [_OCR_STATEMENT])
    by_amount = {t.amount: t for t in txns}
    assert by_amount["190"].merchant_raw == "連支＊統一超商"   # 圖片商家由 OCR 補回
    assert by_amount["40"].merchant_raw == ""                # OCR 也沒認出來就維持空白
    # 本來就有真文字商家的列，維持原值、不接 OCR 雜訊（保持既有 txn_id 穩定）
    assert by_amount["15"].merchant_raw == "eTag ABC-0000"


def test_ctbc_extract_pdf_text_uses_ctbc_layout(monkeypatch):
    import mailquill.hsbc_ocr_text as ocr
    captured = {}

    def fake(pdf_bytes, resolution=400, layout=None):
        captured["layout"] = layout
        return "ok"

    monkeypatch.setattr(ocr, "extract_pdf_text_ocr", fake)
    assert CtbcParser().extract_pdf_text(b"%PDF") == "ok"
    assert captured["layout"] is ocr.CTBC_LAYOUT
