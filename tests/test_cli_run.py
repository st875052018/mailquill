import mailquill.cli as cli
from mailquill.config import Config
from mailquill.pipeline import RunResult


def test_main_run_invokes_pipeline_and_returns_zero(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務")
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "build_service", lambda c, t: object())

    captured = {}

    def fake_run(service, cfg_arg, imported_at, query=None, on_progress=None,
                force_refetch=False):
        captured["imported_at"] = imported_at
        captured["query"] = query
        captured["force_refetch"] = force_refetch
        return RunResult(fetched=2, matched=1, added=1, skipped=0,
                         needs_parser=["x@unknown.test | 帳單"])

    monkeypatch.setattr(cli, "run_pipeline", fake_run)

    rc = cli.main(["run", "--config", str(tmp_path / "config.yaml")])
    assert rc == 0
    assert captured["imported_at"]  # 有帶入時間字串
    out = capsys.readouterr().out
    assert "added" in out or "新增" in out
    assert "unknown.test" in out  # needs_parser 有列出


def test_resolve_since_defaults_to_this_year():
    from datetime import datetime
    import argparse
    import mailquill.cli as cli
    args = argparse.Namespace(since=None, all=False)
    assert cli._resolve_since(args) == f"{datetime.now().year}-01-01"


def test_resolve_since_all_disables_filter():
    import argparse
    import mailquill.cli as cli
    args = argparse.Namespace(since=None, all=True)
    assert cli._resolve_since(args) is None


def test_resolve_since_explicit():
    import argparse
    import mailquill.cli as cli
    args = argparse.Namespace(since="2025-03-15", all=False)
    assert cli._resolve_since(args) == "2025-03-15"


def test_resolve_since_rejects_bad_format():
    import argparse
    import pytest
    import mailquill.cli as cli
    args = argparse.Namespace(since="20260101", all=False)
    with pytest.raises(SystemExit):
        cli._resolve_since(args)
