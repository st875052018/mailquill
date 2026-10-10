"""Google Sheets API service：跟 gmail_client 共用同一份 OAuth token。"""
from __future__ import annotations

from mailquill.google_auth import build_google_service


def build_service(credentials_path: str, token_path: str):
    """整合用，不寫單元測試（比照 gmail_client.build_service）。"""
    return build_google_service("sheets", "v4", credentials_path, token_path)
