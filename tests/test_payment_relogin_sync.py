from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.register import RegistrationResult
from src.database.models import Base, Account
from src.database.session import DatabaseSessionManager
from src.web.routes import payment as payment_routes


def _create_manager(db_name: str) -> DatabaseSessionManager:
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / db_name
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)
    return manager


def _build_client(monkeypatch, manager: DatabaseSessionManager) -> TestClient:
    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(payment_routes, "get_db", fake_get_db)
    monkeypatch.setattr(payment_routes, "_resolve_runtime_proxy", lambda explicit_proxy, account=None: None)

    app = FastAPI()
    app.include_router(payment_routes.router, prefix="/payment")
    return TestClient(app)


def _create_account(manager: DatabaseSessionManager, **overrides) -> tuple[int, str]:
    payload = {
        "email": "tester@example.com",
        "password": "secret-pass",
        "email_service": "manual",
        "status": "active",
        "access_token": "access-token",
        "subscription_type": None,
    }
    payload.update(overrides)

    with manager.session_scope() as session:
        account = Account(**payload)
        session.add(account)
        session.flush()
        return account.id, account.email


def test_account_relogin_sync_updates_full_account_snapshot(monkeypatch):
    manager = _create_manager("payment_relogin_sync_success.db")
    account_id, email = _create_account(manager)
    client = _build_client(monkeypatch, manager)

    def fake_relogin(*, db, account, proxy):
        account.access_token = "access-new"
        account.refresh_token = "refresh-new"
        account.id_token = "id-new"
        account.session_token = "session-new"
        account.cookies = "foo=bar; __Secure-next-auth.session-token=session-new"
        account.account_id = "acct-new"
        account.workspace_id = "ws-new"
        account.last_refresh = datetime.utcnow()
        return {
            "message": "重登切组同步完成",
            "relogin_used": True,
        }

    def fake_detect(*, db, account, proxy, allow_token_refresh):
        account.subscription_type = "team"
        account.subscription_at = datetime.utcnow()
        return {
            "status": "team",
            "detail": {
                "status": "team",
                "source": "relogin_sync.overview",
                "confidence": "high",
                "note": "relogin_sync_ok",
            },
            "refreshed": False,
            "checked_at": datetime.utcnow(),
            "context_updated": True,
        }

    monkeypatch.setattr(payment_routes, "_relogin_and_refresh_account_snapshot", fake_relogin, raising=False)
    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect)

    response = client.post(f"/payment/accounts/{account_id}/relogin-sync", json={})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "subscription_type": "team",
        "detail": {
            "status": "team",
            "source": "relogin_sync.overview",
            "confidence": "high",
            "note": "relogin_sync_ok",
        },
        "account_id": account_id,
        "account_email": email,
        "message": "重登切组同步完成",
    }

    with manager.session_scope() as session:
        saved = session.get(Account, account_id)
        assert saved.access_token == "access-new"
        assert saved.refresh_token == "refresh-new"
        assert saved.id_token == "id-new"
        assert saved.session_token == "session-new"
        assert saved.cookies == "foo=bar; __Secure-next-auth.session-token=session-new"
        assert saved.account_id == "acct-new"
        assert saved.workspace_id == "ws-new"
        assert saved.subscription_type == "team"
        assert saved.subscription_at is not None
        assert saved.last_refresh is not None


def test_account_relogin_sync_returns_400_without_required_credentials(monkeypatch):
    manager = _create_manager("payment_relogin_sync_missing_creds.db")
    account_id, _ = _create_account(manager, password="", email_service="")
    client = _build_client(monkeypatch, manager)

    response = client.post(f"/payment/accounts/{account_id}/relogin-sync", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "账号缺少邮箱、密码或邮箱服务，无法自动重登切组"


def test_account_relogin_sync_returns_404_for_missing_account(monkeypatch):
    manager = _create_manager("payment_relogin_sync_missing.db")
    client = _build_client(monkeypatch, manager)

    response = client.post("/payment/accounts/999/relogin-sync", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "账号不存在"


def test_account_relogin_sync_delegates_to_engine_relogin(monkeypatch):
    manager = _create_manager("payment_relogin_sync_codex_consent.db")
    account_id, _ = _create_account(manager)

    with manager.session_scope() as session:
        account = session.get(Account, account_id)
        account.cookies = "foo=bar; oai-did=did-old; __Secure-next-auth.session-token=session-old"
        delegated = []

        class StubEngine:
            def __init__(self, *, email_service, proxy_url, callback_logger, task_uuid):
                self.email_service = email_service
                self.proxy_url = proxy_url
                self.callback_logger = callback_logger
                self.task_uuid = task_uuid
                self.email = None
                self.password = None
                self.email_info = None

            def relogin_existing_account(self, *, label, seed_cookies_text=None, seed_device_id=None):
                delegated.append((label, seed_cookies_text, seed_device_id))
                return RegistrationResult(
                    success=True,
                    email="tester@example.com",
                    access_token="access-new",
                    refresh_token="refresh-new",
                    id_token="id-new",
                    session_token="session-new",
                    account_id="acct-new",
                    workspace_id="ws-new",
                )

            def _dump_session_cookies(self):
                return "foo=bar; oai-did=did-old"

        monkeypatch.setattr(payment_routes, "_resolve_email_service_for_account_session_bootstrap", lambda db, account, proxy: object())
        monkeypatch.setattr(payment_routes, "RegistrationEngine", StubEngine)
        monkeypatch.setattr(payment_routes, "_resolve_account_device_id", lambda account: "did-old")

        result = payment_routes._relogin_and_refresh_account_snapshot(session, account, proxy=None)

        assert result["message"] == "重登切组同步完成"
        assert delegated == [("重登切组同步", "foo=bar; oai-did=did-old; __Secure-next-auth.session-token=session-old", "did-old")]
        assert account.access_token == "access-new"
        assert account.refresh_token == "refresh-new"
        assert account.id_token == "id-new"
        assert account.session_token == "session-new"
        assert account.account_id == "acct-new"
        assert account.workspace_id == "ws-new"
        assert account.cookies == "foo=bar; oai-did=did-old; __Secure-next-auth.session-token=session-new"
        assert account.last_refresh is not None


def test_account_relogin_sync_raises_engine_error(monkeypatch):
    manager = _create_manager("payment_relogin_sync_unknown_page.db")
    account_id, _ = _create_account(manager)

    with manager.session_scope() as session:
        account = session.get(Account, account_id)

        class StubEngine:
            def __init__(self, *, email_service, proxy_url, callback_logger, task_uuid):
                self.email = None
                self.password = None
                self.email_info = None

            def relogin_existing_account(self, *, label, seed_cookies_text=None, seed_device_id=None):
                return RegistrationResult(success=False, error_message="登录密码后返回未知页面: some_unknown_page")

        monkeypatch.setattr(payment_routes, "_resolve_email_service_for_account_session_bootstrap", lambda db, account, proxy: object())
        monkeypatch.setattr(payment_routes, "RegistrationEngine", StubEngine)
        with pytest.raises(RuntimeError, match="登录密码后返回未知页面: some_unknown_page"):
            payment_routes._relogin_and_refresh_account_snapshot(session, account, proxy=None)


def test_extract_session_token_helpers_support_secure_and_chunked_values():
    cookie_text = "foo=bar; _Secure-next-auth.session-token=token-secure"
    assert payment_routes._extract_session_token_from_cookie_text(cookie_text) == "token-secure"

    chunked_text = (
        "foo=bar; __Secure-next-auth.session-token.0=chunk-a; "
        "__Secure-next-auth.session-token.1=chunk-b"
    )
    assert payment_routes._extract_session_token_from_cookie_text(chunked_text) == "chunk-achunk-b"
    assert payment_routes._extract_session_token_chunks_from_cookie_text(chunked_text) == [0, 1]


class _DummyCookieJar:
    def __init__(self, pairs):
        self._pairs = list(pairs)
        self.jar = []

    def items(self):
        return list(self._pairs)

    def get(self, name):
        for key, value in self._pairs:
            if key == name:
                return value
        return None


def test_extract_session_token_from_cookie_jar_prefers_secure_and_chunked_values():
    jar = _DummyCookieJar([
        ("_Secure-next-auth.session-token", "token-direct"),
    ])
    assert payment_routes._extract_session_token_from_cookie_jar(jar) == "token-direct"

    chunked_jar = _DummyCookieJar([
        ("__Secure-next-auth.session-token.0", "chunk-a"),
        ("__Secure-next-auth.session-token.1", "chunk-b"),
    ])
    assert payment_routes._extract_session_token_from_cookie_jar(chunked_jar) == "chunk-achunk-b"


def test_account_relogin_sync_returns_500_when_relogin_fails(monkeypatch):
    manager = _create_manager("payment_relogin_sync_error.db")
    account_id, _ = _create_account(manager)
    client = _build_client(monkeypatch, manager)

    def fake_relogin(*, db, account, proxy):
        raise RuntimeError("otp failed")

    monkeypatch.setattr(payment_routes, "_relogin_and_refresh_account_snapshot", fake_relogin, raising=False)

    response = client.post(f"/payment/accounts/{account_id}/relogin-sync", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "重登切组同步失败: otp failed"
