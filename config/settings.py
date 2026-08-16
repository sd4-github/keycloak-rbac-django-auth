"""Django settings for the Keycloak RBAC/ACM demo project.

Every Keycloak setting is read from the environment (see ``.env.example``)
via python-dotenv, so the same tree runs against a local Keycloak and a
deployed one.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-@g_-t+q)rgeizsz)c3v-93o3($=0s01&rhdxo^fkhi8ozgku6",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "mozilla_django_oidc",
    # local
    "erp",
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

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# -- Keycloak ------------------------------------------------------------

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "erp")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "erp-api")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
KEYCLOAK_ADMIN_REALM = os.environ.get("KEYCLOAK_ADMIN_REALM", "master")
KEYCLOAK_ADMIN_CLIENT_ID = os.environ.get("KEYCLOAK_ADMIN_CLIENT_ID", "admin-cli")
KEYCLOAK_ADMIN_USERNAME = os.environ.get("KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")
KEYCLOAK_VERIFY_TLS = os.environ.get("KEYCLOAK_VERIFY_TLS", "0") != "0"
# Gate logins to subjects carrying at least one role/group (safe default).
KEYCLOAK_REQUIRE_PRINCIPAL = os.environ.get("KEYCLOAK_REQUIRE_PRINCIPAL", "1") == "1"

# -- OIDC (mozilla_django_oidc) ------------------------------------------

OIDC_RP_CLIENT_ID = KEYCLOAK_CLIENT_ID
OIDC_RP_CLIENT_SECRET = KEYCLOAK_CLIENT_SECRET
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth"
OIDC_OP_TOKEN_ENDPOINT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
OIDC_OP_USER_ENDPOINT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/userinfo"
OIDC_OP_JWKS_ENDPOINT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
OIDC_OP_LOGOUT_ENDPOINT = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout"
OIDC_RP_SCOPES = "openid profile email"
OIDC_DRF_AUTH_BACKEND = "erp.backend.KeycloakOIDCBackend"
OIDC_STORE_ID_TOKEN = True
OIDC_CREATE_USER = True

# Mirror Keycloak principals (roles + groups) into Django auth.Groups.
OIDC_AUTHENTICATION_BACKEND = "erp.backend.KeycloakOIDCBackend"
OIDC_AUTHENTICATION_CALLBACK_URL = "erp:oidc_authentication_callback"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    OIDC_AUTHENTICATION_BACKEND,
]

LOGIN_URL = "erp:oidc_authentication_init"
LOGOUT_REDIRECT_URL = "/"


# -- ACM ----------------------------------------------------------------

# Maps a Keycloak client *resource* name to its Django model
# (``app_label.model``). Add new resources here when you grow the API.
ACM_RESOURCE_MODELS = {
    "invoice": "erp.invoice",
    "orgunit": "erp.orgunit",
}


# -- DRF ----------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "erp.authentication.KeycloakBearerAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
}


# -- i18n / static -------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split(",")