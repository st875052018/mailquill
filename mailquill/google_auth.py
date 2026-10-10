"""Gmail 與 Sheets 共用同一份 OAuth token：兩個 scope 一次同意、存同一個
token_path，避免各自跑一次授權流程、也避免使用者要管理兩份憑證檔。

若既有 token.json 是舊版（只含 gmail.readonly），拿去跑會缺 Sheets 權限；
刪掉 token_path 讓下一次呼叫走過完整的 InstalledAppFlow 即可重新同意。
"""
from __future__ import annotations

import json
import os

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SCOPES = [GMAIL_SCOPE, SHEETS_SCOPE]


def get_credentials(credentials_path: str, token_path: str):
    """整合用，不寫單元測試（需要真的瀏覽器 OAuth 往返）。"""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.auth.exceptions import RefreshError

    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except (json.JSONDecodeError, ValueError):
            creds = None  # 空檔/壞檔（如編輯器誤清空）視同沒有 token，走下面重新授權
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                # refresh token 被撤銷或過期（OAuth 應用在「測試」狀態時 7 天就會失效），
                # 直接重走瀏覽器授權，不要讓使用者面對一整頁 traceback。
                print(f"Google 授權已失效（{e.args[0] if e.args else e}），開啟瀏覽器重新授權…")
                creds = None
        if not creds:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def build_google_service(api: str, version: str, credentials_path: str, token_path: str):
    """建立指定 Google API 的 service（Gmail／Sheets 共用同一套 OAuth/HTTP 設定）。
    整合用，不寫單元測試。
    """
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    creds = get_credentials(credentials_path, token_path)
    # 每個請求設 30 秒逾時，避免單一讀取無限期卡住；暫時性錯誤由呼叫端自行重試
    authed = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build(api, version, http=authed, cache_discovery=False)
