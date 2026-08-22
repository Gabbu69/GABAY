from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE = os.getenv("GABAY_PROFILE", "development").lower()
IS_PRODUCTION = PROFILE == "production"
IS_LITE = PROFILE == "lite"

_DEVELOPMENT_SECRET = "dev-only-gabay-secret-key"
SECRET_KEY = os.getenv("GABAY_SECRET_KEY", _DEVELOPMENT_SECRET)
if IS_PRODUCTION and (
    SECRET_KEY == _DEVELOPMENT_SECRET or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5
):
    raise ImproperlyConfigured(
        "GABAY_SECRET_KEY must be a private production value of at least 50 characters."
    )
DEBUG = PROFILE == "development"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("GABAY_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("GABAY_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
if RAILWAY_PUBLIC_DOMAIN:
    if RAILWAY_PUBLIC_DOMAIN not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)
    railway_origin = f"https://{RAILWAY_PUBLIC_DOMAIN}"
    if railway_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(railway_origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "prices.apps.PricesConfig",
    "forecasting.apps.ForecastingConfig",
    "portal.apps.PortalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gabay.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "portal.context_processors.site_context",
            ]
        },
    }
]
WSGI_APPLICATION = "gabay.wsgi.application"
ASGI_APPLICATION = "gabay.asgi.application"


def database_from_url(raw_url: str) -> dict:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("GABAY_DATABASE_URL must be a PostgreSQL URL")
    query = parse_qs(parsed.query)
    options = {}
    if query.get("sslmode"):
        options["sslmode"] = query["sslmode"][0]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": 60,
        "OPTIONS": options,
    }


DATA_DIR = Path(os.getenv("GABAY_DATA_DIR", BASE_DIR / "data")).resolve()
database_url = os.getenv("GABAY_DATABASE_URL", "").strip()
if IS_PRODUCTION and not database_url:
    raise ImproperlyConfigured("GABAY_DATABASE_URL must point to production PostgreSQL.")
if database_url:
    DATABASES = {"default": database_from_url(database_url)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATA_DIR / "gabay.sqlite3",
            "OPTIONS": {"timeout": 5},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("tl", "Filipino")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "staticfiles" if IS_LITE else BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if IS_PRODUCTION
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "uploads"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "portal:login"
LOGIN_REDIRECT_URL = "portal:manage-dashboard"
LOGOUT_REDIRECT_URL = "portal:home"
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

SESSION_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SECURE = IS_PRODUCTION
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = IS_PRODUCTION and os.getenv("GABAY_DISABLE_SSL_REDIRECT") != "1"
SECURE_HSTS_SECONDS = 31_536_000 if IS_PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_HSTS_PRELOAD = IS_PRODUCTION
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATIC_ROOT, exist_ok=True)

from gabay import db as _db  # noqa: E402,F401
