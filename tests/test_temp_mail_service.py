from src.services.temp_mail import TempMailService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeHTTPClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({
            "method": method,
            "url": url,
            "kwargs": kwargs,
        })
        if not self.responses:
            raise AssertionError(f"未准备响应: {method} {url}")
        return self.responses.pop(0)


def test_create_email_supports_legacy_single_domain():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={
            "address": "tester@example.com",
            "jwt": "jwt-123",
        })
    ])
    service.http_client = fake_client

    email_info = service.create_email()

    assert email_info["email"] == "tester@example.com"
    assert email_info["jwt"] == "jwt-123"
    create_call = fake_client.calls[0]
    assert create_call["method"] == "POST"
    assert create_call["url"] == "https://mail.example.com/admin/new_address"
    assert create_call["kwargs"]["json"]["domain"] == "example.com"
    assert service.config["domains"] == ["example.com"]
    assert service.config["domain"] == "example.com"


def test_create_email_randomly_uses_configured_domains(monkeypatch):
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "secret",
        "domains": ["example.com", "mail.example.com", "example.com"],
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={
            "address": "tester@mail.example.com",
            "jwt": "jwt-456",
        })
    ])
    service.http_client = fake_client
    monkeypatch.setattr("random.choice", lambda items: items[-1])

    email_info = service.create_email()

    assert email_info["email"] == "tester@mail.example.com"
    create_call = fake_client.calls[0]
    assert create_call["kwargs"]["json"]["domain"] == "mail.example.com"
    assert service.config["domains"] == ["example.com", "mail.example.com"]
    assert service.config["domain"] == "example.com"


def test_get_verification_code_fallbacks_to_admin_when_api_endpoint_fails():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(status_code=401, payload={"error": "unauthorized"}),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "text": "Your OpenAI verification code is 654321",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    email = "tester@example.com"
    service._email_cache[email] = {"jwt": "jwt-abc"}

    code = service.get_verification_code(email=email, timeout=1)

    assert code == "654321"
    assert fake_client.calls[0]["url"].endswith("/api/mails")
    assert fake_client.calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer jwt-abc"
    assert fake_client.calls[1]["url"].endswith("/admin/mails")
    assert fake_client.calls[1]["kwargs"]["params"]["address"] == email


def test_get_verification_code_without_jwt_uses_admin_only():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "123456 is your verification code",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="nojwt@example.com", timeout=1)

    assert code == "123456"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"].endswith("/admin/mails")


def test_get_verification_code_skips_last_used_mail_id_between_calls():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code #1",
                        "text": "111111 is your verification code",
                    }
                ],
                "total": 1,
            }
        ),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-1",
                        "source": "noreply@openai.com",
                        "subject": "Code #1",
                        "text": "111111 is your verification code",
                    },
                    {
                        "id": "mail-2",
                        "source": "noreply@openai.com",
                        "subject": "Code #2",
                        "text": "222222 is your verification code",
                    },
                ],
                "total": 2,
            }
        ),
    ])
    service.http_client = fake_client

    code_1 = service.get_verification_code(email="reuse@example.com", timeout=1)
    code_2 = service.get_verification_code(email="reuse@example.com", timeout=1)

    assert code_1 == "111111"
    assert code_2 == "222222"


def test_get_verification_code_filters_old_mails_by_otp_sent_at():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    otp_sent_at = 1_700_000_000.0
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-old",
                        "source": "noreply@openai.com",
                        "subject": "Old Code",
                        "text": "333333 is your verification code",
                        "createdAt": otp_sent_at - 30,
                    },
                    {
                        "id": "mail-new",
                        "source": "noreply@openai.com",
                        "subject": "New Code",
                        "text": "444444 is your verification code",
                        "createdAt": otp_sent_at + 5,
                    },
                ],
                "total": 2,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(
        email="filter@example.com",
        timeout=1,
        otp_sent_at=otp_sent_at,
    )

    assert code == "444444"


def test_get_verification_code_accepts_mails_key_and_missing_mail_id():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "mails": [
                    {
                        # 没有 id/mail_id 字段，验证回退 ID 逻辑
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "text": "Your verification code is 987654",
                        "createdAt": "2026-03-23 10:00:00",
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="format@example.com", timeout=1)

    assert code == "987654"


def test_get_verification_code_fetches_mail_detail_when_list_has_no_body():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-100",
                        "source": "noreply@openai.com",
                        "subject": "OpenAI verification",
                        "createdAt": "2026-03-23T10:00:00Z",
                    }
                ]
            }
        ),
        FakeResponse(
            payload={
                "id": "mail-100",
                "source": "noreply@openai.com",
                "subject": "OpenAI verification",
                "text": "Your OpenAI verification code is 246810",
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="detail@example.com", timeout=1)

    assert code == "246810"




def test_get_verification_code_accepts_worker_created_at_with_otp_sent_at():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "routew.shop",
    })
    otp_sent_at = 1_700_000_000.0
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-old",
                        "address": "wkldk0q@routew.shop",
                        "source": "bounce+old@tm1.openai.com",
                        "raw": (
                            "Date: Tue, 14 Nov 2023 22:12:50 +0000\r\n"
                            "Subject: Your ChatGPT code is 111111\r\n"
                            "From: OpenAI <otp@tm1.openai.com>\r\n\r\n"
                            "Your ChatGPT code is 111111"
                        ),
                        "created_at": "2023-11-14 22:12:50",
                    },
                    {
                        "id": "mail-new",
                        "address": "wkldk0q@routew.shop",
                        "source": "bounce+new@tm1.openai.com",
                        "raw": (
                            "Date: Tue, 14 Nov 2023 22:13:25 +0000\r\n"
                            "Subject: Your ChatGPT code is 222222\r\n"
                            "From: OpenAI <otp@tm1.openai.com>\r\n\r\n"
                            "Your ChatGPT code is 222222"
                        ),
                        "created_at": "2023-11-14 22:13:25",
                    },
                ],
                "total": 2,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(
        email="wkldk0q@routew.shop",
        timeout=1,
        otp_sent_at=otp_sent_at,
    )

    assert code == "222222"



def test_get_verification_code_falls_back_to_raw_date_header_when_created_at_missing():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "routew.shop",
    })
    otp_sent_at = 1_700_000_000.0
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-raw-date",
                        "address": "rawdate@routew.shop",
                        "source": "bounce+raw@tm1.openai.com",
                        "raw": (
                            "Date: Tue, 14 Nov 2023 22:13:25 +0000\r\n"
                            "Subject: Your ChatGPT code is 222222\r\n"
                            "From: OpenAI <otp@tm1.openai.com>\r\n\r\n"
                            "Your ChatGPT code is 222222"
                        ),
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(
        email="rawdate@routew.shop",
        timeout=1,
        otp_sent_at=otp_sent_at,
    )

    assert code == "222222"



def test_get_verification_code_reads_worker_ai_extract_metadata_result():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "wwcloud.lol",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-meta",
                        "address": "meta@wwcloud.lol",
                        "source": "bounce+meta@tm1.openai.com",
                        "subject": "OpenAI verification",
                        "created_at": "2026-03-25 19:39:51",
                        "metadata": '{"ai_extract": {"result": "555666"}}',
                    }
                ],
                "total": 1,
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="meta@wwcloud.lol", timeout=1)

    assert code == "555666"



def test_get_verification_code_admin_unfiltered_fallback_uses_safe_limit():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": []}),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-200",
                        "address": "target@example.com",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "135790 is your verification code",
                    },
                ]
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="target@example.com", timeout=1)

    assert code == "135790"
    assert fake_client.calls[1]["kwargs"]["params"]["limit"] == 80



def test_get_verification_code_admin_unfiltered_fallback():
    service = TempMailService({
        "base_url": "https://mail.example.com",
        "admin_password": "admin-secret",
        "domain": "example.com",
    })
    fake_client = FakeHTTPClient([
        FakeResponse(payload={"results": []}),
        FakeResponse(
            payload={
                "results": [
                    {
                        "id": "mail-200",
                        "address": "target@example.com",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "135790 is your verification code",
                    },
                    {
                        "id": "mail-201",
                        "address": "other@example.com",
                        "source": "noreply@openai.com",
                        "subject": "Code",
                        "text": "111111 is your verification code",
                    },
                ]
            }
        ),
    ])
    service.http_client = fake_client

    code = service.get_verification_code(email="target@example.com", timeout=1)

    assert code == "135790"
