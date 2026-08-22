from __future__ import annotations

import os
import subprocess
import sys

from django.test import Client, override_settings


def _production_settings(**overrides):
    environment = os.environ.copy()
    for name in (
        "GABAY_SECRET_KEY",
        "GABAY_DATABASE_URL",
        "GABAY_ALLOWED_HOSTS",
        "GABAY_CSRF_TRUSTED_ORIGINS",
        "RAILWAY_PUBLIC_DOMAIN",
    ):
        environment.pop(name, None)
    environment.update({"GABAY_PROFILE": "production", **overrides})
    return subprocess.run(
        [sys.executable, "-c", "import gabay.settings as s; print(s.ALLOWED_HOSTS)"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_fails_closed_without_secret():
    result = _production_settings(
        GABAY_DATABASE_URL="postgresql://gabay:gabay@127.0.0.1/gabay"
    )

    assert result.returncode != 0
    assert "GABAY_SECRET_KEY must be a private production value" in result.stderr


def test_production_fails_closed_without_postgres_url():
    result = _production_settings(
        GABAY_SECRET_KEY="test-only-long-production-secret-0123456789-ABCDEF"
    )

    assert result.returncode != 0
    assert "GABAY_DATABASE_URL must point" in result.stderr


def test_railway_domain_and_proxy_tls_are_configured():
    script = (
        "import gabay.settings as s; "
        "print(s.ALLOWED_HOSTS); "
        "print(s.CSRF_TRUSTED_ORIGINS); "
        "print(s.SECURE_PROXY_SSL_HEADER)"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "GABAY_PROFILE": "production",
            "GABAY_SECRET_KEY": "test-only-long-production-secret-0123456789-ABCDEF",
            "GABAY_DATABASE_URL": "postgresql://gabay:gabay@127.0.0.1/gabay",
            "RAILWAY_PUBLIC_DOMAIN": "gabay.example.railway.app",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "gabay.example.railway.app" in result.stdout
    assert "https://gabay.example.railway.app" in result.stdout
    assert "HTTP_X_FORWARDED_PROTO" in result.stdout


@override_settings(
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    ALLOWED_HOSTS=["testserver"],
)
def test_trusted_proxy_https_request_does_not_redirect():
    response = Client().get("/health/", HTTP_X_FORWARDED_PROTO="https")

    assert response.status_code == 200
