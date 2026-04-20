#!/usr/bin/env python
"""
KlikniJídlo v2 - bezpečnostní kontrola před nasazením

Spusťte tento skript před nasazením do produkce:
    python check_security.py
"""

import os
import sys
from pathlib import Path

# Přidání projektu do cesty.
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Nastavení Djanga.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'kliknijidlo.settings')
import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from io import StringIO


class Colors:
    """ANSI barvy pro výstup do terminálu."""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header(text):
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'=' * 70}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{text:^70}{Colors.END}")
    print(f"{Colors.BLUE}{Colors.BOLD}{'=' * 70}{Colors.END}\n")


def print_check(name, passed, message=""):
    status = f"{Colors.GREEN}✓ OK{Colors.END}" if passed else f"{Colors.RED}✗ CHYBA{Colors.END}"
    print(f"{status} {name}")
    if message:
        print(f"     {Colors.YELLOW}{message}{Colors.END}")


def print_warning(message):
    print(f"{Colors.YELLOW}! VAROVÁNÍ: {message}{Colors.END}")


def print_info(message):
    print(f"{Colors.BLUE}i INFORMACE: {message}{Colors.END}")


def check_debug_mode():
    """Kontrola, že DEBUG není zapnutý v produkci."""
    passed = not settings.DEBUG
    message = "" if passed else "V produkci musí být DEBUG vypnutý."
    print_check("DEBUG = False", passed, message)
    return passed


def check_secret_key():
    """Kontrola SECRET_KEY."""
    secret = settings.SECRET_KEY
    
    # Check if it's the default insecure key
    is_default = 'django-insecure' in secret
    is_short = len(secret) < 50
    
    passed = not is_default and not is_short
    
    messages = []
    if is_default:
        messages.append("Používá se výchozí Django SECRET_KEY.")
    if is_short:
        messages.append(f"SECRET_KEY je příliš krátký ({len(secret)} znaků, doporučeno alespoň 50).")
    
    message = " ".join(messages) if messages else ""
    print_check("SECRET_KEY je bezpečný", passed, message)
    
    if not passed:
        print_info("Vygenerujte nový klíč: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'")
    
    return passed


def check_allowed_hosts():
    """Kontrola nastavení ALLOWED_HOSTS."""
    hosts = settings.ALLOWED_HOSTS
    
    has_wildcard = '*' in hosts
    has_localhost_only = hosts == ['localhost', '127.0.0.1'] or hosts == ['127.0.0.1']
    has_production_domain = any('kliknijidlo.cz' in host for host in hosts)
    
    passed = not has_wildcard and has_production_domain and not has_localhost_only
    
    message = ""
    if has_wildcard:
        message = "ALLOWED_HOSTS obsahuje '*', což není bezpečné."
    elif has_localhost_only:
        message = "ALLOWED_HOSTS obsahuje jen localhost. Doplňte produkční doménu."
    elif not has_production_domain:
        message = "Doplňte jidelna.kliknijidlo.cz do ALLOWED_HOSTS."
    
    print_check("ALLOWED_HOSTS je nastavené", passed, message)
    if passed:
        print_info(f"Povolené domény: {', '.join(hosts)}")
    
    return passed


def check_database():
    """Kontrola nastavení databáze."""
    db_engine = settings.DATABASES['default']['ENGINE']
    
    is_sqlite = 'sqlite' in db_engine
    is_production_ready = 'postgresql' in db_engine or 'mysql' in db_engine
    
    passed = is_production_ready
    
    message = ""
    if is_sqlite:
        message = "SQLite není vhodné pro produkci. Použijte PostgreSQL nebo MySQL."
    
    print_check("Databáze je vhodná pro produkci", passed, message)
    print_info(f"Databázový engine: {db_engine}")
    
    return passed


def check_security_settings():
    """Kontrola bezpečnostních nastavení Djanga."""
    checks = [
        ('SECURE_SSL_REDIRECT', getattr(settings, 'SECURE_SSL_REDIRECT', False)),
        ('SESSION_COOKIE_SECURE', getattr(settings, 'SESSION_COOKIE_SECURE', False)),
        ('CSRF_COOKIE_SECURE', getattr(settings, 'CSRF_COOKIE_SECURE', False)),
        ('SECURE_HSTS_SECONDS', getattr(settings, 'SECURE_HSTS_SECONDS', 0) > 0),
        ('X_FRAME_OPTIONS', getattr(settings, 'X_FRAME_OPTIONS', None) == 'DENY'),
    ]
    
    all_passed = True
    for name, value in checks:
        passed = value if not settings.DEBUG else True  # Ve vývoji povoleno.
        if not passed:
            all_passed = False
        print_check(f"  {name}", passed, "" if passed else "V produkci má být zapnuto.")
    
    return all_passed


def check_csrf_trusted_origins():
    """Kontrola CSRF_TRUSTED_ORIGINS."""
    origins = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
    
    has_https = any(origin.startswith('https://') for origin in origins)
    has_production = any('kliknijidlo.cz' in origin for origin in origins)
    
    passed = has_https and has_production
    
    message = ""
    if not has_https:
        message = "CSRF_TRUSTED_ORIGINS má používat HTTPS."
    elif not has_production:
        message = "Doplňte https://jidelna.kliknijidlo.cz do CSRF_TRUSTED_ORIGINS."
    
    print_check("CSRF_TRUSTED_ORIGINS je nastavené", passed, message)
    if origins:
        print_info(f"Povolené zdroje: {', '.join(origins)}")
    
    return passed


def check_directories():
    """Kontrola existence potřebných adresářů."""
    dirs_to_check = [
        ('logs', 'Adresář pro logy'),
        ('media', 'Soubory nahrané uživateli'),
        ('staticfiles', 'Sesbírané statické soubory'),
    ]
    
    all_passed = True
    for dirname, description in dirs_to_check:
        dir_path = BASE_DIR / dirname
        exists = dir_path.exists()
        
        if not exists:
            all_passed = False
            message = f"{description} - vytvořte pomocí: mkdir -p {dirname}"
        else:
            message = ""
        
        print_check(f"  {dirname}/ existuje", exists, message)
    
    return all_passed


def check_static_files():
    """Kontrola, že jsou sesbírané statické soubory."""
    static_root = Path(settings.STATIC_ROOT)
    
    if not static_root.exists():
        passed = False
        message = "Spusťte: python manage.py collectstatic"
    else:
        # Kontrola, že adresář obsahuje soubory.
        has_files = any(static_root.iterdir())
        passed = has_files
        message = "" if passed else "Spusťte: python manage.py collectstatic"
    
    print_check("Statické soubory jsou sesbírané", passed, message)
    return passed


def check_env_file():
    """Kontrola existence souboru .env."""
    env_file = BASE_DIR / '.env'
    env_example = BASE_DIR / '.env.example'
    
    passed = env_file.exists()
    
    message = ""
    if not passed:
        if env_example.exists():
            message = "Vytvořte ze vzoru: cp .env.example .env"
        else:
            message = "Chybí soubor .env."
    
    print_check("Soubor .env existuje", passed, message)
    return passed


def run_django_check():
    """Spuštění vestavěné bezpečnostní kontroly Djanga."""
    print(f"\n{Colors.BOLD}Spouštím kontroly Djanga pro nasazení...{Colors.END}\n")
    
    try:
        # Zachycení výstupu.
        out = StringIO()
        call_command('check', '--deploy', stdout=out, stderr=out)
        output = out.getvalue()
        
        if 'System check identified no issues' in output:
            print(f"{Colors.GREEN}✓ Kontrola Djanga pro nasazení prošla.{Colors.END}")
            return True
        else:
            print(f"{Colors.YELLOW}{output}{Colors.END}")
            return False
    except Exception as e:
        print(f"{Colors.RED}✗ Kontrola Djanga selhala: {e}{Colors.END}")
        return False


def main():
    print_header("KlikniJídlo v2 - bezpečnostní kontrola produkce")
    
    results = []
    
    # Spuštění všech kontrol.
    results.append(('Prostředí', check_env_file()))
    results.append(('Režim DEBUG', check_debug_mode()))
    results.append(('SECRET_KEY', check_secret_key()))
    results.append(('ALLOWED_HOSTS', check_allowed_hosts()))
    results.append(('Databáze', check_database()))
    results.append(('CSRF zdroje', check_csrf_trusted_origins()))
    
    print(f"\n{Colors.BOLD}Bezpečnostní nastavení:{Colors.END}")
    results.append(('Bezpečnost', check_security_settings()))
    
    print(f"\n{Colors.BOLD}Souborový systém:{Colors.END}")
    results.append(('Adresáře', check_directories()))
    results.append(('Statické soubory', check_static_files()))
    
    # Kontrola Djanga pro nasazení.
    results.append(('Kontrola Djanga', run_django_check()))
    
    # Souhrn.
    print_header("Souhrn")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    if passed == total:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ VŠECHNY KONTROLY PROŠLY ({passed}/{total}){Colors.END}")
        print(f"\n{Colors.GREEN}Aplikace je připravená k produkčnímu nasazení.{Colors.END}")
        print(f"\nDalší kroky:")
        print(f"  1. Zkontrolujte DEPLOY.md s instrukcemi k nasazení.")
        print(f"  2. Nastavte SSL certifikát, například Let's Encrypt.")
        print(f"  3. Nakonfigurujte Nginx nebo Apache.")
        print(f"  4. Nastavte automatické zálohy.")
        return 0
    else:
        failed = total - passed
        print(f"{Colors.RED}{Colors.BOLD}✗ SELHALO {failed} KONTROL ({passed}/{total} prošlo){Colors.END}")
        print(f"\n{Colors.RED}Před nasazením do produkce opravte výše uvedené problémy.{Colors.END}")
        print(f"\nPodrobné instrukce najdete v DEPLOY.md.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
