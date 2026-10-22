from mailquill import google_auth


class _FakeCreds:
    valid = True

    def to_json(self):
        return "{}"


def test_get_credentials_treats_empty_token_file_as_missing(tmp_path, monkeypatch):
    token_path = tmp_path / "token.json"
    token_path.write_text("", encoding="utf-8")  # 模擬編輯器誤清空

    class FakeFlow:
        @staticmethod
        def from_client_secrets_file(credentials_path, scopes):
            return FakeFlow()

        def run_local_server(self, port=0):
            return _FakeCreds()

    import sys
    fake_module = type(sys)("google_auth_oauthlib.flow")
    fake_module.InstalledAppFlow = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_module)

    creds = google_auth.get_credentials("credentials.json", str(token_path))

    assert creds.to_json() == "{}"
    assert token_path.read_text(encoding="utf-8") == "{}"


def test_noninteractive_raises_instead_of_opening_browser(tmp_path, monkeypatch):
    """排程（mbp 上的 watchtower）沒有人能點瀏覽器：授權失效要直接報錯，不能卡住等。"""
    import sys
    import pytest

    class FakeFlow:
        @staticmethod
        def from_client_secrets_file(credentials_path, scopes):
            raise AssertionError("不該開瀏覽器授權")

    fake_module = type(sys)("google_auth_oauthlib.flow")
    fake_module.InstalledAppFlow = FakeFlow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", fake_module)
    monkeypatch.setenv("MAILQUILL_NONINTERACTIVE", "1")

    with pytest.raises(google_auth.AuthExpired):
        google_auth.get_credentials("credentials.json", str(tmp_path / "token.json"))
