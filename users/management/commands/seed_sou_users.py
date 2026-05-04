from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from users.models import StravovaciSkupina

try:
    from users.models import Vklad
    HAS_VKLAD = True
except Exception:
    HAS_VKLAD = False
    Vklad = None


DEFAULT_PASSWORD = "Test123456!"


STRAVOVACI_SKUPINY = [
    {"kod": "DS15+", "nazev": "Denní studium 15+", "typ_vzdelavani": "SS"},
    {"kod": "DM15+", "nazev": "Domov mládeže 15+", "typ_vzdelavani": "SS"},
    {"kod": "PS15+", "nazev": "Povinná strava 15+", "typ_vzdelavani": "SS"},
]


ROLE_NAMES = [
    "Administrátor",
    "Vedoucí jídelny",
    "Kuchyň",
    "Výdej",
    "Pokladna",
    "Student",
    "Vychovatel DM",
    "Třídní učitel",
]


DEMO_USERS = [
    {
        "username": "admin",
        "email": "admin@sou-demo.local",
        "first_name": "Demo",
        "last_name": "Admin",
        "group": "Administrátor",
        "is_staff": True,
        "is_superuser": True,
        "stravovaci_skupina": None,
    },
    {
        "username": "vedouci.jidelny",
        "email": "vedouci.jidelny@sou-demo.local",
        "first_name": "Jana",
        "last_name": "Novotná",
        "group": "Vedoucí jídelny",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },
    {
        "username": "kuchyn",
        "email": "kuchyn@sou-demo.local",
        "first_name": "Marie",
        "last_name": "Kuchařová",
        "group": "Kuchyň",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },
    {
        "username": "vydej",
        "email": "vydej@sou-demo.local",
        "first_name": "Petr",
        "last_name": "Vydávající",
        "group": "Výdej",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },
    {
        "username": "pokladna",
        "email": "pokladna@sou-demo.local",
        "first_name": "Lucie",
        "last_name": "Pokladní",
        "group": "Pokladna",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },
    {
        "username": "vychovatel.dm",
        "email": "vychovatel.dm@sou-demo.local",
        "first_name": "Roman",
        "last_name": "Vychovatel",
        "group": "Vychovatel DM",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },
    {
        "username": "tridni.ucitel",
        "email": "tridni.ucitel@sou-demo.local",
        "first_name": "Alena",
        "last_name": "Učitelová",
        "group": "Třídní učitel",
        "is_staff": True,
        "is_superuser": False,
        "stravovaci_skupina": None,
    },

    # Denní studium
    {
        "username": "student.ds01",
        "email": "student.ds01@sou-demo.local",
        "first_name": "Tomáš",
        "last_name": "Dvořák",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DS15+",
    },
    {
        "username": "student.ds02",
        "email": "student.ds02@sou-demo.local",
        "first_name": "Veronika",
        "last_name": "Králová",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DS15+",
    },
    {
        "username": "student.ds03",
        "email": "student.ds03@sou-demo.local",
        "first_name": "David",
        "last_name": "Malý",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DS15+",
    },

    # Domov mládeže
    {
        "username": "student.dm01",
        "email": "student.dm01@sou-demo.local",
        "first_name": "Pavel",
        "last_name": "Beneš",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DM15+",
    },
    {
        "username": "student.dm02",
        "email": "student.dm02@sou-demo.local",
        "first_name": "Nikola",
        "last_name": "Šimková",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DM15+",
    },
    {
        "username": "student.dm03",
        "email": "student.dm03@sou-demo.local",
        "first_name": "Jakub",
        "last_name": "Urban",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "DM15+",
    },

    # Povinná strava
    {
        "username": "student.ps01",
        "email": "student.ps01@sou-demo.local",
        "first_name": "Eliška",
        "last_name": "Pospíšilová",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "PS15+",
    },
    {
        "username": "student.ps02",
        "email": "student.ps02@sou-demo.local",
        "first_name": "Ondřej",
        "last_name": "Němec",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "PS15+",
    },
    {
        "username": "student.ps03",
        "email": "student.ps03@sou-demo.local",
        "first_name": "Klára",
        "last_name": "Horáková",
        "group": "Student",
        "is_staff": False,
        "is_superuser": False,
        "stravovaci_skupina": "PS15+",
    },
]


class Command(BaseCommand):
    help = "Naplní demo uživatele, role a stravovací skupiny pro SOU."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords",
            action="store_true",
            help="Přepíše hesla demo uživatelů na výchozí heslo.",
        )
        parser.add_argument(
            "--wipe-demo-users",
            action="store_true",
            help="Smaže demo uživatele z této commandy a vytvoří je znovu.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seed SOU uživatelů startuje..."))

        User = get_user_model()

        skupiny = self.seed_stravovaci_skupiny()
        role = self.seed_roles()

        if options["wipe_demo_users"]:
            self.delete_demo_users(User)

        users = self.seed_users(User, skupiny, role, reset_passwords=options["reset_passwords"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Hotovo."))
        self.stdout.write(f"Vytvořeno / aktualizováno uživatelů: {len(users)}")
        self.stdout.write(f"Výchozí heslo demo účtů: {DEFAULT_PASSWORD}")

    def seed_stravovaci_skupiny(self):
        result = {}
        for item in STRAVOVACI_SKUPINY:
            obj, _ = StravovaciSkupina.objects.update_or_create(
                kod=item["kod"],
                defaults={
                    "nazev": item["nazev"],
                    "typ_vzdelavani": item["typ_vzdelavani"],
                },
            )
            result[obj.kod] = obj
        return result

    def seed_roles(self):
        result = {}
        for name in ROLE_NAMES:
            obj, _ = Group.objects.get_or_create(name=name)
            result[name] = obj
        return result

    def delete_demo_users(self, User):
        usernames = [u["username"] for u in DEMO_USERS]
        qs = User.objects.filter(username__in=usernames)
        count = qs.count()
        qs.delete()
        self.stdout.write(self.style.WARNING(f"Smazáno demo uživatelů: {count}"))

    def seed_users(self, User, skupiny, role, reset_passwords=False):
        result = []

        for item in DEMO_USERS:
            username = item["username"]
            email = item["email"]

            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": email},
            )

            changed_fields = []

            # standardní pole
            for field_name in ("email", "first_name", "last_name"):
                if hasattr(user, field_name):
                    value = item[field_name]
                    if getattr(user, field_name) != value:
                        setattr(user, field_name, value)
                        changed_fields.append(field_name)

            # staff/superuser
            for field_name in ("is_staff", "is_superuser"):
                if hasattr(user, field_name):
                    value = item[field_name]
                    if getattr(user, field_name) != value:
                        setattr(user, field_name, value)
                        changed_fields.append(field_name)

            # aktivní účet
            if hasattr(user, "is_active") and not user.is_active:
                user.is_active = True
                changed_fields.append("is_active")

            # stravovací skupina
            skupina_kod = item["stravovaci_skupina"]
            if skupina_kod and hasattr(user, "stravovaci_skupina"):
                skupina = skupiny[skupina_kod]
                if user.stravovaci_skupina_id != skupina.id:
                    user.stravovaci_skupina = skupina
                    changed_fields.append("stravovaci_skupina")

            if created or reset_passwords:
                user.set_password(DEFAULT_PASSWORD)
                changed_fields.append("password")

            if changed_fields:
                # password se neukládá přes update_fields spolehlivě se vším kolem hashů,
                # takže radši full save
                user.save()

            # skupiny oprávnění
            if item["group"] in role:
                user.groups.set([role[item["group"]]])

            # nepovinné demo vklady
            if HAS_VKLAD:
                self.ensure_demo_balance(user)

            result.append(user)

            suffix = "vytvořen" if created else "aktualizován"
            self.stdout.write(f" - {username} ({suffix})")

        return result

    def ensure_demo_balance(self, user):
        """
        Pokud model Vklad existuje a má očekávané minimum polí,
        založí ukázkový zůstatek pro studenty.
        """
        if not hasattr(user, "stravovaci_skupina") or not user.stravovaci_skupina_id:
            return

        field_names = {f.name for f in Vklad._meta.get_fields()}

        # Bezpečný, minimální pokus – jen pokud model odpovídá očekávání.
        if "user" not in field_names:
            return

        defaults = {}
        if "castka" in field_names:
            defaults["castka"] = Decimal("1500.00")
        if "poznamka" in field_names:
            defaults["poznamka"] = "Demo seed vklad"

        try:
            Vklad.objects.get_or_create(user=user, defaults=defaults)
        except Exception:
            # Seed nemá spadnout kvůli detailům modelu Vklad
            pass
