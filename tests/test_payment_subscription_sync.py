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


def test_account_sync_subscription_updates_team_status(monkeypatch):
    manager = _create_manager("payment_sync_team.db")
    account_id, email = _create_account(manager)
    client = _build_client(monkeypatch, manager)

    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        account.subscription_type = "team"
        account.subscription_at = datetime.utcnow()
        return {
            "status": "team",
            "detail": {
                "status": "team",
                "source": "wham_usage.no_scope.plan",
                "confidence": "medium",
                "note": "checked_without_scope",
            },
            "refreshed": True,
            "checked_at": datetime.utcnow(),
        }

    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect_and_apply, raising=False)

    response = client.post(f"/payment/accounts/{account_id}/sync-subscription", json={})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "subscription_type": "team",
        "detail": {
            "status": "team",
            "source": "wham_usage.no_scope.plan",
            "confidence": "medium",
            "note": "checked_without_scope",
        },
        "account_id": account_id,
        "account_email": email,
    }

    with manager.session_scope() as session:
        saved = session.get(Account, account_id)
        assert saved.subscription_type == "team"
        assert saved.subscription_at is not None


def test_account_sync_subscription_keeps_existing_team_on_low_confidence_free(monkeypatch):
    manager = _create_manager("payment_sync_low_free.db")
    account_id, email = _create_account(
        manager,
        subscription_type="team",
        subscription_at=datetime(2026, 3, 26, 12, 0, 0),
    )
    client = _build_client(monkeypatch, manager)

    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        return {
            "status": "free",
            "detail": {
                "status": "free",
                "source": "wham_usage.plan",
                "confidence": "low",
                "note": "no_paid_signal",
            },
            "refreshed": False,
            "checked_at": datetime.utcnow(),
        }

    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect_and_apply, raising=False)

    response = client.post(f"/payment/accounts/{account_id}/sync-subscription", json={})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "subscription_type": "team",
        "detail": {
            "status": "free",
            "source": "wham_usage.plan",
            "confidence": "low",
            "note": "no_paid_signal",
        },
        "account_id": account_id,
        "account_email": email,
    }

    with manager.session_scope() as session:
        saved = session.get(Account, account_id)
        assert saved.subscription_type == "team"
        assert saved.subscription_at == datetime(2026, 3, 26, 12, 0, 0)


def test_account_sync_subscription_clears_existing_team_on_high_confidence_free(monkeypatch):
    manager = _create_manager("payment_sync_high_free.db")
    account_id, email = _create_account(
        manager,
        subscription_type="team",
        subscription_at=datetime(2026, 3, 26, 12, 0, 0),
    )
    client = _build_client(monkeypatch, manager)

    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        account.subscription_type = None
        account.subscription_at = None
        return {
            "status": "free",
            "detail": {
                "status": "free",
                "source": "explicit_free.plan",
                "confidence": "high",
                "note": "explicit_free=basic",
            },
            "refreshed": False,
            "checked_at": datetime.utcnow(),
        }

    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect_and_apply, raising=False)

    response = client.post(f"/payment/accounts/{account_id}/sync-subscription", json={})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "subscription_type": "free",
        "detail": {
            "status": "free",
            "source": "explicit_free.plan",
            "confidence": "high",
            "note": "explicit_free=basic",
        },
        "account_id": account_id,
        "account_email": email,
    }

    with manager.session_scope() as session:
        saved = session.get(Account, account_id)
        assert saved.subscription_type is None
        assert saved.subscription_at is None


def test_account_sync_subscription_returns_404_for_missing_account(monkeypatch):
    manager = _create_manager("payment_sync_missing.db")
    client = _build_client(monkeypatch, manager)

    response = client.post("/payment/accounts/999/sync-subscription", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "账号不存在"


def test_account_sync_subscription_returns_500_when_detection_fails(monkeypatch):
    manager = _create_manager("payment_sync_error.db")
    account_id, _ = _create_account(manager)
    client = _build_client(monkeypatch, manager)

    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        raise RuntimeError("boom")

    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect_and_apply, raising=False)

    response = client.post(f"/payment/accounts/{account_id}/sync-subscription", json={})

    assert response.status_code == 500
    assert response.json()["detail"] == "订阅检测失败: boom"
