"""國泰世華 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.cathay import CathayParser

# 模仿真實月結單 pdfplumber 抽出的版型（商家/金額/卡號皆為假資料）
_STATEMENT = """\
信用卡帳單 115年6月
測試人 先生
您本月消費明細如下：
消費日 交易說明 新臺幣金額 卡號 國家 幣別
上期帳單總額 30,914
06/10 06/10 本行自動扣繳 -30,914
02/21 05/25 保險費分期 03/12 1,770 9999 TW TWD
05/23 05/26 某計程車 95 9999 TW TWD
05/24 05/27 某加油站 1,125 9999 8888 TW TWD
06/01 06/03 某海外服務 945 9999 SG TWD 945.00 06/02
06/01 06/23 國外交易手續費 -海外 14 9999
06/01 06/03 點數折抵 -283 9999 TW TWD
12/28 01/05 跨年消費測試 500 9999 TW TWD
"""


def _msg(sender="ebill <statement@pxbillrc01.cathaybk.com.tw>"):
    return EmailMessage(msg_id="m1", sender=sender, subject="信用卡電子帳單",
                        date="", body_text="", attachments=[])


def test_cathay_parser_matches_sender():
    assert isinstance(get_parser(_msg()), CathayParser)
    assert get_parser(_msg("a@example-bank.test")) is None or not isinstance(
        get_parser(_msg("a@example-bank.test")), CathayParser)


def test_cathay_parses_consumption_lines_only():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    # 略過繳款、上期帳單總額；其餘 7 筆消費
    assert len(txns) == 7
    descs = [t.merchant_raw for t in txns]
    assert "本行自動扣繳" not in " ".join(descs)


def test_cathay_roc_year_and_fields():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}

    ins = by_desc["保險費分期 03/12"]
    assert ins.date == "2026-02-21"        # 民國115→2026
    assert ins.post_date == "2026-05-25"
    assert ins.amount == "1,770"
    assert ins.account_last4 == "9999"
    assert ins.bank == "Cathay"
    assert ins.currency == "TWD"

    assert by_desc["某計程車"].amount == "95"

    assert by_desc["某海外服務"].amount == "945"   # 取新臺幣金額，非外幣 945.00

    fee = by_desc["國外交易手續費 -海外"]
    assert fee.amount == "14"

    assert by_desc["點數折抵"].amount == "-283"    # 負數＝退款/折抵


def test_cathay_year_rollover():
    txns = CathayParser().parse(_msg(), [_STATEMENT])
    by_desc = {t.merchant_raw: t for t in txns}
    # 12 月消費出現在 6 月帳單 → 視為前一年；入帳日 01/05 仍屬帳單年
    assert by_desc["跨年消費測試"].date == "2025-12-28"
    assert by_desc["跨年消費測試"].post_date == "2026-01-05"


def test_cathay_no_period_header_returns_empty():
    no_header = "05/23 05/26 某計程車 95 9999 TW TWD\n"
    assert CathayParser().parse(_msg(), [no_header]) == []


# 模仿每日消費彙整通知信 HTML 內文的表格片段（真實信件無 text/plain 版本，
# 商家/金額/卡號皆為假資料）。第一筆商店未簽約（商店名稱空白，&nbsp;）；
# 第二筆商店已簽約，有店名。
_DAILY_NOTICE_HTML = """\
<html><body>
<p>通知日期：2026/08/02</p>
<p>卡號後4碼： 9999</p>
<table><tr>
<td>卡別</td><td>行動卡號後4碼</td><td>授權日期</td><td>授權時間</td><td>消費地區</td>
</tr><tr>
<td>正卡</td><td>1234</td><td>2026/08/01</td><td>19:08</td><td>TW</td>
</tr></table>
<table><tr>
<td>消費金額</td><td>商店名稱</td><td>消費類別</td><td>備註</td>
</tr><tr>
<td>NT$940</td><td>&nbsp;</td><td>餐飲</td><td>註一</td>
</tr></table>
<table><tr>
<td>卡別</td><td>行動卡號後4碼</td><td>授權日期</td><td>授權時間</td><td>消費地區</td>
</tr><tr>
<td>正卡</td><td>1234</td><td>2026/07/31</td><td>18:00</td><td>TW</td>
</tr></table>
<table><tr>
<td>消費金額</td><td>商店名稱</td><td>消費類別</td><td>備註</td>
</tr><tr>
<td>NT$291</td><td>某超市門市</td><td>超市/量販</td><td>&nbsp;</td>
</tr></table>
</body></html>
"""


def _daily_msg():
    return EmailMessage(
        msg_id="m2", sender="service@pxbillrc01.cathaybk.com.tw",
        subject="國泰世華銀行消費彙整通知（請勿直接回覆）", date="",
        body_text=_DAILY_NOTICE_HTML, attachments=[],
    )


def test_cathay_daily_notice_parses_both_transactions():
    txns = CathayParser().parse(_daily_msg(), [])
    assert len(txns) == 2
    assert all(t.bank == "Cathay" for t in txns)
    assert all(t.account_last4 == "9999" for t in txns)  # 卡號後4碼，非行動卡號


def test_cathay_daily_notice_blank_merchant_kept_empty_with_category():
    txns = CathayParser().parse(_daily_msg(), [])
    unsettled = txns[0]
    assert unsettled.date == "2026/08/01"
    assert unsettled.amount == "NT$940"
    assert unsettled.merchant_raw == ""
    assert unsettled.source_category == "餐飲"


def test_cathay_daily_notice_settled_merchant_filled():
    txns = CathayParser().parse(_daily_msg(), [])
    settled = txns[1]
    assert settled.date == "2026/07/31"
    assert settled.amount == "NT$291"
    assert settled.merchant_raw == "某超市門市"
    assert settled.source_category == "超市/量販"


def test_cathay_daily_notice_amount_and_date_normalize_downstream():
    from mailquill.normalizer import normalize
    txns = CathayParser().parse(_daily_msg(), [])
    txn = normalize(txns[0], "m2", "email_body", "m2", "2026-08-02T00:00:00Z")
    assert txn.date == "2026-08-01"
    assert txn.amount == "940"


def test_cathay_daily_notice_no_match_returns_empty():
    unrelated = "<html><body><p>CUBE App登入成功通知</p></body></html>"
    assert CathayParser().parse(_daily_msg(), []).__class__ is list  # sanity
    msg = EmailMessage(msg_id="m3", sender="webservice@cathaybk.com.tw",
                        subject="CUBE App登入成功通知", date="",
                        body_text=unrelated, attachments=[])
    assert CathayParser().parse(msg, []) == []


# 模仿真實月結單彙總頁 pdfplumber 抽出的版型片段（僅取跟應繳金額相關的行，
# 金額為假資料）——「最低應繳」沒有乾淨的單行標籤，改抓超商繳費聯上緊接在
# 金額前的「$金額元」樣式，見 cathay.py 的 `_MIN_DUE` 說明。
_BILL_SUMMARY = """\
信用卡帳單 115年8月
本期應繳總額 47,338
正卡本期消費 47,338
$4,734元
新臺幣最低應繳
"""


def test_cathay_parse_bill_extracts_total_and_min_due():
    bill = CathayParser().parse_bill(_msg(), [_BILL_SUMMARY])
    assert bill.bank == "Cathay"
    assert bill.period == "2026-08"
    assert bill.amount_due == "47338"
    assert bill.min_due == "4734"
    assert bill.source_msg_id == "m1"


def test_cathay_parse_bill_missing_min_due_leaves_it_blank():
    text = "信用卡帳單 115年8月\n本期應繳總額 47,338\n"
    bill = CathayParser().parse_bill(_msg(), [text])
    assert bill.amount_due == "47338"
    assert bill.min_due == ""


def test_cathay_parse_bill_no_period_returns_none():
    text = "本期應繳總額 47,338\n$4,734元\n"
    assert CathayParser().parse_bill(_msg(), [text]) is None


def test_cathay_parse_bill_no_total_returns_none():
    assert CathayParser().parse_bill(_msg(), ["信用卡帳單 115年8月\n"]) is None


def test_cathay_daily_notice_parse_bill_returns_none():
    assert CathayParser().parse_bill(_daily_msg(), []) is None
