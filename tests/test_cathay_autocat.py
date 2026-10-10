"""國泰消費類別自動產生 categories.yaml 規則測試。"""
from mailquill.cathay_autocat import fetch_merchant_categories, generate_rules
from mailquill.categorizer import Rule


def test_generates_rule_for_unmatched_merchant_with_known_category():
    new_rules = generate_rules({"某超市門市": "超市∕量販"}, existing_rules=[])
    assert len(new_rules) == 1
    assert new_rules[0].keyword == "某超市門市"
    assert (new_rules[0].l1, new_rules[0].l2) == ("Food", "Groceries")


def test_merchant_already_matched_by_existing_rule_is_skipped():
    existing = [Rule(keyword="某超市", l1="Food", l2="Groceries")]
    new_rules = generate_rules({"某超市門市": "超市∕量販"}, existing_rules=existing)
    assert new_rules == []


def test_unmapped_category_generates_no_rule():
    new_rules = generate_rules({"神秘商店": "其他"}, existing_rules=[])
    assert new_rules == []


def test_blank_category_generates_no_rule():
    new_rules = generate_rules({"某商店": ""}, existing_rules=[])
    assert new_rules == []


def test_multiple_merchants_each_get_own_rule():
    new_rules = generate_rules(
        {"某超市門市": "超市∕量販", "某咖啡店": "餐飲"}, existing_rules=[])
    by_keyword = {r.keyword: (r.l1, r.l2) for r in new_rules}
    assert by_keyword == {
        "某超市門市": ("Food", "Groceries"),
        "某咖啡店": ("Food", "Dining"),
    }


# ---- fetch_merchant_categories：假 Gmail service，測整合線路 ----

class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeBatch:
    def __init__(self, callback):
        self._callback = callback
        self._items = []

    def add(self, request, request_id=None):
        self._items.append((request, request_id))

    def execute(self):
        for request, request_id in self._items:
            try:
                response = request.execute()
            except Exception as e:  # noqa: BLE001（比照真的 batch callback 簽名）
                self._callback(request_id, None, e)
            else:
                self._callback(request_id, response, None)


class _AttachmentsProxy:
    def get(self, userId, messageId, id):
        raise AssertionError("每日通知信不該有附件被下載")


class _Labels:
    def __init__(self, fake):
        self._fake = fake

    def list(self, userId):
        return _Exec({"labels": self._fake.labels})


class _MessagesResource:
    def __init__(self, fake):
        self._fake = fake

    def list(self, userId, labelIds=None, q=None, pageToken=None):
        return _Exec(self._fake.list_pages[pageToken])

    def get(self, userId, id, format=None, metadataHeaders=None):
        return _Exec(self._fake.messages[id])

    def attachments(self):
        return _AttachmentsProxy()


class _Users:
    def __init__(self, fake):
        self._fake = fake

    def messages(self):
        return _MessagesResource(self._fake)

    def labels(self):
        return _Labels(self._fake)


class FakeService:
    def __init__(self):
        self.labels = []
        self.list_pages = {}
        self.messages = {}

    def users(self):
        return _Users(self)

    def new_batch_http_request(self, callback=None):
        return _FakeBatch(callback)


_DAILY_NOTICE_HTML = """
<html><body>
<table><tr>
<td>卡別</td><td>行動卡號後4碼</td><td>授權日期</td><td>授權時間</td><td>消費地區</td>
</tr><tr>
<td>正卡</td><td>1234</td><td>2026/07/31</td><td>18:00</td><td>TW</td>
</tr></table>
<table><tr>
<td>消費金額</td><td>商店名稱</td><td>消費類別</td><td>備註</td>
</tr><tr>
<td>NT$291</td><td>某超市門市</td><td>超市∕量販</td><td>&nbsp;</td>
</tr></table>
</body></html>
"""


def _msg_dict(sender, subject, html):
    return {
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 01 Aug 2026 10:00:00 +0800"},
            ],
            "mimeType": "text/html",
            "body": {"data": _b64(html)},
        }
    }


def _b64(s: str) -> str:
    import base64
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def test_fetch_merchant_categories_scans_daily_notices_across_messages():
    svc = FakeService()
    svc.labels = [{"id": "L1", "name": "消費通知"}]
    svc.list_pages = {None: {"messages": [{"id": "m1"}, {"id": "m2"}]}}
    svc.messages = {
        "m1": _msg_dict("service@pxbillrc01.cathaybk.com.tw",
                        "國泰世華銀行消費彙整通知（請勿直接回覆）", _DAILY_NOTICE_HTML),
        "m2": _msg_dict("no-reply@some-other-bank.com", "跟國泰無關的信",
                        "<html><body>hi</body></html>"),
    }

    result = fetch_merchant_categories(svc, ["消費通知"])

    assert result == {"某超市門市": "超市∕量販"}


def test_fetch_merchant_categories_only_downloads_full_mail_for_cathay_senders(monkeypatch):
    # 標籤底下有各家銀行的信（還帶 PDF 附件）：先用標頭過濾，只有國泰的信才
    # 下載完整內容，不然會把其他銀行的月結單 PDF 全抓一遍。
    import mailquill.cathay_autocat as mod
    from mailquill.gmail_message import EmailMessage

    senders = {"c1": "service@cathaybk.com.tw", "h1": "estatement@hsbc.com.tw",
               "c2": "service@cathaybk.com.tw"}
    monkeypatch.setattr(mod, "list_labels_messages",
                        lambda service, labels, query=None: (["c1", "h1", "c2"], []))
    monkeypatch.setattr(mod, "get_message_metadata",
                        lambda service, mid: (senders[mid], "subject", ""))
    fetched = []

    def fake_extract(service, ids):
        fetched.extend(ids)
        return {i: EmailMessage(i, senders[i], "subject", "", "", []) for i in ids}

    monkeypatch.setattr(mod, "extract_messages", fake_extract)

    mod.fetch_merchant_categories(object(), ["財務"])

    assert fetched == ["c1", "c2"]
