from types import SimpleNamespace

from src.core.upload import sub2api_upload


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def make_account(**overrides):
    data = {
        "email": "tester@example.com",
        "access_token": "at-123",
        "refresh_token": "rt-123",
        "client_id": "client-123",
        "account_id": "team-account",
        "workspace_id": "team-workspace",
        "expires_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_upload_to_sub2api_uses_latest_db_account_and_workspace_ids(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(sub2api_upload.cffi_requests, "post", fake_post)

    success, message = sub2api_upload.upload_to_sub2api(
        [make_account()],
        api_url="https://sub2api.example.com",
        api_key="key-123",
    )

    assert success is True
    assert message == "成功上传 1 个账号"
    account_item = calls[0]["kwargs"]["json"]["data"]["accounts"][0]
    assert account_item["credentials"]["chatgpt_account_id"] == "team-account"
    assert account_item["credentials"]["organization_id"] == "team-workspace"
