import mailquill.cli as cli
from mailquill.config import Config
from mailquill.pipeline import RunResult
from mailquill.reconcile import ReconcileResult
from mailquill.sheets_sync import SyncResult


def _setup(monkeypatch, calls, captured):
    cfg = Config(label="財務")
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda c, t: object())
    monkeypatch.setattr(cli, "build_sheets_service", lambda c, t: object())

    def fake_run(service, cfg_arg, imported_at, query=None, on_progress=None,
                 force_refetch=False):
        calls.append("run")
        captured["query"] = query
        return RunResult(fetched=3, matched=2, added=2, skipped=0)

    def fake_reconcile(csv_path, db_path):
        calls.append("reconcile")
        return ReconcileResult(merged=1, ambiguous=0, voided=0)

    def fake_sync(csv_path, state_path, service, **kw):
        calls.append("sync")
        return SyncResult(spreadsheet_url="https://sheet.test/x",
                          created=False, rows=5, archived_rows=0, accounts=[],
                          budget_month="2026-09")

    monkeypatch.setattr(cli, "run_pipeline", fake_run)
    monkeypatch.setattr(cli, "reconcile", fake_reconcile)
    monkeypatch.setattr(cli, "sync_sheets", fake_sync)
    return cfg


def test_update_runs_three_steps_in_order(monkeypatch, tmp_path, capsys):
    calls, captured = [], {}
    _setup(monkeypatch, calls, captured)

    rc = cli.main(["update", "--config", str(tmp_path / "config.yaml")])

    assert rc == 0
    assert calls == ["run", "reconcile", "sync"]
    out = capsys.readouterr().out
    assert "update 完成" in out
    assert "https://sheet.test/x" in out


def test_update_passes_since_to_pipeline(monkeypatch, tmp_path):
    calls, captured = [], {}
    _setup(monkeypatch, calls, captured)

    cli.main(["update", "--config", str(tmp_path / "config.yaml"), "--since", "2026-09-01"])

    assert "2026/09/01" in captured["query"]
