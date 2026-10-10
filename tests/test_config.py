import textwrap
import pytest

from mailquill.config import Config, load_config, load_passwords


def test_load_config_applies_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("label: 財務\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.label == "財務"
    assert cfg.csv_path == "transactions.csv"
    assert cfg.db_path == "mailquill.db"
    assert cfg.passwords_path == "passwords.txt"
    assert cfg.sheets_state_path == "sheets_state.json"
    assert cfg.seen_ids_path == "seen_msg_ids.txt"
    assert cfg.account_aliases == {}
    assert cfg.goal_monthly_income == 100000.0


def test_load_config_overrides(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        label: 財務
        csv_path: /data/t.csv
        db_path: /data/t.db
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.csv_path == "/data/t.csv"
    assert cfg.db_path == "/data/t.db"


def test_load_config_parses_account_aliases(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        label: 財務
        account_aliases:
          "Fubon-6040": 富邦帳戶
          "Fubon-7254": 富邦JCB卡
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.account_aliases == {"Fubon-6040": "富邦帳戶", "Fubon-7254": "富邦JCB卡"}


def test_load_config_parses_goal_monthly_income(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        label: 財務
        goal_monthly_income: 120000
    """), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.goal_monthly_income == 120000


def test_load_config_missing_label_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("csv_path: x.csv\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_load_passwords_filters_blank_and_comments(tmp_path):
    p = tmp_path / "passwords.txt"
    p.write_text("A1234\n\n# comment\n  B5678  \n", encoding="utf-8")
    assert load_passwords(str(p)) == ["A1234", "B5678"]


def test_load_passwords_missing_file_returns_empty(tmp_path):
    assert load_passwords(str(tmp_path / "none.txt")) == []


def test_label_names_single_string():
    assert Config(label="財務").label_names() == ["財務"]


def test_label_names_list():
    assert Config(label=["財務", "銀行"]).label_names() == ["財務", "銀行"]


def test_load_config_accepts_label_list(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("label:\n  - 財務\n  - 銀行\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.label_names() == ["財務", "銀行"]
