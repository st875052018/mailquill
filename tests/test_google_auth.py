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
