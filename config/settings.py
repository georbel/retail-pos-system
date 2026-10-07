import os
import shutil
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "" if IS_VERCEL else "django-insecure-pos-development-key-change-before-deployment",
)
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY in the Vercel project environment variables.")

DEBUG = os.environ.get("DEBUG", "false" if IS_VERCEL else "true").lower() in {"1", "true", "yes"}
configured_hosts = os.environ.get("ALLOWED_HOSTS", "")
if configured_hosts:
    ALLOWED_HOSTS = [host.strip() for host in configured_hosts.split(",") if host.strip()]
    if IS_VERCEL and ".vercel.app" not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(".vercel.app")
elif IS_VERCEL:
    ALLOWED_HOSTS = [".vercel.app"]
else:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

vercel_hosts = (
    os.environ.get("VERCEL_URL", ""),
    os.environ.get("VERCEL_BRANCH_URL", ""),
    os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", ""),
)
CSRF_TRUSTED_ORIGINS = [
    f"https://{'*' if host.strip().startswith('.') else ''}{host.strip()}"
    for host in (*vercel_hosts, *ALLOWED_HOSTS)
    if host.strip() and host.strip() not in {"localhost", "127.0.0.1", "testserver"}
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


def get_database_config(environ=os.environ, base_dir=BASE_DIR):
    configured_path = environ.get("SQLITE_PATH")
    if configured_path:
        database_path = Path(configured_path)
    elif environ.get("VERCEL"):
        database_path = Path(tempfile.gettempdir()) / "pos.sqlite3"
    else:
        database_path = base_dir / "db.sqlite3"

    if environ.get("VERCEL"):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        packaged_database = base_dir / "db.sqlite3"
        if packaged_database.is_file() and not database_path.exists():
            temporary_copy = database_path.with_name(f"{database_path.name}.{os.getpid()}.tmp")
            shutil.copyfile(packaged_database, temporary_copy)
            os.replace(temporary_copy, database_path)
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path,
        "OPTIONS": {"timeout": 20},
    }


INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "pos.apps.PosConfig",
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

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": get_database_config()}
AUTH_USER_MODEL = "pos.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
