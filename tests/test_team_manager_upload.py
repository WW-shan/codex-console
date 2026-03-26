from contextlib import contextmanager
from types import SimpleNamespace

from src.core.upload import team_manager_upload as tm_upload


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeField:
    def __eq__(self, other):
        return other


class FakeAccountModel:
    id = FakeField()


class FakeQuery:
    def __init__(self, accounts):
        self.accounts = accounts
        self.account_id = None

    def filter(self, account_id):
        self.account_id = account_id
        return self

    def first(self):
        return self.accounts.get(self.account_id)


class FakeSession:
    def __init__(self, accounts):
        self.accounts = accounts

    def query(self, model):
        return FakeQuery(self.accounts)


def make_account(**overrides):
    data = {
        "id": 1,
        "email": "tester@example.com",
        "access_token": None,
        "session_token": None,
        "refresh_token": None,
        "client_id": None,
        "account_id": None,
        "workspace_id": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_upload_to_team_manager_accepts_access_token(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(access_token="at-123"),
        api_url="https://tm.example.com/",
        api_key="key-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["url"] == "https://tm.example.com/admin/teams/import"
    assert calls[0]["kwargs"]["headers"]["X-API-Key"] == "key-123"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "single",
        "email": "tester@example.com",
        "access_token": "at-123",
    }



def test_upload_to_team_manager_accepts_session_token_without_access_token(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=201)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(session_token="st-123"),
        api_url="https://tm.example.com",
        api_key="key-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "single",
        "email": "tester@example.com",
        "session_token": "st-123",
    }



def test_upload_to_team_manager_accepts_refresh_token_with_client_id(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(refresh_token="rt-123", client_id="client-123"),
        api_url="https://tm.example.com",
        api_key="key-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "single",
        "email": "tester@example.com",
        "refresh_token": "rt-123",
        "client_id": "client-123",
    }


def test_upload_to_team_manager_includes_workspace_context(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(access_token="at-123", account_id="team-account", workspace_id="team-workspace"),
        api_url="https://tm.example.com",
        api_key="key-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "single",
        "email": "tester@example.com",
        "access_token": "at-123",
        "account_id": "team-account",
        "workspace_id": "team-workspace",
    }



def test_upload_to_team_manager_rejects_account_without_supported_credentials(monkeypatch):
    called = False

    def fake_post(url, **kwargs):
        nonlocal called
        called = True
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(),
        api_url="https://tm.example.com",
        api_key="key-123",
    )

    assert success is False
    assert message == "账号缺少 Team Manager 导入凭据"
    assert called is False



def test_upload_to_team_manager_omits_blank_optional_fields(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    success, message = tm_upload.upload_to_team_manager(
        make_account(email=None, access_token="at-123", account_id="   "),
        api_url="https://tm.example.com",
        api_key="key-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "single",
        "access_token": "at-123",
    }



def test_batch_upload_to_team_manager_accepts_supported_credential_variants(monkeypatch):
    calls = []
    accounts = {
        1: make_account(id=1, email="st@example.com", session_token="st-123", account_id="acct-1", workspace_id="ws-1"),
        2: make_account(id=2, email="rt@example.com", refresh_token="rt-123", client_id="client-123", account_id="acct-2", workspace_id="ws-2"),
        3: make_account(id=3, email="at@example.com", access_token="at-123", account_id="acct-3", workspace_id="ws-3"),
        4: make_account(id=4, email="invalid@example.com"),
    }

    @contextmanager
    def fake_get_db():
        yield FakeSession(accounts)

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200)

    monkeypatch.setattr(tm_upload, "get_db", fake_get_db)
    monkeypatch.setattr(tm_upload, "Account", FakeAccountModel)
    monkeypatch.setattr(tm_upload.cffi_requests, "post", fake_post)

    results = tm_upload.batch_upload_to_team_manager(
        [1, 2, 3, 4],
        api_url="https://tm.example.com/",
        api_key="key-123",
    )

    assert results["success_count"] == 3
    assert results["failed_count"] == 0
    assert results["skipped_count"] == 1
    assert calls[0]["url"] == "https://tm.example.com/admin/teams/import"
    assert calls[0]["kwargs"]["json"] == {
        "import_type": "batch",
        "content": "\n".join([
            "st@example.com,,,st-123,,acct-1,ws-1",
            "rt@example.com,,rt-123,,client-123,acct-2,ws-2",
            "at@example.com,at-123,,,,acct-3,ws-3",
        ]),
    }
    invalid_detail = next(detail for detail in results["details"] if detail["id"] == 4)
    assert invalid_detail["error"] == "缺少 Team Manager 导入凭据"
