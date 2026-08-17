import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present (local development)
load_dotenv(BASE_DIR / '.env')

# ── Security ───────────────────────────────────────────────────────────────────

def _get_bool_env(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_list_env(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


DJANGO_ENV = os.environ.get('DJANGO_ENV', 'development').strip().lower()
IS_PRODUCTION = DJANGO_ENV == 'production'
DEBUG = _get_bool_env('DEBUG', default=not IS_PRODUCTION)
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured('DEBUG must be False when DJANGO_ENV is production.')

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-coconut-wireless'
    else:
        raise ImproperlyConfigured('SECRET_KEY environment variable is required when DEBUG is False.')
if IS_PRODUCTION and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith('django-insecure-')):
    raise ImproperlyConfigured(
        'SECRET_KEY must be at least 50 characters and not use the django-insecure prefix in production.'
    )

ALLOWED_HOSTS = _get_list_env(
    'ALLOWED_HOSTS',
    '127.0.0.1,localhost,testserver' if DEBUG else ''
)
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must be set when DEBUG is False.')
# Railway healthchecks use this hostname — always allow it.
if 'healthcheck.railway.app' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('healthcheck.railway.app')
# Include the Railway-assigned public domain if available.
_railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
if _railway_domain and _railway_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_railway_domain)

CSRF_TRUSTED_ORIGINS = _get_list_env('CSRF_TRUSTED_ORIGINS', '')
if IS_PRODUCTION and not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured('CSRF_TRUSTED_ORIGINS must be set when DJANGO_ENV is production.')

# Closed beta access controls
CLOSED_BETA_ENABLED = _get_bool_env('CLOSED_BETA_ENABLED', False)
BETA_GATE_CLIENT_SIGNUPS = _get_bool_env('BETA_GATE_CLIENT_SIGNUPS', CLOSED_BETA_ENABLED)
BETA_GATE_TRADIE_SIGNUPS = _get_bool_env('BETA_GATE_TRADIE_SIGNUPS', CLOSED_BETA_ENABLED)
BETA_ALLOWED_EMAILS = {email.lower() for email in _get_list_env('BETA_ALLOWED_EMAILS', '')}
BETA_ALLOWED_DOMAINS = {domain.lower().lstrip('@') for domain in _get_list_env('BETA_ALLOWED_DOMAINS', '')}

# Facebook login / multi-workspace / Messenger — phased rollout flags. All off
# by default. FACEBOOK_PAGE_LINKING_ENABLED / MESSENGER_CONNECTION_ENABLED /
# MESSENGER_NOTIFICATIONS_ENABLED are still inert — those integrations
# haven't been built (only Facebook Login itself has landed).
FACEBOOK_LOGIN_ENABLED = _get_bool_env('FACEBOOK_LOGIN_ENABLED', False)
FACEBOOK_PAGE_LINKING_ENABLED = _get_bool_env('FACEBOOK_PAGE_LINKING_ENABLED', False)
MESSENGER_CONNECTION_ENABLED = _get_bool_env('MESSENGER_CONNECTION_ENABLED', False)
MESSENGER_NOTIFICATIONS_ENABLED = _get_bool_env('MESSENGER_NOTIFICATIONS_ENABLED', False)
MULTI_WORKSPACE_ENABLED = _get_bool_env('MULTI_WORKSPACE_ENABLED', False)

# ── Meta (Facebook) app configuration ─────────────────────────────────────────
# Application-level only. Required in production once FACEBOOK_LOGIN_ENABLED
# is on (enforced below) — inert placeholders are fine while it's off.
META_APP_ID = os.environ.get('META_APP_ID', '').strip()
META_APP_SECRET = os.environ.get('META_APP_SECRET', '').strip()
META_GRAPH_API_VERSION = os.environ.get('META_GRAPH_API_VERSION', 'v21.0').strip()
META_OAUTH_REDIRECT_URI = os.environ.get('META_OAUTH_REDIRECT_URI', '').strip()

if IS_PRODUCTION and FACEBOOK_LOGIN_ENABLED:
    _missing_meta_vars = sorted({
        var_name for var_name in ('META_APP_ID', 'META_APP_SECRET', 'META_OAUTH_REDIRECT_URI')
        if not globals()[var_name]
    })
    if _missing_meta_vars:
        raise ImproperlyConfigured(
            f'FACEBOOK_LOGIN_ENABLED is on but missing required env var(s): {", ".join(_missing_meta_vars)}'
        )

# ── Application definition ─────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'marketplace',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'coconut_wireless.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
            ],
        },
    },
]

WSGI_APPLICATION = 'coconut_wireless.wsgi.application'
ASGI_APPLICATION = 'coconut_wireless.asgi.application'

# ── Database ───────────────────────────────────────────────────────────────────
# Default: SQLite for local development.
# For production set DATABASE_URL in .env or environment, e.g.:
#   DATABASE_URL=postgres://user:pass@host:5432/dbname
# and add dj-database-url + psycopg2 to requirements.txt
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}

AUTH_USER_MODEL = 'marketplace.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Localisation ───────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Pacific/Fiji'
USE_I18N = True
USE_TZ = True

# ── Static & media files ───────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'   # target for collectstatic in production
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'
    },
}
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Production media object storage (S3-compatible)
OBJECT_STORAGE_BACKEND = os.environ.get('OBJECT_STORAGE_BACKEND', 'filesystem').strip().lower()
if IS_PRODUCTION and OBJECT_STORAGE_BACKEND != 's3':
    raise ImproperlyConfigured('OBJECT_STORAGE_BACKEND must be set to "s3" in production.')

if OBJECT_STORAGE_BACKEND == 's3':
    # botocore defaults to adding CRC32 request/response checksums on S3 calls,
    # which non-AWS S3-compatible providers (Cloudflare R2 included) reject with
    # a 400 Bad Request on basic operations like HeadObject. Disable unless AWS
    # itself requires it. Uses setdefault so a real env var still wins.
    os.environ.setdefault('AWS_REQUEST_CHECKSUM_CALCULATION', 'when_required')
    os.environ.setdefault('AWS_RESPONSE_CHECKSUM_VALIDATION', 'when_required')

    AWS_STORAGE_BUCKET_NAME = os.environ.get('AWS_STORAGE_BUCKET_NAME', '').strip()
    AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', '').strip() or None
    AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL', '').strip() or None
    AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN', '').strip() or None
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', '').strip()
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '').strip()
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = _get_bool_env('AWS_QUERYSTRING_AUTH', True)
    AWS_QUERYSTRING_EXPIRE = int(os.environ.get('AWS_QUERYSTRING_EXPIRE', '3600'))
    AWS_S3_FILE_OVERWRITE = False
    # Non-AWS S3-compatible providers (R2, MinIO, Railway's own bucket, etc.) usually
    # need path-style addressing rather than boto3's AWS-default virtual-hosted style.
    AWS_S3_ADDRESSING_STYLE = os.environ.get('AWS_S3_ADDRESSING_STYLE', '').strip() or None

    required_s3_vars = ('AWS_STORAGE_BUCKET_NAME', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY')
    missing_s3_vars = [name for name in required_s3_vars if not os.environ.get(name, '').strip()]
    if missing_s3_vars:
        raise ImproperlyConfigured(
            f'Missing required S3 media storage environment variables: {", ".join(missing_s3_vars)}'
        )

    STORAGES['default'] = {'BACKEND': 'storages.backends.s3.S3Storage'}
    if AWS_S3_CUSTOM_DOMAIN:
        MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN.strip("/")}/'
    elif AWS_S3_ENDPOINT_URL:
        MEDIA_URL = f'{AWS_S3_ENDPOINT_URL.rstrip("/")}/{AWS_STORAGE_BUCKET_NAME}/'
    elif AWS_S3_REGION_NAME:
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com/'
    else:
        MEDIA_URL = f'https://{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com/'

# ── Proxy/HTTPS security defaults ──────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if _get_bool_env('USE_X_FORWARDED_PROTO', IS_PRODUCTION) else None
SESSION_COOKIE_SECURE = _get_bool_env('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _get_bool_env('CSRF_COOKIE_SECURE', not DEBUG)
# When enabling SECURE_SSL_REDIRECT behind Railway/Render, keep USE_X_FORWARDED_PROTO=True.
SECURE_SSL_REDIRECT = _get_bool_env('SECURE_SSL_REDIRECT', not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '3600' if not DEBUG else '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _get_bool_env('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = _get_bool_env('SECURE_HSTS_PRELOAD', False)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Auth redirects ─────────────────────────────────────────────────────────────
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# ── Email ──────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend' if IS_PRODUCTION else 'django.core.mail.backends.console.EmailBackend'
)
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@coconutwireless.fj')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '10'))

if IS_PRODUCTION and EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend':
    raise ImproperlyConfigured('EMAIL_BACKEND must be django.core.mail.backends.smtp.EmailBackend in production.')

if IS_PRODUCTION:
    required_email_vars = ('EMAIL_HOST', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD')
    missing_email_vars = [name for name in required_email_vars if not os.environ.get(name, '').strip()]
    if missing_email_vars:
        raise ImproperlyConfigured(
            f'Missing required SMTP environment variables in production: {", ".join(missing_email_vars)}'
        )

# Where operational notifications (support contact clicks, new tradie signups
# awaiting approval) get sent. Deliberately optional — never required, so
# forgetting to set it degrades to "no notification sent" rather than
# crashing the whole app on startup.
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '').strip()

# ── Observability (optional) ──────────────────────────────────────────────────
SENTRY_DSN = os.environ.get('SENTRY_DSN', '').strip()
if SENTRY_DSN:
    import sentry_sdk
    try:
        traces_sample_rate = float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0'))
    except ValueError as exc:
        raise ImproperlyConfigured('SENTRY_TRACES_SAMPLE_RATE must be a valid number.') from exc
    if not 0 <= traces_sample_rate <= 1:
        raise ImproperlyConfigured('SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.')

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        environment=DJANGO_ENV,
    )
