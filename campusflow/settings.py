"""
Django settings for CampusFlow LMS.

Environment variables (all optional):
  DJANGO_SECRET_KEY      secret key (a dev key is used when unset)
  DJANGO_DEBUG           "0" to disable debug
  DJANGO_ALLOWED_HOSTS   comma separated host list
  ANTHROPIC_API_KEY      enables Claude-assisted syllabus parsing and question generation
  LMS_AI_MODEL           Claude model id (default: claude-opus-5)
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me-for-production-campusflow"
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") != "0"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
# Render / Railway style hosting: the platform tells us our public hostname
for _host in (os.environ.get("RENDER_EXTERNAL_HOSTNAME"), os.environ.get("RAILWAY_PUBLIC_DOMAIN")):
    if _host:
        ALLOWED_HOSTS.append(_host)
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]
# Behind an HTTPS host (Render/Railway, or DJANGO_HTTPS=1): trust the proxy and use secure cookies.
if not DEBUG and (os.environ.get("RENDER_EXTERNAL_HOSTNAME") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
                  or os.environ.get("DJANGO_HTTPS") == "1"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "accounts",
    "courses",
    "delivery",
    "classroom",
    "exams",
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
]

ROOT_URLCONF = "campusflow.urls"

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
                "courses.context_processors.lms",
            ],
        },
    },
]

WSGI_APPLICATION = "campusflow.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("LMS_SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("LMS_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("LMS_MEDIA_ROOT", BASE_DIR / "media"))
# Serve uploaded files from Django itself (fine for a single test/demo instance; use object storage in production)
LMS_SERVE_MEDIA = DEBUG or os.environ.get("LMS_SERVE_MEDIA") == "1"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000

# --- LMS configuration -------------------------------------------------------
LMS_AI_MODEL = os.environ.get("LMS_AI_MODEL", "claude-opus-5")
# Minimum attendance percentage before a student is flagged as at-risk.
LMS_ATTENDANCE_THRESHOLD = 75
# CO attainment: a student "attains" a CO when they score >= the CO target
# (default 60%). The course attainment level follows the common NBA scale.
LMS_CO_ATTAINMENT_LEVELS = [(70, 3), (60, 2), (50, 1)]
# Weight of end-semester (ESE) evidence in the overall CO attainment.
LMS_CO_ESE_WEIGHT = 0.6
