"""設定與密碼清單載入。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


@dataclass
class Config:
    label: str | list[str]
    csv_path: str = "transactions.csv"
    db_path: str = "mailquill.db"
    categories_path: str = "categories.yaml"
    rules_path: str = "rules.yaml"
    raw_dir: str = "raw"
    passwords_path: str = "passwords.txt"
    credentials_path: str = "credentials.json"
    token_path: str = "token.json"
    sheets_state_path: str = "sheets_state.json"
    seen_ids_path: str = "seen_msg_ids.txt"  # 見 store.read_seen_ids
    bills_path: str = "bills.csv"  # 帳單應繳總額/最低應繳（見 bill_due.py）
    # 富邦轉帳通知信「只認這個收款帳戶」的比對片段（例：["008(華南銀行)", "1234"]）。
    # 每個片段都要出現在信件內文才算命中；留空則停用轉帳房租解析。見 config.example.yaml。
    fubon_rent_dest_signature: list[str] = field(default_factory=list)
    # sync-sheets 帳戶分頁／Overview 矩陣欄位的顯示名稱覆寫，key 為預設名稱
    # 「銀行-末四碼」（如 "Fubon-6040"），value 為想顯示的名稱；沒設定的帳戶
    # 仍用預設名稱。見 config.example.yaml。
    account_aliases: dict[str, str] = field(default_factory=dict)
    # Budget 分頁「五大目標」區塊（見 sheets_sync.py 的 GOAL_CATEGORIES）拿來
    # 算目標金額的月收入。刻意只放在本地 config（不進 git、不同步進 Google
    # Sheet）——使用者不想讓收入數字出現在 Sheet 上，只想看目標金額/實際/
    # 差異。見 config.example.yaml。
    goal_monthly_income: float = 100000.0

    def label_names(self) -> list[str]:
        """label 可為單一字串或字串清單，一律正規化為清單。"""
        if isinstance(self.label, str):
            return [self.label]
        return list(self.label)


def load_config(path: str = "config.yaml") -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "label" not in data or not data["label"]:
        raise ValueError("config 缺少必填欄位 'label'")
    allowed = {f for f in Config.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in allowed}
    return Config(**kwargs)


def load_passwords(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return out
