import textwrap

import pytest
import yaml

from mailquill.schema import Transaction, make_txn_id
from mailquill.store import append_transactions, read_transactions, rebuild_sqlite
from mailquill.webapp import api_add_transaction, api_categorize, api_get_data


def _txn(date, merchant, amount, l1="Uncategorized", l2="", bank="Cathay", currency="TWD"):
    tid = make_txn_id(bank, "1234", date, amount, merchant)
    return Transaction(
        txn_id=tid, date=date, post_date=date, amount=amount, currency=currency,
        merchant_raw=merchant, merchant_norm=merchant, category_l1=l1, category_l2=l2,
        bank=bank, account_last4="1234", source_type="pdf", source_msg_id="m1",
        raw_ref="", imported_at="2026-06-24T00:00:00",
    )


def _paths(tmp_path, rules_yaml: str = "rules:\n"):
    csv_path = str(tmp_path / "t.csv")
    db_path = str(tmp_path / "t.db")
    categories_path = tmp_path / "categories.yaml"
    categories_path.write_text(textwrap.dedent(rules_yaml), encoding="utf-8")
    return csv_path, db_path, str(categories_path)


def _loaded_rules(categories_path):
    with open(categories_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("rules", [])


def test_api_get_data_includes_txn_id_and_merchant_raw(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    append_transactions(csv_path, [_txn("2026-06-01", "全聯福利中心", "300", "Food", "Groceries")])
    rebuild_sqlite(csv_path, db_path)

    rows = api_get_data(csv_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["merchant_raw"] == "全聯福利中心"
    assert row["txn_id"]
    assert row["amount"] == 300.0
    assert row["l1"] == "Food"
    assert row["month"] == "2026-06"


def test_api_categorize_adds_rule_and_recategorizes(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    append_transactions(csv_path, [
        _txn("2026-06-01", "全聯福利中心", "300"),
        _txn("2026-06-02", "全聯福利中心", "150"),
        _txn("2026-06-03", "家樂福", "500"),
    ])
    rebuild_sqlite(csv_path, db_path)

    result = api_categorize(csv_path, db_path, categories_path, "全聯", "Food", "Groceries")

    assert result == {"ok": True, "rule_added": True, "count": 3}
    rows = {r.merchant_raw: r for r in read_transactions(csv_path)}
    assert (rows["全聯福利中心"].category_l1, rows["全聯福利中心"].category_l2) == ("Food", "Groceries")
    assert rows["家樂福"].category_l1 == "Uncategorized"
    assert _loaded_rules(categories_path) == [
        {"keyword": "全聯", "l1": "Food", "l2": "Groceries"}
    ]


def test_api_categorize_skips_duplicate_rule(tmp_path):
    csv_path, db_path, categories_path = _paths(
        tmp_path, 'rules:\n  - {keyword: "全聯", l1: "Food", l2: "Groceries"}\n'
    )
    append_transactions(csv_path, [_txn("2026-06-01", "全聯福利中心", "300", "Food", "Groceries")])
    rebuild_sqlite(csv_path, db_path)

    result = api_categorize(csv_path, db_path, categories_path, "全聯", "Food", "Groceries")

    assert result["rule_added"] is False
    assert _loaded_rules(categories_path) == [
        {"keyword": "全聯", "l1": "Food", "l2": "Groceries"}
    ]


def test_api_categorize_edit_takes_effect_even_when_shadowed_by_earlier_rule(tmp_path):
    # 網頁上「編輯分類」如果只會 append，這條新規則會被更早、範圍更廣的
    # 「財團法人」擋住，畫面看起來像編輯完全沒生效——這裡驗證真的有生效。
    csv_path, db_path, categories_path = _paths(
        tmp_path, 'rules:\n  - {keyword: "財團法人", l1: "Donation", l2: "Donation"}\n'
    )
    append_transactions(csv_path, [_txn("2026-06-01", "財團法人私立高雄醫院附設", "300")])
    rebuild_sqlite(csv_path, db_path)

    result = api_categorize(csv_path, db_path, categories_path,
                            "財團法人私立高雄醫", "Medical", "Care")

    assert result["rule_added"] is True
    rows = {r.merchant_raw: r for r in read_transactions(csv_path)}
    row = rows["財團法人私立高雄醫院附設"]
    assert (row.category_l1, row.category_l2) == ("Medical", "Care")


def test_api_categorize_editing_same_keyword_again_does_not_duplicate(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    append_transactions(csv_path, [_txn("2026-06-01", "全聯福利中心", "300")])
    rebuild_sqlite(csv_path, db_path)

    api_categorize(csv_path, db_path, categories_path, "全聯", "Food", "")
    api_categorize(csv_path, db_path, categories_path, "全聯", "Food", "Groceries")

    assert _loaded_rules(categories_path) == [
        {"keyword": "全聯", "l1": "Food", "l2": "Groceries"}
    ]


def test_api_categorize_requires_keyword_and_l1(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    with pytest.raises(ValueError):
        api_categorize(csv_path, db_path, categories_path, "", "Food", "")
    with pytest.raises(ValueError):
        api_categorize(csv_path, db_path, categories_path, "全聯", "", "")


def test_api_add_transaction_creates_manual_txn(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)

    result = api_add_transaction(csv_path, db_path, categories_path, {
        "date": "2026-06-15", "merchant": "路邊攤", "amount": "120",
        "currency": "TWD", "l1": "Food", "l2": "Dining",
    })

    assert result["ok"] is True
    txns = read_transactions(csv_path)
    assert len(txns) == 1
    t = txns[0]
    assert t.txn_id == result["txn_id"]
    assert t.source_type == "manual"
    assert t.bank == "manual"
    assert (t.category_l1, t.category_l2) == ("Food", "Dining")
    # SQLite 也已重建，查得到這筆
    from mailquill.report import aggregate
    data = aggregate(db_path)
    assert data.count == 1


def test_api_add_transaction_adds_rule_when_no_existing_match(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)

    result = api_add_transaction(csv_path, db_path, categories_path, {
        "date": "2026-06-15", "merchant": "路邊攤", "amount": "120", "l1": "Food", "l2": "Dining",
    })

    assert result["rule_added"] is True
    assert _loaded_rules(categories_path) == [{"keyword": "路邊攤", "l1": "Food", "l2": "Dining"}]


def test_api_add_transaction_skips_rule_when_already_matches(tmp_path):
    csv_path, db_path, categories_path = _paths(
        tmp_path, 'rules:\n  - {keyword: "路邊攤", l1: "Food", l2: "Dining"}\n'
    )

    result = api_add_transaction(csv_path, db_path, categories_path, {
        "date": "2026-06-15", "merchant": "路邊攤", "amount": "120", "l1": "Food", "l2": "Dining",
    })

    assert result["rule_added"] is False
    assert _loaded_rules(categories_path) == [{"keyword": "路邊攤", "l1": "Food", "l2": "Dining"}]


def test_api_add_transaction_requires_core_fields(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    with pytest.raises(ValueError):
        api_add_transaction(csv_path, db_path, categories_path, {
            "date": "", "merchant": "路邊攤", "amount": "120", "l1": "Food",
        })
    with pytest.raises(ValueError):
        api_add_transaction(csv_path, db_path, categories_path, {
            "date": "2026-06-15", "merchant": "路邊攤", "amount": "not-a-number", "l1": "Food",
        })


def test_api_add_transaction_dedupes_txn_id_on_repeated_submit(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    payload = {
        "date": "2026-06-15", "merchant": "路邊攤", "amount": "120", "l1": "Food", "l2": "Dining",
    }

    r1 = api_add_transaction(csv_path, db_path, categories_path, payload)
    r2 = api_add_transaction(csv_path, db_path, categories_path, dict(payload))

    assert r1["txn_id"] != r2["txn_id"]
    assert len(read_transactions(csv_path)) == 2


def test_api_get_data_merges_foreign_fee(tmp_path):
    csv_path, db_path, categories_path = _paths(tmp_path)
    append_transactions(csv_path, [
        _txn("2026-07-20", "MOBILE SUICA APPLE V", "3998", "Transport"),
        _txn("2026-07-20", "國外交易手續費 -MOBILE", "60", "Other", "Fee"),
    ])
    rows = api_get_data(csv_path)
    assert [(r["merchant_raw"], r["amount"], r["fee"]) for r in rows] == [
        ("MOBILE SUICA APPLE V", 4058.0, 60.0)]


# ---- HTTP 層：本地伺服器沒有驗證，必須擋掉從別的網頁／DNS rebinding 來的請求 ----

@pytest.fixture
def live_server(tmp_path):
    import http.client
    import http.server
    import threading
    from mailquill.config import Config
    from mailquill.webapp import _make_handler

    csv_path, db_path, categories_path = _paths(tmp_path)
    cfg = Config(label="x", csv_path=csv_path, db_path=db_path, categories_path=categories_path)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(cfg))
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01},
                     daemon=True).start()
    port = httpd.server_address[1]

    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        hdrs = {"Host": f"127.0.0.1:{port}"}
        hdrs.update(headers or {})
        conn.request(method, path, body=body.encode("utf-8") if body else None, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    request.port = port
    request.csv_path = csv_path
    yield request
    httpd.shutdown()
    httpd.server_close()


_GOOD = ('{"date":"2026-06-01","merchant":"現金","amount":"50","l1":"Food"}')
_JSON = {"Content-Type": "application/json"}


def test_http_normal_json_post_works(live_server):
    status, _ = live_server("POST", "/api/transactions", _GOOD, _JSON)
    assert status == 200
    assert len(read_transactions(live_server.csv_path)) == 1


def test_http_rejects_foreign_host_header_dns_rebinding(live_server):
    status, _ = live_server("GET", "/api/data", headers={"Host": "evil.example:80"})
    assert status == 403


def test_http_rejects_cross_origin_post(live_server):
    status, _ = live_server("POST", "/api/transactions", _GOOD,
                            {**_JSON, "Origin": "http://evil.example"})
    assert status == 403
    assert read_transactions(live_server.csv_path) == []


def test_http_rejects_non_json_content_type_post(live_server):
    # text/plain 的跨站 POST 不會觸發 preflight，是最常見的 CSRF 手法
    status, _ = live_server("POST", "/api/transactions", _GOOD, {"Content-Type": "text/plain"})
    assert status == 415
    assert read_transactions(live_server.csv_path) == []


def test_http_same_origin_header_is_accepted(live_server):
    status, _ = live_server("POST", "/api/transactions", _GOOD,
                            {**_JSON, "Origin": f"http://127.0.0.1:{live_server.port}"})
    assert status == 200


@pytest.mark.parametrize("body", ["[1,2]", '"str"', "null", "{bad"])
def test_http_malformed_or_non_object_body_is_400(live_server, body):
    status, _ = live_server("POST", "/api/transactions", body, _JSON)
    assert status == 400


def test_http_bad_content_length_is_400(live_server):
    status, _ = live_server("POST", "/api/transactions", _GOOD,
                            {**_JSON, "Content-Length": "abc"})
    assert status == 400


@pytest.mark.parametrize("amount", ["nan", "inf", "-inf", "NaN"])
def test_add_transaction_rejects_non_finite_amount(tmp_path, amount):
    csv_path, db_path, cat = _paths(tmp_path)
    with pytest.raises(ValueError):
        api_add_transaction(csv_path, db_path, cat,
                            {"date": "2026-06-01", "merchant": "x", "amount": amount, "l1": "Food"})
    assert read_transactions(csv_path) == []


def test_http_wrong_field_types_are_400_not_500(live_server):
    status, _ = live_server("POST", "/api/categorize", '{"keyword": 123, "l1": ["x"]}', _JSON)
    assert status == 400
