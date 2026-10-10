import mailquill.cli as cli
from mailquill.config import Config


def test_main_serve_invokes_run_server_with_config_and_port(monkeypatch, tmp_path, capsys):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)

    captured = {}

    def fake_run_server(config, port=8420):
        captured["config"] = config
        captured["port"] = port

    monkeypatch.setattr(cli, "run_server", fake_run_server)

    rc = cli.main(["serve", "--config", "x.yaml", "--port", "9999"])

    assert rc == 0
    assert captured["config"] is cfg
    assert captured["port"] == 9999
    assert "9999" in capsys.readouterr().out


def test_main_serve_defaults_to_port_8420(monkeypatch, tmp_path):
    cfg = Config(label="財務", csv_path=str(tmp_path / "t.csv"), db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)

    captured = {}
    monkeypatch.setattr(cli, "run_server", lambda config, port=8420: captured.setdefault("port", port))

    rc = cli.main(["serve"])

    assert rc == 0
    assert captured["port"] == 8420
