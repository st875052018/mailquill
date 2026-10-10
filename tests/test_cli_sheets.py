import mailquill.cli as cli
from mailquill.config import Config
from mailquill.pipeline import RunResult
from mailquill.sheets_sync import SyncResult


def test_main_sync_sheets_builds_service_and_prints_created_url(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"),
                db_path=str(tmp_path / "t.db"),
                sheets_state_path=str(tmp_path / "sheets_state.json"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_sheets_service", lambda creds, token: "fake-service")

    def fake_sync_sheets(csv_path, state_path, service, **kw):
        assert csv_path == cfg.csv_path
        assert state_path == cfg.sheets_state_path
        assert service == "fake-service"
        return SyncResult(spreadsheet_url="https://sheets.google/abc", rows=3, created=True)

    monkeypatch.setattr(cli, "sync_sheets", fake_sync_sheets)

    rc = cli.main(["sync-sheets", "--config", "x.yaml"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "https://sheets.google/abc" in out
    assert "rows=3" in out


def test_main_sync_sheets_prints_overwritten_when_not_created(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_sheets_service", lambda creds, token: "fake-service")
    monkeypatch.setattr(
        cli, "sync_sheets",
        lambda *a, **kw: SyncResult(spreadsheet_url="https://sheets.google/xyz",
                                    rows=0, created=False),
    )

    rc = cli.main(["sync-sheets"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "已覆寫" in out
    assert "https://sheets.google/xyz" in out


def test_main_run_with_sync_sheets_flag_chains_sync(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"),
                db_path=str(tmp_path / "t.db"),
                sheets_state_path=str(tmp_path / "sheets_state.json"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **kw: RunResult(fetched=1, matched=1, added=1))
    monkeypatch.setattr(cli, "build_sheets_service", lambda creds, token: "fake-sheets-service")

    sync_calls = []

    def fake_sync_sheets(csv_path, state_path, service, **kw):
        sync_calls.append(service)
        return SyncResult(spreadsheet_url="https://sheets.google/abc", rows=1, created=False)

    monkeypatch.setattr(cli, "sync_sheets", fake_sync_sheets)

    rc = cli.main(["run", "--sync-sheets"])

    out = capsys.readouterr().out
    assert rc == 0
    assert sync_calls == ["fake-sheets-service"]
    assert "https://sheets.google/abc" in out


def test_main_run_without_sync_sheets_flag_skips_sync(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda creds, token: "fake-gmail-service")
    monkeypatch.setattr(cli, "run_pipeline",
                        lambda *a, **kw: RunResult(fetched=0, matched=0, added=0))

    def fail_sync_sheets(*a, **kw):
        raise AssertionError("sync_sheets should not be called without --sync-sheets")

    monkeypatch.setattr(cli, "sync_sheets", fail_sync_sheets)

    rc = cli.main(["run"])

    assert rc == 0
