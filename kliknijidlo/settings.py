import os
import sys
import secrets
from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv
LOCAL_RUNSERVER = "runserver" in sys.argv


load_dotenv(os.path.join(BASE_DIR, '.env'))


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default):
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


# --- SECURITY ---
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    SECRET_KEY = 'django-insecure-dev-key-temporary'


DEBUG = env_bool('DJANGO_DEBUG', default=True)


ALLOWED_HOSTS = env_list(
    'DJANGO_ALLOWED_HOSTS',
    [
        '127.0.0.1',
        'localhost',
        '10.0.0.108',
        'jidelna.kliknijidlo.cz',
        'www.jidelna.kliknijidlo.cz',
    ],
)

if LOCAL_RUNSERVER:
    for host in ['127.0.0.1', 'localhost', '0.0.0.0', '10.0.0.108', 'testserver']:
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)


# --- CLOUDFLARE & HTTPS FIX ---
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


CSRF_TRUSTED_ORIGINS = [
    'https://jidelna.kliknijidlo.cz',
    'http://jidelna.kliknijidlo.cz',
    'http://10.0.0.108:8000',
]


if not DEBUG and not LOCAL_RUNSERVER:
    if SECRET_KEY == 'django-insecure-dev-key-temporary':
        raise ValueError("DJANGO_SECRET_KEY must be set in production.")

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False

if TESTING:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False


# --- SESSION SETTINGS ---
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_AGE = 86400  # 24 hodin
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
KIOSK_AUTO_LOGIN_ENABLED = env_bool('KIOSK_AUTO_LOGIN_ENABLED', default=False)


# --- APPS ---
INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    'django_extensions',
    "users",
    "jidelnicek",
    "objednavky",
    'import_export',
    'dotace',
    'canteen_settings',
    'widget_tweaks',
    'vydej_jidel',
    'frontend',
    'vydej',
    'vydej_frontend',
    'reporty',
    "finance",
    "fakturace",
    'prepocty',
    'sklad',
    "pokladna",
    "ankety",
]


# --- MIDDLEWARE ---
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


# --- DATABASE: lokálně jen SQLite, bez ohledu na DEBUG/ENV ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


if (
    not DEBUG
    and DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3'
    and not DATABASES['default'].get('PASSWORD')
):
    raise ValueError("DB_PASSWORD must be set in production!")


# --- STATIC & MEDIA ---
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'


MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


# --- OSTATNÍ NASTAVENÍ ---
AUTH_USER_MODEL = 'users.CustomUser'
ROOT_URLCONF = 'kliknijidlo.urls'
WSGI_APPLICATION = 'kliknijidlo.wsgi.application'
LANGUAGE_CODE = 'cs-cz'
TIME_ZONE = 'Europe/Prague'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'users.context_processors.user_balance',
                'canteen_settings.context_processors.footer_info',
            ],
        },
    },
]


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 8,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'django.log'),
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'security_file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'security.log'),
            'maxBytes': 1024 * 1024 * 10,
            'backupCount': 5,
            'formatter': 'verbose',
            'filters': ['require_debug_false'],
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'mail_admins': {
            'level': 'ERROR',
            'class': 'django.utils.log.AdminEmailHandler',
            'filters': ['require_debug_false'],
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_file', 'mail_admins'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['file', 'mail_admins'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}


# Email configuration
if os.getenv('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', os.getenv('EMAIL_HOST_USER'))
    SERVER_EMAIL = os.getenv('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

    admin_email = os.getenv('ADMIN_EMAIL')
    if admin_email:
        ADMINS = [('Admin', admin_email)]
        MANAGERS = ADMINS
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755


ALLOWED_UPLOAD_EXTENSIONS = [
    '.jpg', '.jpeg', '.png', '.gif', '.pdf',
    '.doc', '.docx', '.xls', '.xlsx',
]


# Jazzmin Admin Configuration
JAZZMIN_SETTINGS = {
    "site_title": "KlikniJídlo Admin",
    "site_header": "KlikniJídlo",
    "site_brand": "",
    # logo v top baru – soubor dej do static/img/kliknijidlo-logo.png
    "site_logo": "images/kliknijidlo-logo.png",
    "show_ui_builder": DEBUG,
    # sidebar chceme, aby v appce zůstával kontext
    "show_sidebar": True,
    "navigation_expanded": False,
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {
        # např.: "jidelnicek.Jidlo": "vertical_tabs",
        # "sklad.Vydejka": "horizontal_tabs",
    },
    "brand_logo": None,


    "icons": {
        # === APP IKONY ===
        "auth": "fas fa-shield-alt",
        "users": "fas fa-user-friends",
        "jidelnicek": "fas fa-utensils",
        "objednavky": "fas fa-shopping-cart",
        "vydej": "fas fa-dolly",
        "vydej_jidel": "fas fa-concierge-bell",
        "dotace": "fas fa-file-contract",
        "canteen_settings": "fas fa-gear",
        "frontend": "fas fa-globe",
        "reporty": "fas fa-chart-mixed",
        "finance": "fas fa-coins",
        "fakturace": "fas fa-file-invoice-dollar",
        "prepocty": "fas fa-calculator",
        "sklad": "fas fa-warehouse",
        "pokladna": "fas fa-cash-register",
        "ankety": "fas fa-star-half-stroke",
        "import_export": "fas fa-file-import",
        "widget_tweaks": "fas fa-wand-magic-sparkles",
        "vydej_frontend": "fas fa-window-restore",

        # === USERS ===
        "users.StravovaciSkupina": "fas fa-users",
        "users.Vklad": "fas fa-cash-register",
        "users.CustomUser": "fas fa-id-card-alt",

        # === JIDELNICEK ===
        "jidelnicek.Alergen": "fas fa-triangle-exclamation",
        "jidelnicek.DruhJidla": "fas fa-utensils",
        "jidelnicek.Jidlo": "fas fa-bowl-food",
        "jidelnicek.Jidelnicek": "fas fa-calendar-days",
        "jidelnicek.PolozkaJidelnicku": "fas fa-list-ul",

        # === OBJEDNAVKY ===
        "objednavky.Order": "fas fa-shopping-basket",
        "objednavky.OrderItem": "fas fa-receipt",
        "objednavky.UserRFID": "fas fa-id-card",
        "objednavky.PriceRecalculationLog": "fas fa-clipboard-list",
        "objednavky.PriceRecalculationDetail": "fas fa-clipboard-check",

        # === VYDEJ ===
        "vydej.VydejOrder": "fas fa-shopping-cart",
        "vydej.PrehledProKuchyni": "fas fa-kitchen-set",
        "vydej.VydejSettings": "fas fa-sliders-h",
        "vydej.VydejniUctenka": "fas fa-receipt",
        "vydej.PolozkaUctenky": "fas fa-list-ol",
        "vydej.StornovaneObjednavky": "fas fa-trash-alt",

        # === VYDEJ_JIDEL ===
        "vydej_jidel.VydajiciCas": "fas fa-clock-rotate-left",
        "vydej_jidel.VydejSettings": "fas fa-stopwatch-20",

        # === DOTACE ===
        "dotace.DotacniPolitika": "fas fa-file-contract",
        "dotace.DotaceProJidelniskouSkupinu": "fas fa-hand-holding-heart",
        "dotace.SkupinoveNastaveni": "fas fa-users-gear",
        "dotace.Dotace": "fas fa-piggy-bank",

        # === CANTEEN_SETTINGS ===
        "canteen_settings.CanteenContact": "fas fa-building",
        "canteen_settings.OrderClosingTime": "fas fa-stopwatch",
        "canteen_settings.GroupOrderLimit": "fas fa-user-friends",
        "canteen_settings.MealPickupTime": "fas fa-clock",
        "canteen_settings.OperatingDays": "fas fa-circle-check",
        "canteen_settings.OperatingExceptions": "fas fa-triangle-exclamation",

        # === FRONTEND ===
        # žádné modely (zatím) – app ikona už je výše

        # === REPORTY / PREPOCTY ===
        "reporty.ReportDummy": "fas fa-chart-pie",
        "finance.FinancniDashboard": "fas fa-chart-line",
        "fakturace.FakturacniNastaveni": "fas fa-sliders",
        "fakturace.FakturacniDavka": "fas fa-file-invoice-dollar",
        "fakturace.FakturacniPolozka": "fas fa-list-check",
        "prepocty.PrepoctyDummy": "fas fa-calculator",

        # === SKLAD ===
        "sklad.Surovina": "fas fa-carrot",
        "sklad.StavSkladu": "fas fa-boxes-stacked",
        "sklad.PohybSkladu": "fas fa-right-left",
        "sklad.SkladDashboard": "fas fa-warehouse",
        "sklad.RecepturaPolozka": "fas fa-list-ul",
        "sklad.PrijemSkladu": "fas fa-truck-loading",
        "sklad.PolozkaPrijmu": "fas fa-plus-square",
        "sklad.Dodavatel": "fas fa-truck",
        "sklad.KomponentaJidla": "fas fa-layer-group",
        "sklad.SarzeSkladu": "fas fa-box-open",
        "sklad.OdpisExpirace": "fas fa-calendar-times",
        "sklad.SkladovaUzaverka": "fas fa-lock",
        "sklad.Inventura": "fas fa-clipboard-check",
        "sklad.PolozkaInventury": "fas fa-list-check",
        "sklad.InventurniDoklad": "fas fa-file-invoice",
        "sklad.Vydejka": "fas fa-boxes-packing",
        "sklad.PolozkaVydejky": "fas fa-list-ol",
        "sklad.ReportSpotrebniKos": "fas fa-chart-pie",
        "sklad.NormaSpotrebnihoKose": "fas fa-scale-balanced",
        "sklad.ToleranceSpotrebnihoKose": "fas fa-sliders",
        "sklad.ReportNakladySkladu": "fas fa-money-bill-trend-up",

        # === POKLADNA ===
        "pokladna.DPHSkupina": "fas fa-percent",
        "pokladna.PLUKategorie": "fas fa-layer-group",
        "pokladna.PLUPolozka": "fas fa-barcode",
        "pokladna.Pokladna": "fas fa-cash-register",
        "pokladna.PokladniDoklad": "fas fa-file-invoice-dollar",
        "pokladna.PokladniPolozka": "fas fa-list-ol",
        "pokladna.PokladniUzaverka": "fas fa-clipboard-check",
        "pokladna.PokladnaTile": "fas fa-square",
        "ankety.AnketniOtazka": "fas fa-circle-question",
        "ankety.HodnoceniJidla": "fas fa-star",
        "ankety.OdpovedHodnoceni": "fas fa-list-check",

        # === AUTH fallback ===
        "auth.User": "fas fa-user-circle",
        "auth.Group": "fas fa-users-cog",
    },

    "order_with_respect_to": [
        "users",
        "jidelnicek",
        "objednavky",
        "vydej",
        "vydej_jidel",
        "dotace",
        "canteen_settings",
        "frontend",
        "reporty",
        "finance",
        "fakturace",
        "prepocty",
        "auth",
        "sklad",
        "pokladna",
        "ankety",
    ],

    # >>> HORNÍ HORIZONTÁLNÍ MENU – tlačítka na dashboardy appek <<<
    "topmenu_links": [
        {
            "name": "Dashboard",
            "url": "admin:index",
            "permissions": ["auth.view_user"],
            "icon": "fas fa-home",
        },
        # každá appka míří na svůj „dashboard“ (changelist daného pseudo‑modelu)
        {
            "name": "Uživatelé",
            "url": "admin:users_customuser_changelist",
            "icon": "fas fa-user-friends",
        },
        {
            "name": "Jídelníček",
            "url": "admin:jidelnicek_jidelnicek_changelist",
            "icon": "fas fa-utensils",
        },
        {
            "name": "Objednávky",
            "url": "admin:objednavky_order_changelist",
            "icon": "fas fa-shopping-cart",
        },
        {
            "name": "Výdej",
            "url": "admin:vydej_prehledprokuchyni_changelist",
            "icon": "fas fa-dolly",
        },
        {
            "name": "Výdej jídelna",
            "url": "admin:vydej_jidel_vydejsettings_changelist",
            "icon": "fas fa-concierge-bell",
        },
        {
            "name": "Dotace",
            "url": "admin:dotace_dotacnipolitika_changelist",
            "icon": "fas fa-file-contract",
        },
        {
            "name": "Nastavení jídelny",
            "url": "admin:canteen_settings_canteencontact_changelist",
            "icon": "fas fa-gear",
        },
        {
            "name": "Frontend",
            "url": "home",
            "icon": "fas fa-globe",
        },
        {
            "name": "Reporty",
            "url": "admin:reporty_reportdummy_changelist",
            "icon": "fas fa-chart-line",
        },
        {
            "name": "Finance",
            "url": "admin:finance_financnidashboard_changelist",
            "icon": "fas fa-coins",
        },
        {
            "name": "Fakturace",
            "url": "admin:fakturace_fakturacnidavka_changelist",
            "icon": "fas fa-file-invoice-dollar",
        },
        {
            "name": "Přepočty",
            "url": "admin:prepocty_prepoctydummy_changelist",
            "icon": "fas fa-calculator",
        },
        {
            "name": "Sklad",
            "url": "admin:sklad_skladdashboard_changelist",
            "icon": "fas fa-warehouse",
        },
        # Pokladnu si doplníš podle modelu, který chceš jako vstupní
        # {"name": "Pokladna", "url": "admin:pokladna_xxx_changelist", "icon": "fas fa-cash-register"},
    ],

    "custom_css": "css/custom-admin.css",

    "custom_links": {
        "users": [
            {
                "name": "Nulování kont",
                "url": "admin:users_vklad_nulovani_konta",
                "icon": "fas fa-rotate-left",
            },
        ],
        "prepocty": [
            {
                "name": "Spustit přepočet cen",
                "url": "admin:objednavky_order_price_recalculation",
                "icon": "fas fa-play-circle",
            },
            {
                "name": "Historie přepočtů",
                "url": "admin:objednavky_pricerecalculationlog_changelist",
                "icon": "fas fa-history",
            },
            {
                "name": "Detaily přepočtů",
                "url": "admin:objednavky_pricerecalculationdetail_changelist",
                "icon": "fas fa-list",
            },
        ],
        "sklad": [
            {
                "name": "Měsíční spotřební koš",
                "url": "admin:sklad_mesicni_spotrebni_kos",
                "icon": "fas fa-chart-pie",
            },
            {
                "name": "Zdraví skladu",
                "url": "admin:sklad_zdravi_skladu",
                "icon": "fas fa-heartbeat",
            },
            {
                "name": "Doklady k opravě",
                "url": "admin:sklad_doklady_k_oprave",
                "icon": "fas fa-triangle-exclamation",
            },
            {
                "name": "Návrh nákupu",
                "url": "admin:sklad_navrh_nakupu",
                "icon": "fas fa-cart-plus",
            },
        ],
        "ankety": [
            {
                "name": "Vyhodnocení anket",
                "url": "admin:ankety_report",
                "icon": "fas fa-chart-simple",
            },
        ],
    },

    "hide_models": [
        "prepocty.PrepoctyDummy",
        "objednavky.PriceRecalculationLog",
        "objednavky.PriceRecalculationDetail",
    ],
}


# UI customizace
JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": True,
    "footer_small_text": True,
    "body_small_text": False,
    "brand_small_text": True,
    "brand_colour": "navbar-orange",
    "accent": "accent-success",
    "navbar": "navbar-success navbar-dark",
    "no_navbar_border": False,
    "navbar_fixed": True,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-success",
    "sidebar_nav_small_text": True,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": False,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": True,
    "theme": "spacelab",
    "dark_mode_theme": None,
    "button_classes": {
        "primary": "btn-outline-primary",
        "secondary": "btn-outline-secondary",
        "info": "btn-outline-info",
        "warning": "btn-outline-warning",
        "danger": "btn-outline-danger",
        "success": "btn-outline-success",
    },
    "actions_sticky_top": False,
}


# DEBUG: lokální SQLite místo Postgresu
if DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "timeout": 30,
            },
        }
    }


if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3" and "test" not in sys.argv:
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault("timeout", 30)

    from django.db.backends.signals import connection_created

    def _configure_local_sqlite(sender, connection, **kwargs):
        if connection.vendor != "sqlite":
            return
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=OFF")
            cursor.execute("PRAGMA synchronous=OFF")
            cursor.execute("PRAGMA temp_store=MEMORY")

    connection_created.connect(_configure_local_sqlite)


# =============================================================================
# SECURITY NOTES FOR PRODUCTION:
# =============================================================================
# ...
