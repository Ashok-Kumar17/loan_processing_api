"""Django settings for the LAMF credit-line API.

Everything environment-specific is read from environment variables so the same
settings module runs locally (SQLite, zero setup), in CI (PostgreSQL service
container) and in Docker Compose (PostgreSQL + gunicorn).
"""

import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-in-production-0123456789")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "core",
    "accounts",
    "partners",
    "lending",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:  # runserver serves static files itself in development
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "loan_processing_api.urls"
WSGI_APPLICATION = "loan_processing_api.wsgi.application"
ASGI_APPLICATION = "loan_processing_api.asgi.application"

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
            ],
        },
    },
]

# DATABASE_URL examples:
#   (unset)                                         -> SQLite file db.sqlite3
#   postgres://postgres:postgres@localhost:5432/lamf -> PostgreSQL
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Hashed, compressed files in production (needs collectstatic); plain files in dev and tests.
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "auth": os.environ.get("AUTH_THROTTLE_RATE", "20/min"),
    },
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "LAMF Credit Line API",
    "DESCRIPTION": (
        "Loan-against-mutual-funds backend: portfolio fetch from RTAs, credit-bureau "
        "check, lien marking, idempotent drawdowns and repayments, daily interest "
        "accrual and a partner (MFD/RIA) dashboard."
    ),
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/",  # tags endpoints by area: auth, portfolio, credit-lines, partner, ops
    # Serve Swagger UI assets from the app itself (works offline, no CDN).
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
}

# Field-level encryption for PAN. In production set FIELD_ENCRYPTION_KEY to a
# Fernet key (python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").
# Locally a key is derived from SECRET_KEY so the project runs with zero setup.
FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")

# ---- Lending policy (all configurable) -------------------------------------
LENDING = {
    "MIN_LIMIT": Decimal("10000.00"),  # Rs 10,000
    "MAX_LIMIT": Decimal("250000000.00"),  # Rs 25 crore
    "DEFAULT_APR": Decimal("10.40"),  # % per annum, simple, on utilised amount
    "MIN_BUREAU_SCORE": 700,
    # Loan-to-value applied to the market value of pledged units, by scheme category.
    "LTV": {
        "EQUITY": Decimal("0.50"),
        "HYBRID": Decimal("0.60"),
        "DEBT": Decimal("0.80"),
    },
    # Trail commission paid to the referring partner, % per annum of outstanding principal.
    "PARTNER_TRAIL_RATE": Decimal("0.50"),
}

# Where a partner's referral link points (the borrower sign-up page of the app).
PARTNER_SIGNUP_URL = os.environ.get("PARTNER_SIGNUP_URL", "https://app.example.com/signup?ref={code}")

INTEGRATIONS = {
    "RTA_PROVIDERS": ["integrations.rta.MockCAMSClient", "integrations.rta.MockKFintechClient"],
    "BUREAU_PROVIDER": "integrations.bureau.MockBureauClient",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"lending": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")}},
}

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
