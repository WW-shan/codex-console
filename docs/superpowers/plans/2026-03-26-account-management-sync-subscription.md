# Account Management Sync Subscription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-account “同步订阅” action to account management that reuses the proven bind-card subscription sync path without requiring any bind-card task.

**Architecture:** Keep all subscription detection behavior in `src/web/routes/payment.py` and avoid creating a third copy of the writeback rules. Extract a shared helper for “detect + apply subscription result”, use it from both the existing bind-card sync endpoint and the new account-level sync endpoint, then expose the new endpoint in `static/js/accounts.js` through the existing “更多” menu.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, existing payment/account route modules, existing vanilla JS frontend, pytest

---

### File Structure

**Files to modify:**
- `src/web/routes/payment.py` — add a shared helper for subscription result writeback, add the new account-level sync endpoint, and switch existing endpoints to reuse the helper
- `static/js/accounts.js` — add the new “同步订阅” menu item and frontend action that posts `{}` to the new endpoint and shows diagnostic toast text

**Files to create:**
- `tests/test_payment_subscription_sync.py` — focused backend tests for the new account-level sync endpoint and shared writeback behavior

**Reference files to read while implementing:**
- `docs/superpowers/specs/2026-03-26-account-management-sync-subscription-design.md`
- `src/web/routes/payment.py:1805-1888` (`_check_subscription_detail_with_retry`)
- `src/web/routes/payment.py:2981-3076` (`sync_bind_card_task_subscription`)
- `src/web/routes/payment.py:3244-3302` (`batch_check_subscription`)
- `static/js/payment.js:1809-1826` (`syncBindCardTask`)
- `static/js/accounts.js:320-329` (current “更多” menu)
- `static/js/accounts.js:1004-1019` (`markSubscription` pattern)
- `tests/test_email_service_tempmail_routes.py` (project style for DB-backed route tests)

---

### Task 1: Add backend regression tests for account-level sync subscription

**Files:**
- Create: `tests/test_payment_subscription_sync.py`
- Test: `tests/test_payment_subscription_sync.py`

- [ ] **Step 1: Write the failing test for paid subscription writeback through the new endpoint**

```python
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.database.models import Base, Account
from src.database.session import DatabaseSessionManager
from src.web.routes import payment as payment_routes


def test_account_sync_subscription_updates_team_status(monkeypatch):
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "payment_sync_team.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        session.add(Account(
            email="teamer@example.com",
            email_service="manual",
            status="active",
            access_token="token",
            subscription_type=None,
        ))

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(payment_routes.router, prefix="/payment")
    app.dependency_overrides[payment_routes.get_db] = fake_get_db
    client = TestClient(app)

    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        return {
            "status": "team",
            "detail": {
                "status": "team",
                "source": "wham_usage.no_scope.plan",
                "confidence": "medium",
                "note": "checked_without_scope",
            },
            "refreshed": True,
            "checked_at": None,
        }

    monkeypatch.setattr(payment_routes, "_detect_and_apply_subscription_result", fake_detect_and_apply)

    response = client.post("/payment/accounts/1/sync-subscription", json={})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "success": True,
        "subscription_type": "team",
        "detail": {
            "status": "team",
            "source": "wham_usage.no_scope.plan",
            "confidence": "medium",
            "note": "checked_without_scope",
        },
        "account_id": 1,
        "account_email": "teamer@example.com",
    }
```

Use `TestClient` here so the test verifies the real `POST .../sync-subscription` request contract with `json={}` instead of only direct Python calls. Re-open the DB session after the request and assert persisted `account.subscription_type == "team"`.

- [ ] **Step 2: Run the targeted paid-subscription test to verify it fails first**

Run: `pytest tests/test_payment_subscription_sync.py -k "updates_team_status" -v`
Expected: FAIL because `/payment/accounts/{id}/sync-subscription` does not exist yet.

- [ ] **Step 3: Write the failing test for low-confidence free preserving an existing paid subscription**

```python
def test_account_sync_subscription_keeps_existing_team_on_low_confidence_free(monkeypatch):
    ...
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
            "checked_at": None,
        }
```

Assert that:
- HTTP status is `200`
- response still includes required `account_id`, `account_email`, `detail.status`, `detail.source`, `detail.confidence`, `detail.note`
- persisted `account.subscription_type` remains `"team"`

- [ ] **Step 4: Run the low-confidence free test to verify it fails first**

Run: `pytest tests/test_payment_subscription_sync.py -k "keeps_existing_team" -v`
Expected: FAIL before implementation.

- [ ] **Step 5: Write the failing test for high-confidence free clearing an existing paid subscription**

```python
def test_account_sync_subscription_clears_existing_team_on_high_confidence_free(monkeypatch):
    ...
    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        return {
            "status": "free",
            "detail": {
                "status": "free",
                "source": "explicit_free.plan",
                "confidence": "high",
                "note": "explicit_free=basic",
            },
            "refreshed": False,
            "checked_at": None,
        }
```

Assert that the persisted `account.subscription_type is None` and `account.subscription_at is None` after the request.

- [ ] **Step 6: Run the high-confidence free test to verify it fails first**

Run: `pytest tests/test_payment_subscription_sync.py -k "clears_existing_team" -v`
Expected: FAIL before implementation.

- [ ] **Step 7: Write the failing test for 404 when the account does not exist**

```python
def test_account_sync_subscription_returns_404_for_missing_account(client):
    response = client.post("/payment/accounts/999/sync-subscription", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "账号不存在"
```

- [ ] **Step 8: Write the failing test for 500 when subscription detection fails**

```python
def test_account_sync_subscription_returns_500_when_detection_fails(monkeypatch, client):
    def fake_detect_and_apply(*, db, account, proxy, allow_token_refresh):
        raise RuntimeError("boom")
```

Assert `500` and `detail` starts with `订阅检测失败:`.

- [ ] **Step 9: Run the full new backend test file and confirm at least one test fails**

Run: `pytest tests/test_payment_subscription_sync.py -v`
Expected: FAIL before implementation because the new shared helper and endpoint are missing.

- [ ] **Step 10: Commit the failing tests scaffold once it matches the intended behavior**

```bash
git add tests/test_payment_subscription_sync.py
git commit -m "test: add account sync subscription regressions"
```

Only do this after the failing tests are in place and readable.

---

### Task 2: Extract a shared detect-and-writeback helper and add the account-level endpoint

**Files:**
- Modify: `src/web/routes/payment.py:1805-1888`
- Modify: `src/web/routes/payment.py:1987-1990`
- Modify: `src/web/routes/payment.py:2981-3076`
- Modify: `src/web/routes/payment.py:3244-3302`
- Modify: `src/web/routes/payment.py:3305-3321`
- Test: `tests/test_payment_subscription_sync.py`

- [ ] **Step 1: Add one shared helper that performs both detection and account writeback**

Add a focused helper near the existing subscription sync code, for example:

```python
def _detect_and_apply_subscription_result(
    *,
    db,
    account: Account,
    proxy: Optional[str],
    allow_token_refresh: bool,
) -> dict:
    detail, refreshed = _check_subscription_detail_with_retry(
        db=db,
        account=account,
        proxy=proxy,
        allow_token_refresh=allow_token_refresh,
    )
    now = datetime.utcnow()
    status = str(detail.get("status") or "free").lower()
    confidence = str(detail.get("confidence") or "low").lower()

    if status in ("plus", "team"):
        account.subscription_type = status
        account.subscription_at = now
    elif status == "free" and confidence == "high":
        account.subscription_type = None
        account.subscription_at = None

    return {
        "status": status,
        "detail": detail,
        "refreshed": refreshed,
        "checked_at": now,
    }
```

This helper is the required convergence point. It must be used by:
- the new account-level sync endpoint
- `sync_bind_card_task_subscription()`
- `batch_check_subscription()`

Keep task-status mutation outside this helper.

- [ ] **Step 2: Add the new account-level sync route using the existing `{}`-compatible request model**

Add:

```python
@router.post("/accounts/{account_id}/sync-subscription")
def sync_account_subscription(account_id: int, request: SyncBindCardTaskRequest):
    ...
```

Implementation requirements:
- load account by id
- 404 if missing
- resolve proxy with `_resolve_runtime_proxy(request.proxy, account)`
- call `_detect_and_apply_subscription_result(...)`
- commit once after writeback
- on detection failure, return `HTTPException(status_code=500, detail=f"订阅检测失败: {exc}")`
- return the exact spec contract with required top-level fields and required `detail.status/source/confidence/note`

- [ ] **Step 3: Update the existing bind-card sync endpoint to use the shared detect-and-writeback helper**

In `sync_bind_card_task_subscription()`:
- replace the inline call to `_check_subscription_detail_with_retry(...)`
- replace the inline `plus/team/free` account writeback block
- read `status`, `detail`, `refreshed`, and `checked_at` from the shared helper result
- preserve all existing task-status behavior and response fields
- do not change bind-card task UX or text

- [ ] **Step 4: Update batch subscription check to use the same shared helper**

In `batch_check_subscription()`:
- replace the inline detection call and inline account writeback block with the shared helper
- keep the existing batch response shape unchanged
- preserve `token_refreshed` from helper output

- [ ] **Step 5: Run the new targeted backend tests and make them pass**

Run: `pytest tests/test_payment_subscription_sync.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing nearby regression test to ensure no drift in overview behavior**

Run: `pytest tests/test_accounts_overview_refresh.py -v`
Expected: PASS.

- [ ] **Step 7: Commit the backend implementation**

```bash
git add src/web/routes/payment.py tests/test_payment_subscription_sync.py
git commit -m "feat: add account-level subscription sync"
```

---

### Task 3: Add the account-management frontend action in the existing dropdown menu

**Files:**
- Modify: `static/js/accounts.js:320-329`
- Modify: `static/js/accounts.js:1004-1039`
- Modify: `static/js/accounts.js:1300-1341`
- Test: manual browser verification

- [ ] **Step 1: Add the new dropdown item to the existing “更多” menu**

Update the menu render block so the order is exactly:

```html
<a ... onclick="...refreshToken(${account.id})">刷新</a>
<a ... onclick="...uploadAccount(${account.id})">上传</a>
<a ... onclick="...syncAccountSubscription(${account.id})">同步订阅</a>
<a ... onclick="...markSubscription(${account.id})">标记</a>
```

Do not move the action out of the dropdown.

- [ ] **Step 2: Implement `syncAccountSubscription(id)` using the same UX style as nearby account actions**

Add a function near `markSubscription()` / batch subscription functions:

```javascript
async function syncAccountSubscription(id) {
    try {
        const data = await api.post(`/payment/accounts/${id}/sync-subscription`, {});
        const sub = String(data?.subscription_type || "free").toUpperCase();
        const source = String(data?.detail?.source || "unknown");
        const confidence = String(data?.detail?.confidence || "unknown");
        const note = String(data?.detail?.note || "");
        ...
        await loadAccounts();
    } catch (e) {
        toast.error("同步订阅失败: " + e.message);
    }
}
```

Use the same message style as `static/js/payment.js:1809-1826`:
- success toast for `PLUS/TEAM`
- warning toast for `FREE`
- include `source/confidence/note`

- [ ] **Step 3: Export the new function on `window` with the other account-management actions**

Add:

```javascript
window.syncAccountSubscription = syncAccountSubscription;
```

Place it with the existing `window.*` assignments at the bottom of `static/js/accounts.js`.

- [ ] **Step 4: Verify the frontend file has no obvious syntax mistakes**

Run a focused syntax sanity check using Node if available:

Run: `node --check static/js/accounts.js`
Expected: no output / zero exit code.

If Node is unavailable, skip this and rely on the browser/manual verification step instead.

- [ ] **Step 5: Commit the frontend wiring**

```bash
git add static/js/accounts.js
git commit -m "feat: add account sync subscription action"
```

---

### Task 4: Verify end-to-end behavior before handoff

**Files:**
- Modify: none
- Test: `tests/test_payment_subscription_sync.py`
- Test: `tests/test_accounts_overview_refresh.py`
- Test: manual account-management flow

- [ ] **Step 1: Run the focused automated regression suite together**

Run: `pytest tests/test_payment_subscription_sync.py tests/test_accounts_overview_refresh.py -v`
Expected: PASS.

- [ ] **Step 2: Manually verify the account-management dropdown flow**

Use a real account whose current DB subscription is stale but can be corrected by the existing bind-card sync logic:
- open account management
- click `更多 -> 同步订阅`
- confirm toast includes status/source/confidence/note
- confirm the account list subscription badge updates after reload

- [ ] **Step 3: Cross-check the same account through bind-card sync for consistency**

For the same account (or an equivalent fixture account):
- run bind-card task `同步订阅`
- verify the resulting subscription classification matches the account-management action

- [ ] **Step 4: Review changed code for duplication before finishing**

Check specifically that:
- there is one shared helper for account writeback
- `sync_bind_card_task_subscription()` and `batch_check_subscription()` both use it
- no second/third copy of the `plus/team/free + confidence` branching remains except where task-specific status handling requires it

- [ ] **Step 5: Final commit for any verification-driven fixes**

```bash
git add src/web/routes/payment.py static/js/accounts.js tests/test_payment_subscription_sync.py
git commit -m "test: verify account subscription sync flow"
```

Only create this commit if verification required follow-up fixes after the earlier commits.
