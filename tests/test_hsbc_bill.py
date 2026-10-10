"""HSBC 帳單應繳金額擷取測試（完全合成資料，非真實帳單）。

輸入 HTML 模仿真實 `eBarCodeFullPay.html`/`eBarCodeMinPay.html` 附件裡跟
金額/月份相關的部分（省略樣式/條碼圖片等無關內容）。
"""
from mailquill.gmail_message import Attachment, EmailMessage
from mailquill.hsbc_bill import extract_hsbc_bill_due

_FULL_PAY_HTML = """\
<div class="pay_title">全額繳清</div>
張O凱 先生/女士 付款月份 2026年08月
<div class="amount">應繳總額：19,711</div>
"""

_MIN_PAY_HTML = """\
<div class="pay_title">最低應繳</div>
張O凱 先生/女士 付款月份 2026年08月
<div class="amount">最低應繳：2,233</div>
"""


def _msg(attachments):
    return EmailMessage("m1", "HSBC Card Services <cards@estatements.hsbc.com.tw>",
                        "115年08月滙豐旅人無限卡信用卡帳單", "", "", attachments)


def test_extracts_amount_due_min_due_and_period():
    msg = _msg([
        Attachment("eBarCodeFullPay.html", "text/html", _FULL_PAY_HTML.encode("utf-8")),
        Attachment("eBarCodeMinPay.html", "text/html", _MIN_PAY_HTML.encode("utf-8")),
    ])
    bill = extract_hsbc_bill_due(msg)
    assert bill.bank == "HSBC"
    assert bill.period == "2026-08"
    assert bill.amount_due == "19711"
    assert bill.min_due == "2233"
    assert bill.source_msg_id == "m1"


def test_missing_min_pay_attachment_leaves_min_due_blank():
    msg = _msg([Attachment("eBarCodeFullPay.html", "text/html", _FULL_PAY_HTML.encode("utf-8"))])
    bill = extract_hsbc_bill_due(msg)
    assert bill.amount_due == "19711"
    assert bill.min_due == ""


def test_no_full_pay_attachment_returns_none():
    msg = _msg([Attachment("eBarCodeMinPay.html", "text/html", _MIN_PAY_HTML.encode("utf-8"))])
    assert extract_hsbc_bill_due(msg) is None


def test_no_attachments_returns_none():
    assert extract_hsbc_bill_due(_msg([])) is None


def test_full_pay_missing_amount_returns_none():
    html = "張O凱 先生/女士 付款月份 2026年08月\n<div class=\"amount\"></div>"
    msg = _msg([Attachment("eBarCodeFullPay.html", "text/html", html.encode("utf-8"))])
    assert extract_hsbc_bill_due(msg) is None
