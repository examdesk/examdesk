import os
from datetime import timedelta
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-examdesk-development-only")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_cotton",
    "django_tailwind_cli",
    "examdesk",  # before allauth, so its account/ templates win
    "allauth",
    "allauth.account",
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
    "django_htmx.middleware.HtmxMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "examdesk.urls"
WSGI_APPLICATION = "examdesk.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "examdesk.context_processors.config",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60, conn_health_checks=True
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"] = {"transaction_mode": "IMMEDIATE"}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-sg"
TIME_ZONE = "Asia/Singapore"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("EXAMDESK_STATIC_ROOT", BASE_DIR / "static"))
STATICFILES_DIRS = [BASE_DIR / "assets"]
TAILWIND_CLI_SRC_CSS = "examdesk/tailwind.css"
TAILWIND_CLI_USE_DAISY_UI = True
TAILWIND_CLI_VERSION = "2.10.32"  # tailwind-cli-extra
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
# Never served directly: the photo view checks exam membership.
MEDIA_ROOT = Path(os.environ.get("EXAMDESK_MEDIA_ROOT", BASE_DIR / "media"))

# Sign-in codes go out by SMTP, or to the console when no host is set.
_email_port = int(os.environ.get("DJANGO_EMAIL_PORT", 587))
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": os.environ["DJANGO_EMAIL_HOST"],
            "port": _email_port,
            "username": os.environ.get("DJANGO_EMAIL_USER", ""),
            "password": os.environ.get("DJANGO_EMAIL_PASSWORD", ""),
            "use_tls": _email_port == 587,
            "use_ssl": _email_port == 465,
            "timeout": 10,
        },
    }
    if os.environ.get("DJANGO_EMAIL_HOST")
    else {"BACKEND": "django.core.mail.backends.console.EmailBackend"}
}
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "Exam Desk <examdesk@localhost>")

# Shared by all workers, so sign-in rate limits hold. Needs `manage.py createcachetable`.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "cache"}
}

# Sign-in by emailed code, without passwords. A first sign-in goes through sign-up when
# EXAMDESK_REGISTRATION is on; see examdesk/accounts.py.
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_ADAPTER = "examdesk.accounts.AccountAdapter"
ACCOUNT_SIGNUP_FORM_CLASS = "examdesk.forms.SignupForm"
ACCOUNT_FORMS = {"login": "examdesk.accounts.LoginForm"}
ACCOUNT_USER_DISPLAY = "examdesk.accounts.display_name"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 600
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[Exam Desk] "
ACCOUNT_RATE_LIMITS = {"request_login_code": "20/m/ip,3/m/key,10/h/key"}
# Proxies in front that append to X-Forwarded-For, so rate limits see the client's address.
ALLAUTH_TRUSTED_PROXY_COUNT = int(os.environ.get("EXAMDESK_PROXY_COUNT", 0 if DEBUG else 1))

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True


# Exam Desk settings; see the README for what each one means.
def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _seconds(name: str, default: int) -> timedelta:
    return timedelta(seconds=_int(name, default))


def _list(name: str, default: str, sep: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(sep) if item.strip()]


EXAMDESK_POLL_EVERY = _seconds("EXAMDESK_POLL_EVERY", 2)
EXAMDESK_OFFLINE_AFTER = _seconds("EXAMDESK_OFFLINE_AFTER", 6)
EXAMDESK_SEEN_EVERY = _seconds("EXAMDESK_SEEN_EVERY", 30)
EXAMDESK_ONLINE_FOR = _seconds("EXAMDESK_ONLINE_FOR", 60)
EXAMDESK_SEAT_CLAIM_FOR = _seconds("EXAMDESK_SEAT_CLAIM_FOR", 300)
EXAMDESK_SNOOZE_FOR = _seconds("EXAMDESK_SNOOZE_FOR", 300)
EXAMDESK_LATE_AFTER = _seconds("EXAMDESK_LATE_AFTER", 300)
EXAMDESK_VERY_LATE_AFTER = _seconds("EXAMDESK_VERY_LATE_AFTER", 600)
EXAMDESK_CANNED_ANSWERS = _list(
    "EXAMDESK_CANNED_ANSWERS",
    "No comment.|Read the question carefully.|State your assumptions.",
    sep="|",
)
EXAMDESK_SIMILARITY_THRESHOLD = float(os.environ.get("EXAMDESK_SIMILARITY_THRESHOLD", 0.5))
EXAMDESK_MODEL_REPO = os.environ.get("EXAMDESK_MODEL_REPO", "Xenova/all-MiniLM-L6-v2")
EXAMDESK_MODEL_REVISION = os.environ.get(
    "EXAMDESK_MODEL_REVISION", "751bff37182d3f1213fa05d7196b954e230abad9"
)
EXAMDESK_MODEL_FILES = _list(
    "EXAMDESK_MODEL_FILES", "onnx/model_quantized.onnx,tokenizer.json", sep=","
)
EXAMDESK_MODEL_MAX_TOKENS = _int("EXAMDESK_MODEL_MAX_TOKENS", 256)
EXAMDESK_PHOTO_MAX_SIDE = _int("EXAMDESK_PHOTO_MAX_SIDE", 1600)
EXAMDESK_PHOTO_MAX_BYTES = _int("EXAMDESK_PHOTO_MAX_BYTES", 200_000)
EXAMDESK_PHOTO_UPLOAD_MAX_BYTES = _int("EXAMDESK_PHOTO_UPLOAD_MAX_BYTES", 10_000_000)
EXAMDESK_PHOTO_CACHE_FOR = _seconds("EXAMDESK_PHOTO_CACHE_FOR", 86400)
EXAMDESK_REGISTRATION = os.environ.get("EXAMDESK_REGISTRATION", "1") == "1"
EXAMDESK_REGISTRATION_DOMAINS = _list("EXAMDESK_REGISTRATION_DOMAINS", "", sep=",")
EXAMDESK_BACKUP_DAYS = _int("EXAMDESK_BACKUP_DAYS", 14)
EXAMDESK_TEXT_MAX = _int("EXAMDESK_TEXT_MAX", 2000)
