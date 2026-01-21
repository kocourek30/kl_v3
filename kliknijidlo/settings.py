import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

print("DEBUG from env:", os.getenv('DJANGO_DEBUG'))
print("SECRET from env:", os.getenv('DJANGO_SECRET_KEY'))


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
load_dotenv(os.path.join(BASE_DIR, '.env'))

# --- SECURITY ---
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')

# Pokud není v env, použij dev key (jen pro vývoj)
if not SECRET_KEY:
    SECRET_KEY = 'django-insecure-dev-key-temporary'

# DEBUG musí být False pro produkci, True jen pro vývoj
DEBUG = os.getenv('DJANGO_DEBUG', 'False') == 'True'

ALLOWED_HOSTS = ['*']

# --- CLOUDFLARE & HTTPS FIX ---
# Důležité: Cloudflare posílá hlavičku X-Forwarded-Proto
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# CSRF Trusted Origins - Cloudflare vyžaduje přesné domény
CSRF_TRUSTED_ORIGINS = [
    'https://jidelna.kliknijidlo.cz',
    'http://jidelna.kliknijidlo.cz',
    'http://10.0.0.108:8000',
]

# Aby vás Django neodhlásilo při přechodu mezi Cloudflare a NASem:
if not DEBUG:
    SESSION_COOKIE_SECURE = True   # Cookie se posílá jen přes HTTPS (Cloudflare)
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True     # Django bude vědět, že je na HTTPS díky proxy hlavičce
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False

# --- SESSION SETTINGS ---
SESSION_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_SAVE_EVERY_REQUEST = True 
SESSION_COOKIE_AGE = 86400  # 24 hodin

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
    'prepocty',
    
]

# --- MIDDLEWARE ---
# Pořadí je naprosto klíčové pro správné fungování session a static souborů
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

# --- DATABASE ---
DATABASES = {
    'default': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.getenv('DB_NAME', 'kliknijidlo_dev'),
        'USER': os.getenv('DB_USER', 'kliknijidlo_user'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'dev_password_123'),
        'HOST': os.getenv('DB_HOST', 'db'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': os.getenv('DB_SSLMODE', 'prefer'),
        },
    }
}

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


WSGI_APPLICATION = 'kliknijidlo.wsgi.application'

# Database
# Database configuration from environment
DATABASES = {
    'default': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.getenv('DB_NAME', 'kliknijidlo_dev'),
        'USER': os.getenv('DB_USER', 'kliknijidlo_user'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'dev_password_123'),

        'HOST': os.getenv('DB_HOST', 'db'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': os.getenv('DB_SSLMODE', 'prefer'),
        },
    }
}

# Validate database password in production
if not DEBUG and not DATABASES['default']['PASSWORD']:
    raise ValueError("DB_PASSWORD must be set in production!")



# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators
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

# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/
LANGUAGE_CODE = 'cs-cz'
TIME_ZONE = 'Europe/Prague'
USE_I18N = True
USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'security_file': {
            'level': 'WARNING',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'security.log'),
            'maxBytes': 1024 * 1024 * 10,  # 10MB
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

# Email configuration (for error notifications and admin alerts)
if os.getenv('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', os.getenv('EMAIL_HOST_USER'))
    SERVER_EMAIL = os.getenv('SERVER_EMAIL', DEFAULT_FROM_EMAIL)
    
    # Admin email for error notifications
    admin_email = os.getenv('ADMIN_EMAIL')
    if admin_email:
        ADMINS = [('Admin', admin_email)]
        MANAGERS = ADMINS
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# File Upload Security
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
FILE_UPLOAD_PERMISSIONS = 0o644
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755

# Content Security (allowed file extensions for uploads if applicable)
ALLOWED_UPLOAD_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.doc', '.docx', '.xls', '.xlsx']

# Jazzmin Admin Configuration
JAZZMIN_SETTINGS = {
    "site_title": "KlikniJídlo Admin",
    "site_header": "KlikniJídlo",
    "site_brand": "Kliknijidlo.cz",
    "show_ui_builder": DEBUG,  # Only show in development
    
    "icons": {
        # Uživatelé & autentizace
        "auth.User": "fas fa-user-circle",
        "auth.Group": "fas fa-users-cog",
        "users.CustomUser": "fas fa-id-card-alt",
        "users.Vklad": "fas fa-cash-register",
        
        # Dotace
        
        "dotace.DotacniPolitika": "fas fa-file-contract",
        "dotace.SkupinoveNastaveni": "fas fa-users-gear",
        
        # Jídelníčky & jídla
        "jidelnicek.Alergen": "fas fa-triangle-exclamation",
        "jidelnicek.DruhJidla": "fas fa-utensils",
        "jidelnicek.Jidelnicek": "fas fa-calendar-days",
        "jidelnicek.Jidlo": "fas fa-bowl-food",
        
        # Objednávky
        "objednavky.Order": "fas fa-shopping-basket",
        "objednavky.OrderItem": "fas fa-receipt",
        
        # Nastavení jídelny
        "canteen_settings.CanteenContact": "fas fa-building",
        "canteen_settings.GroupOrderLimit": "fas fa-user-friends",
        "canteen_settings.MealPickupTime": "fas fa-clock",
        "canteen_settings.OrderClosingTime": "fas fa-stopwatch",
        "canteen_settings.OperatingExceptions": "fas fa-triangle-exclamation",
        "canteen_settings.OperatingDays": "fas fa-circle-check",
        
        # Výdej jídel
        "vydej_jidel.VydajiciCas": "fas fa-clock-rotate-left",
        "vydej.Vydej": "fas fa-box-open",
        "vydej.VydejOrder": "fas fa-shopping-cart",
        "vydej.VydejniUctenka": "fas fa-receipt",
        "vydej.StornovaneObjednavky": "fas fa-trash-alt",
        "vydej.PrehledProKuchyni": "fas fa-kitchen-set",
        
        # Frontend
        "frontend.Page": "fas fa-file-lines",
        "frontend.Setting": "fas fa-sliders",
        "reporty": "fas fa-chart-mixed",
        "auth": "fas fa-shield-alt",

        "reporty.ReportDummy": "fas fa-chart-pie",
        "objednavky.PriceRecalculationDetail": "fas fa-clipboard-check",
        "objednavky.PriceRecalculationLog": "fas fa-clipboard-list",

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
        "prepocty",
        "auth",
    ],
    
    "topmenu_links": [
        {"name": "🏠 Dashboard", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "📊 Reporty", "url": "admin:reporty_reportdummy_changelist", "permissions": ["auth.view_user"]},
    ],
    
    "custom_css": "css/custom-admin.css",

    'custom_links': {
        'prepocty': [  # ✅ Custom odkazy pod novou sekcí
            {
                'name': 'Spustit přepočet cen',
                'url': 'admin:objednavky_order_price_recalculation',
                'icon': 'fas fa-play-circle',
            },
            {
                'name': 'Historie přepočtů',
                'url': 'admin:objednavky_pricerecalculationlog_changelist',
                'icon': 'fas fa-history',
            },
            {
                'name': 'Detaily přepočtů',
                'url': 'admin:objednavky_pricerecalculationdetail_changelist',
                'icon': 'fas fa-list',
            },
        ]
    },
    
    'hide_models': [
        'prepocty.PrepoctyDummy',
        'objednavky.PriceRecalculationLog',
        'objednavky.PriceRecalculationDetail',
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
        "success": "btn-outline-success"
    },
    "actions_sticky_top": False
}

if os.getenv("DJANGO_DEBUG", "False") == "True":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# =============================================================================
# SECURITY NOTES FOR PRODUCTION:
# =============================================================================
# 1. Change admin URL from /admin/ to something less predictable in urls.py
#    Example: path('secure-admin-panel/', admin.site.urls)
# 
# 2. Consider adding django-axes for login attempt throttling:
#    pip install django-axes
#    https://django-axes.readthedocs.io/
#
# 3. Regular security updates:
#    pip list --outdated
#    pip install -U Django
#
# 4. Database backups:
#    Set up automated daily backups with retention policy
#
# 5. Monitor logs regularly:
#    tail -f logs/security.log
#    tail -f logs/django.log
# =============================================================================
