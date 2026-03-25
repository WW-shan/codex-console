# TempMail Verification Retrieval Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore TempMail verification code retrieval for Cloudflare Worker inboxes that return `created_at`, `raw`, and `metadata.ai_extract` fields.

**Architecture:** Keep the existing TempMail polling flow and fix the root cause in `TempMailService`: consolidate timestamp parsing into a single implementation, support the Worker-specific timestamp sources actually observed in production (`created_at` and MIME `Date` inside `raw`), and add a safe fallback to Worker-side extracted OTP metadata. Verify behavior with focused regression tests that match the live Worker response shape.

**Tech Stack:** Python, pytest, existing TempMail service and fake HTTP client tests

---

### Task 1: Add regression tests for Worker mail payloads

**Files:**
- Modify: `tests/test_temp_mail_service.py`
- Test: `tests/test_temp_mail_service.py`

- [ ] **Step 1: Write the failing test for Worker `created_at` timestamp parsing**

```python
def test_get_verification_code_accepts_worker_created_at_with_otp_sent_at():
    ...
```

Cover this payload shape from the live Worker:
- `results: [...]`
- mail object contains `address`, `source`, `raw`, `created_at`
- no top-level `subject`/`text`
- pass `otp_sent_at` so the timestamp filtering path is exercised

Expected behavior:
- `get_verification_code(...)` returns the OTP from the new mail instead of timing out

- [ ] **Step 2: Run the targeted test to verify it fails first**

Run: `pytest tests/test_temp_mail_service.py -k "worker_created_at" -v`
Expected: FAIL because the current implementation does not reliably parse the Worker `created_at` field in this path

- [ ] **Step 3: Write the failing test for MIME `Date` fallback parsing from `raw`**

```python
def test_get_verification_code_falls_back_to_raw_date_header_when_created_at_missing():
    ...
```

Cover this payload shape:
- `results: [...]`
- mail object contains `raw` with a valid `Date:` header
- top-level timestamp fields are missing
- pass `otp_sent_at` so the raw-date fallback path is exercised directly

- [ ] **Step 4: Run the targeted raw-date test and keep it as a regression check**

Run: `pytest tests/test_temp_mail_service.py -k "raw_date_header" -v`
Expected: if the current implementation misses MIME `Date` fallback in this path, FAIL before implementation; if it already passes, keep the test and continue as a regression guard

- [ ] **Step 5: Write the failing test for Worker metadata OTP extraction**

```python
def test_get_verification_code_reads_worker_ai_extract_metadata_result():
    ...
```

Cover this payload shape:
- `metadata` is a JSON string
- nested path `ai_extract.result` contains the OTP
- message parsing path finds no code, so metadata is required as the fallback source

- [ ] **Step 6: Run the targeted metadata test to verify it fails first**

Run: `pytest tests/test_temp_mail_service.py -k "ai_extract" -v`
Expected: FAIL before implementation

### Task 2: Fix TempMail timestamp parsing at the root cause

**Files:**
- Modify: `src/services/temp_mail.py`
- Test: `tests/test_temp_mail_service.py`

- [ ] **Step 1: Remove the duplicate `_extract_mail_timestamp()` implementation**

Keep a single timestamp parser in `TempMailService` so later code does not silently override the earlier, more complete implementation.

- [ ] **Step 2: Make the surviving parser support the Worker timestamp sources actually needed for this bug**

Ensure the single implementation handles:
- `createdAt`
- `created_at`
- numeric timestamps already supported by `_parse_mail_timestamp`
- ISO strings already supported by `_parse_mail_timestamp`
- `YYYY-MM-DD HH:MM:SS` already supported by `_parse_mail_timestamp`
- MIME `Date` header parsed from `raw`

- [ ] **Step 3: Re-run the existing old-mail regression along with the new timestamp tests**

Run: `pytest tests/test_temp_mail_service.py::test_get_verification_code_filters_old_mails_by_otp_sent_at tests/test_temp_mail_service.py -k "worker_created_at or raw_date_header" -v`
Expected: PASS, confirming older matching mail is still ignored when `otp_sent_at` is newer

- [ ] **Step 4: Keep `otp_sent_at` filtering behavior unchanged except for the fixed timestamps**

Do not redesign polling. Only make the existing filtering logic see real timestamps for Worker payloads so new mails are no longer treated as unknown-timestamp candidates.

- [ ] **Step 5: Run the full timestamp-focused slice**

Run: `pytest tests/test_temp_mail_service.py -k "worker_created_at or raw_date_header or old_mails" -v`
Expected: PASS

### Task 3: Add Worker metadata OTP fallback

**Files:**
- Modify: `src/services/temp_mail.py`
- Test: `tests/test_temp_mail_service.py`

- [ ] **Step 1: Add a small helper to extract OTP from Worker metadata**

Implementation target:
- parse `mail["metadata"]` when it is a JSON string or dict
- read `metadata.ai_extract.result`
- validate the extracted value with the existing OTP regex logic before using it

- [ ] **Step 2: Use the metadata fallback only when normal content extraction finds no code**

Preserve the current precedence:
1. semantic match from message content
2. normal regex match from content/raw
3. metadata fallback

- [ ] **Step 3: Re-run focused TempMail service tests**

Run: `pytest tests/test_temp_mail_service.py -v`
Expected: PASS

### Task 4: Verify the existing call path

**Files:**
- Read/verify only: `src/core/register.py:2103-2117`
- Read/verify only: `src/web/routes/accounts.py:2275-2280`
- Test: `tests/test_temp_mail_service.py`
- Test: `tests/test_registration_engine.py`

- [ ] **Step 1: Confirm callers still pass the same arguments**

Verify no caller changes are needed for:
- `email`
- `email_id`
- `timeout`
- `pattern`
- `otp_sent_at`

- [ ] **Step 2: Run the narrow verification suite**

Run: `pytest tests/test_temp_mail_service.py tests/test_registration_engine.py -v`
Expected: PASS, with no regression in registration-side verification retrieval
