"""Django settings, derived from config.yml (see conf.py)."""

import os
from pathlib import Path

import dj_database_url

from feedxcavator import conf

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY = conf.secret_key()
DEBUG = False
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "feedxcavator",
]

MIDDLEWARE = [
    "feedxcavator.middleware.RequestContextMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "feedxcavator.middleware.AccessMiddleware",
]

ROOT_URLCONF = "feedxcavator.urls"
ASGI_APPLICATION = "feedxcavator.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]


def _database():
    url = os.environ.get("FEEDXCAVATOR_DATABASE_URL") or conf.get("database-connection-url")
    db = dj_database_url.parse(url)
    if db["ENGINE"].endswith("sqlite3"):
        name = Path(db["NAME"])
        if db["NAME"] != ":memory:" and not name.is_absolute():
            name = conf.HOME / name
            name.parent.mkdir(parents=True, exist_ok=True)
            db["NAME"] = str(name)
        db["OPTIONS"] = {
            "timeout": 30,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=30000;",
        }
    return db


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = []

USE_TZ = True
TIME_ZONE = "UTC"
USE_I18N = False

# AUTH2-2
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = False  # the console reads it to send X-CSRFToken
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_secure = str(conf.get("hostname") or "").startswith("https://")
SESSION_COOKIE_SECURE = _secure
CSRF_COOKIE_SECURE = _secure
USE_X_FORWARDED_HOST = True
X_FRAME_OPTIONS = "SAMEORIGIN"

LOGIN_URL = "/login"
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"level": "ERROR"},
        "watchfiles": {"level": "WARNING"},
        "trafilatura": {"level": "ERROR"},
        "telethon": {"level": "WARNING"},
    },
}
