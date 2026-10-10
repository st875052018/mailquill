"""台北富邦 parser 測試（完全合成資料，非真實帳單）。"""
from mailquill.gmail_message import EmailMessage
from mailquill.parsers import get_parser
from mailquill.parsers.fubon import FubonParser

_STATEMENT = """\
帳單年月 信用額度 帳單結帳日 繳款截止日
115/02 340,000 115/02/24 115/03/12
消費日期 消費說明 入帳日期 外幣折算日/幣別 外幣金額/消費地 台幣金額
前期應繳總額 10,084
115/02/10 自動扣繳 115/02/11 -10,084
115/02/24 悠遊卡轉置餘額 115/02/24 -85
JCB晶緻正卡末４碼9999
115/01/22 某商店股份有限公司 115/01/26 TWD 70
115/01/23 某外送平台 115/01/27 TWD 379
115/01/26 某咖啡店 建國門市 115/01/29 TWD 45
115/02/13 掛失費 123456******7890 200
本期應繳金額 8,325
"""


def _msg(sender="富邦 <ebill@cf.taipeifubon.com.tw>"):
    return EmailMessage("m1", sender, "信用卡帳單", "", "", [])


def test_fubon_parser_matches():
    assert isinstance(get_parser(_msg()), FubonParser)


def test_fubon_skips_precard_and_summary_rows():
    txns = FubonParser().parse(_msg(), [_STATEMENT])
    # 卡別標題行之後的 4 筆消費；略過 自動扣繳/悠遊卡轉置(卡別前)、前期/本期應繳
    assert len(txns) == 4
    descs = [t.merchant_raw for t in txns]
    assert "自動扣繳" not in " ".join(descs)
    assert "悠遊卡轉置餘額" not in " ".join(descs)


def test_fubon_fields_and_dates():
    by = {t.merchant_raw: t for t in FubonParser().parse(_msg(), [_STATEMENT])}

    a = by["某商店股份有限公司"]
    assert a.date == "2026-01-22"            # 消費日期(行首)
    assert a.post_date == "2026-01-26"       # 入帳日期(描述後)
    assert a.amount == "70"
    assert a.account_last4 == "9999"         # 來自「正卡末４碼9999」
    assert a.bank == "Fubon"

    fp = by["某外送平台"]
    assert fp.amount == "379"

    # 掛失費：無入帳日，需剝除遮罩卡號 123456******7890
    fee = by["掛失費"]
    assert fee.amount == "200"
    assert fee.post_date == ""


def test_fubon_no_card_header_returns_empty():
    # 沒有卡別標題行 → 不計任何消費（避免把繳款調整當消費）
    no_card = "115/01/22 某商店 115/01/26 TWD 70\n"
    assert FubonParser().parse(_msg(), [no_card]) == []


# 轉帳通知信只有 HTML（無 text/plain）：金額/手續費標籤在 <th>，值在相鄰 <td>；
# 其餘欄位是「標籤+值」同一個 <td>——比照真實信件的表格結構合成（帳號/分行/姓名皆為假資料）。
def _rent_transfer_html(dest: str) -> str:
    return f"""\
<html><body><table>
<tr><td>交易結果 交易成功</td></tr>
<tr><td>交易時間 2026/06/30 18:11:50</td></tr>
<tr>
  <th>轉出帳號</th><td>轉出帳號 測試分行 999900****0000 即時餘額 $73,892</td>
  <th>轉出金額</th><td>TWD 35,000</td>
</tr>
<tr>
  <th>轉入帳號</th><td>轉入帳號 {dest}</td>
  <th>手續費</th><td>TWD 0</td>
</tr>
</table></body></html>
"""


_TEST_RENT_SIGNATURE = ("008(某銀行)", "1234")
_RENT_TRANSFER_HTML = _rent_transfer_html("008(某銀行)-00001111****1234 某某人")
_OTHER_TRANSFER_HTML = _rent_transfer_html("822(另一銀行)-00002222****9999 某某人")


def _transfer_msg(body: str):
    return EmailMessage("m2", "mbank@dfm.taipeifubon.com.tw",
                        "台北富邦行動銀行臺幣轉帳交易「成功」通知", "", body, [])


def test_fubon_rent_transfer_parsed():
    parser = FubonParser(rent_dest_signature=_TEST_RENT_SIGNATURE)
    txns = parser.parse(_transfer_msg(_RENT_TRANSFER_HTML), [])
    assert len(txns) == 1
    t = txns[0]
    assert t.date == "2026/06/30"
    assert t.amount == "35,000"
    assert t.merchant_raw == "轉帳房租"
    assert t.account_last4 == "0000"
    assert t.bank == "Fubon"
    assert t.currency == "TWD"


def test_fubon_other_transfer_not_treated_as_rent():
    # 轉去其他收款帳戶 → 不進房租分支，落回信用卡帳單解析（無消費明細，回傳空）
    parser = FubonParser(rent_dest_signature=_TEST_RENT_SIGNATURE)
    assert parser.parse(_transfer_msg(_OTHER_TRANSFER_HTML), []) == []


def test_fubon_rent_transfer_disabled_without_signature():
    # 沒設定 rent_dest_signature（預設）→ 完全不解析轉帳信，落回信用卡帳單解析
    assert FubonParser().parse(_transfer_msg(_RENT_TRANSFER_HTML), []) == []


# 模仿真實月結單彙總頁 pdfplumber 抽出的版型片段（真文字，金額為假資料）：
# 「本期應繳總額」跟「本期應繳金額」是不同的兩行，只有前者帶「元」是彙總欄位
# （見 fubon.py 的 `_TOTAL_DUE` 說明，避免跟後面消費明細區的「本期應繳金額」
# 重複列混淆）。
_BILL_SUMMARY = """\
本期應繳總額 2,369元
帳單年月 信用額度 國內預借現金額度 帳單結帳日 繳款截止日 循環信用利率 帳單分期利率
115/07 150,000 15,000 115/07/24 115/08/09 8.62% 5.62%
本期 □繳交最低應繳金額 NT$800元
"""


def test_fubon_parse_bill_extracts_total_min_due_and_period():
    bill = FubonParser().parse_bill(_msg(), [_BILL_SUMMARY])
    assert bill.bank == "Fubon"
    assert bill.period == "2026-07"
    assert bill.amount_due == "2369"
    assert bill.min_due == "800"
    assert bill.source_msg_id == "m1"


def test_fubon_parse_bill_missing_min_due_leaves_it_blank():
    text = "本期應繳總額 2,369元\n帳單年月 信用額度\n115/07 150,000\n"
    bill = FubonParser().parse_bill(_msg(), [text])
    assert bill.amount_due == "2369"
    assert bill.min_due == ""


def test_fubon_parse_bill_no_total_returns_none():
    text = "帳單年月 信用額度\n115/07 150,000\n本期 □繳交最低應繳金額 NT$800元\n"
    assert FubonParser().parse_bill(_msg(), [text]) is None


def test_fubon_parse_bill_no_period_returns_none():
    text = "本期應繳總額 2,369元\n本期 □繳交最低應繳金額 NT$800元\n"
    assert FubonParser().parse_bill(_msg(), [text]) is None


def test_fubon_rent_transfer_parse_bill_returns_none():
    parser = FubonParser(rent_dest_signature=_TEST_RENT_SIGNATURE)
    assert parser.parse_bill(_transfer_msg(_RENT_TRANSFER_HTML), []) is None
