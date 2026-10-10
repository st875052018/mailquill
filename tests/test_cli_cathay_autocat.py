import mailquill.cli as cli
from mailquill.categorizer import Rule
from mailquill.config import Config
from mailquill.pipeline import RunResult


def test_main_cathay_autocat_writes_rules_and_reports_pending_rebuild(monkeypatch, tmp_path, capsys):
    categories_path = str(tmp_path / "categories.yaml")
    cfg = Config(label="財務", categories_path=categories_path)
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "fetch_merchant_categories",
                        lambda service, labels, query=None: {"某超市門市": "超市∕量販"})
    monkeypatch.setattr(cli, "load_categories", lambda path: [])
    monkeypatch.setattr(cli, "cathay_generate_rules",
                        lambda merchant_categories, existing_rules:
                            [Rule(keyword="某超市門市", l1="Food", l2="Groceries")])
    appended = []
    monkeypatch.setattr(cli, "upsert_rules", lambda path, rules: appended.append((path, rules)))

    rc = cli.main(["cathay-autocat", "--categories", categories_path])

    out = capsys.readouterr().out
    assert rc == 0
    assert appended == [(categories_path,
                         [Rule(keyword="某超市門市", l1="Food", l2="Groceries")])]
    assert "新規則=1" in out
    assert f"已附加進 {categories_path}" in out


def test_main_cathay_autocat_dry_run_does_not_write(monkeypatch, tmp_path, capsys):
    categories_path = str(tmp_path / "categories.yaml")
    cfg = Config(label="財務", categories_path=categories_path)
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "fetch_merchant_categories",
                        lambda service, labels, query=None: {"某超市門市": "超市∕量販"})
    monkeypatch.setattr(cli, "load_categories", lambda path: [])
    monkeypatch.setattr(cli, "cathay_generate_rules",
                        lambda merchant_categories, existing_rules:
                            [Rule(keyword="某超市門市", l1="Food", l2="Groceries")])

    def fail_append(*a, **kw):
        raise AssertionError("dry-run should not write")
    monkeypatch.setattr(cli, "upsert_rules", fail_append)

    rc = cli.main(["cathay-autocat", "--categories", categories_path, "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in out


def test_main_run_with_cathay_autocat_flag_rebuilds_when_new_rules(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"),
                db_path=str(tmp_path / "t.db"),
                categories_path=str(tmp_path / "categories.yaml"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **kw: RunResult(fetched=1, matched=1, added=1))
    monkeypatch.setattr(cli, "fetch_merchant_categories",
                        lambda service, labels, query=None: {"某超市門市": "超市∕量販"})
    monkeypatch.setattr(cli, "load_categories", lambda path: [])
    monkeypatch.setattr(cli, "cathay_generate_rules",
                        lambda merchant_categories, existing_rules:
                            [Rule(keyword="某超市門市", l1="Food", l2="Groceries")])
    monkeypatch.setattr(cli, "upsert_rules", lambda path, rules: None)

    rebuild_calls = []
    monkeypatch.setattr(cli, "rebuild",
                        lambda csv_path, db_path, categories_path:
                            rebuild_calls.append((csv_path, db_path, categories_path)) or 0)

    rc = cli.main(["run", "--cathay-autocat"])

    assert rc == 0
    assert rebuild_calls == [(cfg.csv_path, cfg.db_path, cfg.categories_path)]


def test_main_run_with_cathay_autocat_flag_skips_rebuild_when_no_new_rules(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"),
                db_path=str(tmp_path / "t.db"),
                categories_path=str(tmp_path / "categories.yaml"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **kw: RunResult(fetched=1, matched=1, added=1))
    monkeypatch.setattr(cli, "fetch_merchant_categories", lambda service, labels, query=None: {})
    monkeypatch.setattr(cli, "load_categories", lambda path: [])
    monkeypatch.setattr(cli, "cathay_generate_rules", lambda merchant_categories, existing_rules: [])

    def fail_rebuild(*a, **kw):
        raise AssertionError("rebuild should not run when there are no new rules")
    monkeypatch.setattr(cli, "rebuild", fail_rebuild)

    rc = cli.main(["run", "--cathay-autocat"])

    assert rc == 0


def test_main_run_without_cathay_autocat_flag_skips_it_entirely(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **kw: RunResult(fetched=0, matched=0, added=0))

    def fail_fetch(*a, **kw):
        raise AssertionError("fetch_merchant_categories should not be called without the flag")
    monkeypatch.setattr(cli, "fetch_merchant_categories", fail_fetch)

    rc = cli.main(["run"])

    assert rc == 0
