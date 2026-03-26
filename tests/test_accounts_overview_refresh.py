from src.database.models import Account
from src.web.routes import accounts as accounts_routes


def _make_account(**overrides):
    account = Account(
        email="tester@example.com",
        email_service="manual",
        status="active",
        access_token="access-token",
        account_id="old-account",
        workspace_id="old-workspace",
        subscription_type="team",
        extra_data={},
    )
    for key, value in overrides.items():
        setattr(account, key, value)
    return account


def test_get_account_overview_data_persists_refreshed_account_and_workspace_ids(monkeypatch):
    account = _make_account()

    def fake_fetch_codex_overview(_account, proxy=None):
        return {
            "plan_type": "Basic",
            "plan_source": "me.plan",
            "hourly_quota": {"status": "ok", "percentage": 10.0},
            "weekly_quota": {"status": "ok", "percentage": 20.0},
            "code_review_quota": {"status": "ok", "percentage": 30.0},
            "workspace_context": {
                "account_id": "new-account",
                "workspace_id": "new-workspace",
                "source": "me.workspace",
            },
        }

    monkeypatch.setattr(accounts_routes, "fetch_codex_overview", fake_fetch_codex_overview)

    overview, updated = accounts_routes._get_account_overview_data(None, account, force_refresh=True)

    assert updated is True
    assert overview[accounts_routes.OVERVIEW_EXTRA_DATA_KEY if False else "workspace_context"]["account_id"] == "new-account"
    assert account.account_id == "new-account"
    assert account.workspace_id == "new-workspace"
    assert account.subscription_type == "team"
    assert account.extra_data[accounts_routes.OVERVIEW_EXTRA_DATA_KEY]["workspace_context"]["workspace_id"] == "new-workspace"


def test_get_account_overview_data_updates_workspace_without_clearing_existing_account_id(monkeypatch):
    account = _make_account(account_id="kept-account", workspace_id="old-workspace")

    def fake_fetch_codex_overview(_account, proxy=None):
        return {
            "plan_type": "Basic",
            "plan_source": "me.plan",
            "hourly_quota": {"status": "ok", "percentage": 10.0},
            "weekly_quota": {"status": "ok", "percentage": 20.0},
            "code_review_quota": {"status": "ok", "percentage": 30.0},
            "workspace_context": {
                "account_id": "",
                "workspace_id": "new-workspace",
                "source": "me.workspace",
            },
        }

    monkeypatch.setattr(accounts_routes, "fetch_codex_overview", fake_fetch_codex_overview)

    overview, updated = accounts_routes._get_account_overview_data(None, account, force_refresh=True)

    assert updated is True
    assert overview["workspace_context"]["workspace_id"] == "new-workspace"
    assert account.account_id == "kept-account"
    assert account.workspace_id == "new-workspace"
