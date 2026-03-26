from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
