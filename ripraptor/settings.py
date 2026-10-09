"""
Django settings for RipRaptor.

Values that differ between environments are read from environment variables.
The defaults are for local development only.
"""

import os
import sys
from pathlib import Path

from ripraptor.caching import MEMORY_CACHE, cache_dir, shared_caches

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", default=True)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Set DJANGO_SECRET_KEY when DJANGO_DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
if DEBUG:
    # Lets a phone on the same wifi open the site at http://<your computer's IP>:8000/
    ALLOWED_HOSTS.append("*")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "content",
    "catalogue",
    "web",
    "inbox",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "web.middleware.PageViewMiddleware",
]

ROOT_URLCONF = "ripraptor.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "content.context_processors.site_content",
                "web.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "ripraptor.wsgi.application"

# SQLite shared by gunicorn, the cron jobs and the background worker. WAL mode
# lets readers and writers run at once; the busy timeout (20 s, longer than any
# tidy transaction) makes a second writer wait instead of failing; IMMEDIATE
# transactions take the write lock at the start of atomic() so two writers
# cannot deadlock mid-transaction. A visitor's request waits at most one busy
# timeout for its counts (catalogue.pricing.drop_if_locked), well inside
# gunicorn's 60 s request timeout.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=20000;",
        },
    }
}

# One cache shared by the web workers and every command, so a restock or a
# pre-order written by an import shows at once rather than when a worker's own
# copy expires. It is a folder of files (RIPRAPTOR_CACHE_DIR); a folder that
# cannot be created or written falls back to a per-process memory cache with a
# warning. The test run keeps a memory cache so it never reads or clears the
# development site's cache, and parallel test processes never share entries.
TESTING = sys.argv[1:2] == ["test"]
RIPRAPTOR_CACHE_DIR = cache_dir(os.environ, DEBUG, BASE_DIR)
CACHES = {"default": dict(MEMORY_CACHE)} if TESTING else shared_caches(RIPRAPTOR_CACHE_DIR)

# A clear of the list caches triggered by a save is skipped when this process
# cleared them less than this many seconds ago; the end of an import, a restock
# found by the stock watcher and the owner's fixes always clear. A shop read
# changes a few dozen listings, so this bounds the clears without leaving lists
# stale for the whole cache lifetime: a skipped clear makes the cached lists
# expire when the window closes. Tests clear on every save.
RIPRAPTOR_CACHE_CLEAR_SECONDS = 0 if TESTING else 30

# A clear waits for the transaction that changed the rows to commit: another
# worker could otherwise refill the lists from the rows as they were. Django's
# test cases never commit, so the tests clear at once unless a test turns this on.
RIPRAPTOR_CLEAR_AFTER_COMMIT = not TESTING

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if not DEBUG:
    # WhiteNoise serves the collected static files with hashed names, so
    # browsers can cache them for a month. In development Django serves them.
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 30
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Import progress and errors go to the console (and so to the cron log on a server).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(message)s", "datefmt": "%H:%M:%S"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "loggers": {"catalogue": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# RipRaptor settings ---------------------------------------------------------

# Brand name used in page titles and the wordmark.
RIPRAPTOR_SITE_NAME = "RipRaptor"

# Shown on the "How it works" page. Leave blank to hide the contact section.
RIPRAPTOR_CONTACT_EMAIL = os.environ.get("RIPRAPTOR_CONTACT_EMAIL", "")

# A listing that has not been checked for this many hours is treated as out of
# date. It still appears on the product page but is never used as the
# cheapest price.
RIPRAPTOR_STALE_AFTER_HOURS = int(os.environ.get("RIPRAPTOR_STALE_AFTER_HOURS", "72"))

# Window used for "Price drops this week" and "Popular this week".
RIPRAPTOR_TRENDING_DAYS = 7

# Length of the price history chart on product pages.
RIPRAPTOR_HISTORY_DAYS = 90

RIPRAPTOR_PAGE_SIZE = 24

# Trending and biggest savings are worked out from every priced product, so
# the result is kept for this many seconds.
RIPRAPTOR_HOME_CACHE_SECONDS = int(os.environ.get("RIPRAPTOR_HOME_CACHE_SECONDS", "300"))

# A product counts as "back in stock" for this long after a shop restocks it.
RIPRAPTOR_RESTOCK_HOURS = int(os.environ.get("RIPRAPTOR_RESTOCK_HOURS", "48"))

# Google AdSense publisher id (ca-pub-...). Empty means no adverts and no
# Google script on any page. Set it once AdSense has approved the site.
RIPRAPTOR_ADSENSE_CLIENT = os.environ.get("RIPRAPTOR_ADSENSE_CLIENT", "").strip()

# Free DB-IP country database for visitor countries on the Insights page.
# Empty, or no file there, means countries are not recorded. fetch_geoip downloads it.
RIPRAPTOR_GEOIP_DB = os.environ.get("RIPRAPTOR_GEOIP_DB", "").strip()

# Awin publisher id. Loads Awin's MasterTag on every public page so clicks
# through Awin shops are tracked. Empty means no Awin script.
RIPRAPTOR_AWIN_PUBLISHER_ID = os.environ.get("RIPRAPTOR_AWIN_PUBLISHER_ID", "3111686").strip()

# Amazon Associates: Product Advertising API keys and the tracking tag
# (ripraptor-21). All three empty means Amazon is not read.
RIPRAPTOR_AMAZON_ACCESS_KEY = os.environ.get("RIPRAPTOR_AMAZON_ACCESS_KEY", "").strip()
RIPRAPTOR_AMAZON_SECRET_KEY = os.environ.get("RIPRAPTOR_AMAZON_SECRET_KEY", "").strip()
RIPRAPTOR_AMAZON_PARTNER_TAG = os.environ.get("RIPRAPTOR_AMAZON_PARTNER_TAG", "").strip()
# New products looked up on Amazon per daily run. Amazon allows 8,640 calls a day at first.
RIPRAPTOR_AMAZON_DAILY_LIMIT = int(os.environ.get("RIPRAPTOR_AMAZON_DAILY_LIMIT", "2000"))

# eBay Partner Network: a developer keyset (App ID and Cert ID from
# developer.ebay.com, production) and the EPN campaign id. All three set
# means eBay is read once a day.
# Back-in-stock emails, sent through Zoho ZeptoMail. All of token and from address set turns them on.
RIPRAPTOR_ZEPTOMAIL_TOKEN = os.environ.get("RIPRAPTOR_ZEPTOMAIL_TOKEN", "").strip()
# Zoho renamed ZeptoMail to Zoho CPaaS; this is the address its Mail Agent API page gives.
RIPRAPTOR_ZEPTOMAIL_URL = os.environ.get("RIPRAPTOR_ZEPTOMAIL_URL", "https://cpaas.zoho.com/v1.1/email").strip()
RIPRAPTOR_MAIL_FROM = os.environ.get("RIPRAPTOR_MAIL_FROM", "alerts@ripraptor.com").strip()
RIPRAPTOR_SITE_URL = os.environ.get("RIPRAPTOR_SITE_URL", "https://ripraptor.com").strip().rstrip("/")
RIPRAPTOR_INBOX_NOTIFY_EMAIL = os.environ.get("RIPRAPTOR_INBOX_NOTIFY_EMAIL", "").strip()
RIPRAPTOR_NTFY_TOPIC = os.environ.get("RIPRAPTOR_NTFY_TOPIC", "").strip()
RIPRAPTOR_NTFY_URL = os.environ.get("RIPRAPTOR_NTFY_URL", "https://ntfy.sh").strip()

RIPRAPTOR_EBAY_APP_ID = os.environ.get("RIPRAPTOR_EBAY_APP_ID", "").strip()
RIPRAPTOR_EBAY_CERT_ID = os.environ.get("RIPRAPTOR_EBAY_CERT_ID", "").strip()
RIPRAPTOR_EBAY_CAMPAIGN_ID = os.environ.get("RIPRAPTOR_EBAY_CAMPAIGN_ID", "").strip()
# Products checked on eBay per daily run. The Browse API allows 5,000 calls a day.
RIPRAPTOR_EBAY_DAILY_LIMIT = int(os.environ.get("RIPRAPTOR_EBAY_DAILY_LIMIT", "4000"))

# Price imports may record a retailer's product image for products that have
# no uploaded image. Turn off if you would rather upload every image yourself.
RIPRAPTOR_USE_FEED_IMAGES = env_bool("RIPRAPTOR_USE_FEED_IMAGES", default=True)

# Price imports may add sealed products they find in shops to the catalogue.
RIPRAPTOR_AUTO_CATALOGUE = env_bool("RIPRAPTOR_AUTO_CATALOGUE", default=True)
