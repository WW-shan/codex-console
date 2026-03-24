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
