import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-change-in-production",
)
DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# --- Apps ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "shopify_auth",
    "core",
    "vintedge_api",
]

# --- Middleware ---
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "core.middleware.ShopifyEmbedMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "stockpilot.urls"

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
                "core.context_processors.shopify_app",
            ],
        },
    },
]

WSGI_APPLICATION = "stockpilot.wsgi.application"

# --- Database ---
# SQLite for dev, PostgreSQL for production (Railway)
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES = {"default": dj_database_url.config(conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- Auth ---
AUTH_PASSWORD_VALIDATORS = []

# --- i18n ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Session stored in cookies (no DB call per request) ---
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

# --- Cache (in-memory, avoids slow Neon DB round-trips) ---
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "stockpilot-cache",
        "TIMEOUT": 300,  # 5 min default
    }
}

# --- Redis / RQ (for async webhook processing) ---
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
RQ_QUEUES = {
    "default": {
        "URL": REDIS_URL,
        "DEFAULT_TIMEOUT": 300,
    },
}

# --- Shopify App Config ---
SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET", "")
SHOPIFY_API_SCOPES = [
    "read_products",
    "read_inventory",
    "write_inventory",
    "read_orders",
    "read_locations",
]
SHOPIFY_API_VERSION = "2026-04"
SHOPIFY_APP_URL = os.environ.get("SHOPIFY_APP_URL", "https://localhost:8000")

# --- Billing Plans ---
STOCKPILOT_PLANS = {
    "starter": {
        "name": "Starter",
        "price": 19.00,
        "sku_limit": 200,
        "po_limit": 10,
        "supplier_limit": 1,
        "trial_days": 14,
    },
    "growth": {
        "name": "Growth",
        "price": 39.00,
        "sku_limit": 2000,
        "po_limit": None,  # unlimited
        "supplier_limit": None,
        "trial_days": 14,
    },
    "pro": {
        "name": "Pro",
        "price": 79.00,
        "sku_limit": None,
        "po_limit": None,
        "supplier_limit": None,
        "trial_days": 14,
    },
}

# Content Security Policy for Shopify embedded app
SECURE_CONTENT_TYPE_NOSNIFF = True

# CSRF settings for Shopify embedded iframe
CSRF_TRUSTED_ORIGINS = [
    "https://stockpilot-v63z.onrender.com",
    "https://admin.shopify.com",
    "https://*.myshopify.com",
]
CSRF_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = True
