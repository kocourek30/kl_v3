from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from admin_dashboard.models import AdminViewAccess
from admin_dashboard.services import set_group_level_for_access, sync_admin_view_accesses


ROLE_GROUP_NAME = "Role • Obsluha jídelny"

VIEW_ACCESS_SLUGS = {
    "users-admin",
    "canteen-operations-admin",
}

WRITE_ACCESS_SLUGS = {
    "menu-admin",
}

CONTROL_ACCESS_SLUGS = {
    "surveys-admin",
}

VIEW_MODELS = {
    "users": {
        "customuser",
        "stravovaciskupina",
        "vklad",
    },
    "jidelnicek": {
        "jidelnicek",
        "jidlo",
    },
    "provoz_jidelny": {
        "provoznidashboard",
    },
}

FULL_ACCESS_MODELS = {
    "ankety": {
        "anketniotazka",
        "hodnocenijidla",
        "odpovedhodnoceni",
        "mesicnianketa",
        "mesicnianketavarianta",
        "mesicnianketahlas",
    },
}

WRITE_ACCESS_MODELS = {
    "jidelnicek": {
        "jidelnicek",
        "jidlo",
    },
}


class Command(BaseCommand):
    help = "Nastaví view-only roli Obsluha jídelny a volitelně vytvoří uživatele."

    def add_arguments(self, parser):
        parser.add_argument("--create-user", action="store_true", help="Vytvoří i uživatele obsluhy.")
        parser.add_argument("--username", default="obsluha.jidelny", help="Username nového uživatele.")
        parser.add_argument("--password", default="Obsluha123!", help="Dočasné heslo nového uživatele.")
        parser.add_argument("--first-name", default="Obsluha", help="Jméno uživatele.")
        parser.add_argument("--last-name", default="Jídelny", help="Příjmení uživatele.")
        parser.add_argument("--email", default="", help="Email uživatele.")

    @transaction.atomic
    def handle(self, *args, **options):
        sync_admin_view_accesses(sync_role_defaults=True, force_role_defaults=False)
        group, _ = Group.objects.get_or_create(name=ROLE_GROUP_NAME)

        self._sync_admin_areas(group)
        self._sync_model_permissions(group)

        self.stdout.write(self.style.SUCCESS(f"Role '{ROLE_GROUP_NAME}' je připravena."))

        if options["create_user"]:
            self._create_or_update_user(group, options)

    def _sync_admin_areas(self, group):
        for access in AdminViewAccess.objects.all():
            if access.slug in CONTROL_ACCESS_SLUGS:
                level = "control"
            elif access.slug in WRITE_ACCESS_SLUGS:
                level = "write"
            elif access.slug in VIEW_ACCESS_SLUGS:
                level = "view"
            else:
                level = ""
            set_group_level_for_access(group, access, level)

    def _sync_model_permissions(self, group):
        wanted = Permission.objects.none()
        for app_label, models in VIEW_MODELS.items():
            wanted = wanted | Permission.objects.filter(
                content_type__app_label=app_label,
                content_type__model__in=models,
                codename__startswith="view_",
            )
        for app_label, models in FULL_ACCESS_MODELS.items():
            for prefix in ("view_", "add_", "change_", "delete_"):
                wanted = wanted | Permission.objects.filter(
                    content_type__app_label=app_label,
                    content_type__model__in=models,
                    codename__startswith=prefix,
                )
        for app_label, models in WRITE_ACCESS_MODELS.items():
            for prefix in ("view_", "add_", "change_"):
                wanted = wanted | Permission.objects.filter(
                    content_type__app_label=app_label,
                    content_type__model__in=models,
                    codename__startswith=prefix,
                )
        group.permissions.set(list(wanted))
        self.stdout.write(self.style.WARNING(f"Přiřazeno oprávnění celkem: {wanted.count()}"))

    def _create_or_update_user(self, group, options):
        User = get_user_model()
        username = options["username"].strip()
        password = options["password"]
        defaults = {
            "first_name": options["first_name"],
            "last_name": options["last_name"],
            "email": options["email"],
            "is_active": True,
            "is_staff": True,
            "is_superuser": False,
        }
        user, created = User.objects.get_or_create(username=username, defaults=defaults)
        if not created:
            for key, value in defaults.items():
                setattr(user, key, value)
        user.set_password(password)
        user.save()
        user.groups.add(group)
        if hasattr(user, "must_change_password"):
            user.must_change_password = True
            user.save(update_fields=["must_change_password"])

        action = "Vytvořen" if created else "Aktualizován"
        self.stdout.write(self.style.SUCCESS(f"{action} uživatel: {username}"))
        self.stdout.write(self.style.WARNING("Uživatel má nastavenou povinnou změnu hesla při prvním přihlášení."))
