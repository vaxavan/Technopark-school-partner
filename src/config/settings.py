"""Настройки проекта. Все секреты и адреса берутся из переменных окружения (.env)."""

import os
import sys
from pathlib import Path

from celery.schedules import crontab
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # папка src
REPO_DIR = BASE_DIR.parent

TESTING = "pytest" in sys.modules
if TESTING:
    # Тесты не трогают внешние сервисы, даже если в .env заданы настоящие ключи
    if not os.getenv("TEST_WITH_POSTGRES"):
        os.environ["POSTGRES_HOST"] = ""
    os.environ.update({"DJANGO_DEBUG": "1", "LLM_BACKEND": "fake", "REDIS_URL": "",
                       "EMAIL_HOST": "", "MAIL_PAUSE_SECONDS": "0", "SNAPSHOT_BACKEND": "none",
                       "VK_ACCESS_TOKEN": "", "MAX_BOT_TOKEN": "", "VK_SERVICE_TOKEN": "",
                       "SITE_URL": "http://testserver", "DJANGO_ALLOWED_HOSTS": "testserver,localhost"})
else:
    load_dotenv(REPO_DIR / ".env")


def env(name, default=""):
    return os.getenv(name, default)


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "да")


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("Задайте DJANGO_SECRET_KEY в .env")

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
# Адрес, по которому фоновые задачи открывают страницы самой системы (в Docker — через nginx)
INTERNAL_SITE_URL = env("INTERNAL_SITE_URL", SITE_URL).rstrip("/")
if INTERNAL_SITE_URL != SITE_URL:
    from urllib.parse import urlparse as _u

    ALLOWED_HOSTS.append(_u(INTERNAL_SITE_URL).hostname)
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("http") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "core",
    "accounts",
    "submissions",
    "materials",
    "publishing",
    "partners",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.CurrentOrganizationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.organization",
            ],
        },
    },
]

# ---------- База данных ----------
if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "NAME": env("POSTGRES_DB", "technopark"),
            "USER": env("POSTGRES_USER", "technopark"),
            "PASSWORD": env("POSTGRES_PASSWORD", "technopark"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    # Запуск без Docker: локальный файл SQLite
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": REPO_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = REPO_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(REPO_DIR / "media")))
SERVE_MEDIA = env_bool("SERVE_MEDIA", True)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SECURE = SITE_URL.startswith("https")
CSRF_COOKIE_SECURE = SITE_URL.startswith("https")

DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------- Кэш (ограничение частоты публичной формы) ----------
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ---------- REST API ----------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "API системы управления публикациями",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------- Celery ----------
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", not bool(REDIS_URL))
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CRAWL_HOUR = env_int("CRAWL_HOUR", 6)
CELERY_BEAT_SCHEDULE = {
    "publish-due": {"task": "publishing.tasks.publish_due", "schedule": 60.0},
    "deliver-events": {"task": "core.tasks.deliver_events", "schedule": 30.0},
    "crawl-placements": {"task": "partners.tasks.crawl_all", "schedule": crontab(hour=CRAWL_HOUR, minute=0)},
    "send-reminders": {"task": "partners.tasks.send_reminders", "schedule": crontab(hour=CRAWL_HOUR + 3, minute=0)},
}

# ---------- Почта ----------
EMAIL_HOST = env("EMAIL_HOST")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = env_int("EMAIL_PORT", 465)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", EMAIL_PORT == 465)
    EMAIL_USE_TLS = not EMAIL_USE_SSL and env_bool("EMAIL_USE_TLS", True)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_TIMEOUT = 30
else:
    # Демо-режим: письма складываются файлами в media/outbox
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = MEDIA_ROOT / "outbox"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or env("EMAIL_HOST_USER") or "noreply@localhost"
MAIL_PAUSE_SECONDS = float(env("MAIL_PAUSE_SECONDS", "2") or 0)

# ---------- Внешние сервисы ----------
YANDEX_API_KEY = env("YANDEX_API_KEY")
YANDEX_FOLDER_ID = env("YANDEX_FOLDER_ID")
YANDEX_MODEL = env("YANDEX_MODEL", "yandexgpt/latest")
YANDEX_MODEL_LITE = env("YANDEX_MODEL_LITE", "yandexgpt-lite/latest")
LLM_BACKEND = env("LLM_BACKEND") or ("yandex" if YANDEX_API_KEY and YANDEX_FOLDER_ID else "fake")

VK_GROUP_ID = env("VK_GROUP_ID")
VK_ACCESS_TOKEN = env("VK_ACCESS_TOKEN")
VK_SERVICE_TOKEN = env("VK_SERVICE_TOKEN")
VK_API_VERSION = "5.199"

MAX_BOT_TOKEN = env("MAX_BOT_TOKEN")
MAX_CHAT_ID = env("MAX_CHAT_ID")
MAX_API_URL = env("MAX_API_URL", "https://platform-api2.max.ru")
MAX_CA_BUNDLE = env("MAX_CA_BUNDLE")

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

REMINDERS_MAX = env_int("REMINDERS_MAX", 2)
REMINDER_INTERVAL_DAYS = env_int("REMINDER_INTERVAL_DAYS", 3)
SNAPSHOT_BACKEND = env("SNAPSHOT_BACKEND", "playwright")
CRAWLER_USER_AGENT = "TechnoparkMonitor/1.0 (+контроль размещения материалов)"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
