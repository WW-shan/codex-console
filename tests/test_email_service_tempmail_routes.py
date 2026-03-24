import asyncio
from contextlib import contextmanager
from pathlib import Path

from src.config.constants import EmailServiceType
from src.database.models import Base, EmailService
from src.database.session import DatabaseSessionManager
from src.services.base import EmailServiceFactory
from src.web.routes import email as email_routes
from src.web.routes import registration as registration_routes


class DummySettings:
    custom_domain_base_url = ""
    custom_domain_api_key = None


def test_temp_mail_service_registered():
    service_type = EmailServiceType("temp_mail")
    service_class = EmailServiceFactory.get_service_class(service_type)
    assert service_class is not None
    assert service_class.__name__ == "TempMailService"


def test_filter_sensitive_config_keeps_tempmail_domains():
    filtered = email_routes.filter_sensitive_config({
        "base_url": "https://mail.example.com",
        "admin_password": "secret",
        "domain": "example.com",
        "domains": ["example.com", "mail.example.com"],
    })

    assert filtered["base_url"] == "https://mail.example.com"
    assert filtered["domain"] == "example.com"
    assert filtered["domains"] == ["example.com", "mail.example.com"]
    assert filtered["has_admin_password"] is True
    assert "admin_password" not in filtered


def test_normalize_temp_mail_config_supports_domains_and_default_domain():
    normalized = registration_routes._normalize_email_service_config(
        EmailServiceType.TEMP_MAIL,
        {
            "base_url": "https://mail.example.com",
            "default_domain": "@example.com",
            "domains": "mail.example.com\nexample.com，alt.example.com",
        },
    )

    assert normalized["domain"] == "example.com"
    assert normalized["domains"] == ["example.com", "mail.example.com", "alt.example.com"]


def test_registration_available_services_include_tempmail_primary_domain(monkeypatch):
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "tempmail_routes.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        session.add(
            EmailService(
                service_type="temp_mail",
                name="TempMail 主服务",
                config={
                    "base_url": "https://mail.example.com",
                    "admin_password": "secret",
                    "domain": "example.com",
                    "domains": ["example.com", "mail.example.com"],
                },
                enabled=True,
                priority=0,
            )
        )

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)

    import src.config.settings as settings_module

    monkeypatch.setattr(settings_module, "get_settings", lambda: DummySettings())

    result = asyncio.run(registration_routes.get_available_email_services())

    assert result["temp_mail"]["available"] is True
    assert result["temp_mail"]["count"] == 1
    assert result["temp_mail"]["services"][0]["name"] == "TempMail 主服务"
    assert result["temp_mail"]["services"][0]["type"] == "temp_mail"
    assert result["temp_mail"]["services"][0]["domain"] == "example.com"
