from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_date

from users.models import StravovaciSkupina
from jidelnicek.models import Jidlo
from objednavky.models import Order, OrderItem
from django.contrib.auth import get_user_model


DEFAULT_ORDER_MAP = {
    "DS15+": [
        "Rajská s hovězím a houskovým knedlíkem",
        "Kuře na paprice s těstovinami",
        "Zeleninové rizoto",
    ],
    "DM15+": [
        "Rajská s hovězím a těstovinami",
        "Rajská s masovými kuličkami a těstovinami",
        "Kuře na paprice s těstovinami",
    ],
    "PS15+": [
        "Rajská s hovězím a houskovým knedlíkem",
        "Zeleninové rizoto",
    ],
}


DEMO_USER_PATTERNS = [
    "student.ds",
    "student.dm",
    "student.ps",
]


class Command(BaseCommand):
    help = "Naplní demo objednávky pro SOU testování."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Datum objednávek ve formátu YYYY-MM-DD. Výchozí je dnešek.",
        )
        parser.add_argument(
            "--wipe-day",
            action="store_true",
            help="Smaže existující objednávky demo uživatelů pro zadaný den a vytvoří je znovu.",
        )
        parser.add_argument(
            "--quantity",
            type=int,
            default=1,
            help="Počet porcí na jednu položku objednávky. Výchozí 1.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        target_date = self.resolve_date(options["date"])
        quantity = max(1, int(options["quantity"]))

        self.stdout.write(self.style.NOTICE(f"Seed demo objednávek pro datum {target_date} startuje..."))

        User = get_user_model()

        demo_users = self.get_demo_users(User)
        if not demo_users:
            self.stdout.write(self.style.ERROR("Nebyl nalezen žádný demo student. Nejprve spusť seed_sou_users."))
            return

        jidla = self.get_required_jidla()
        if not jidla:
            self.stdout.write(self.style.ERROR("Nebyla nalezena potřebná jídla. Nejprve spusť seed_demo_data."))
            return

        if options["wipe_day"]:
            deleted_orders, deleted_items = self.delete_demo_orders_for_day(target_date, demo_users)
            self.stdout.write(self.style.WARNING(
                f"Smazány staré demo objednávky pro {target_date}: objednávky={deleted_orders}, položky={deleted_items}"
            ))

        created_or_updated = self.seed_orders(User, demo_users, jidla, target_date, quantity)

        self.stdout.write(self.style.SUCCESS(f"Hotovo. Vytvořeno / aktualizováno objednávek: {created_or_updated}"))

    def resolve_date(self, value):
        if not value:
            return date.today()

        parsed = parse_date(value)
        if not parsed:
            raise ValueError("Neplatné datum. Použij formát YYYY-MM-DD.")
        return parsed

    def get_demo_users(self, User):
        qs = User.objects.all()

        # Hledáme demo studenty podle username
        user_qs = qs.filter(
            username__in=[
                "student.ds01", "student.ds02", "student.ds03",
                "student.dm01", "student.dm02", "student.dm03",
                "student.ps01", "student.ps02", "student.ps03",
            ]
        ).select_related("stravovaci_skupina")

        return list(user_qs)

    def get_required_jidla(self):
        names = set()
        for values in DEFAULT_ORDER_MAP.values():
            names.update(values)

        qs = Jidlo.objects.filter(nazev__in=list(names)).select_related("druh")
        return {j.nazev: j for j in qs}

    def delete_demo_orders_for_day(self, target_date, demo_users):
        user_ids = [u.id for u in demo_users]

        qs = Order.objects.filter(
            user_id__in=user_ids,
            datum_vydeje=target_date,
        )

        item_count = OrderItem.objects.filter(order__in=qs).count()
        order_count = qs.count()

        qs.delete()
        return order_count, item_count

    def choose_meal_for_user(self, user, jidla):
        skupina = getattr(user, "stravovaci_skupina", None)
        if not skupina:
            return None

        pool = DEFAULT_ORDER_MAP.get(skupina.kod, [])
        if not pool:
            return None

        # deterministický výběr podle username
        index = sum(ord(c) for c in user.username) % len(pool)
        meal_name = pool[index]

        return jidla.get(meal_name)

    def seed_orders(self, User, demo_users, jidla, target_date, quantity):
        count = 0

        for user in demo_users:
            jidlo = self.choose_meal_for_user(user, jidla)
            if not jidlo:
                self.stdout.write(self.style.WARNING(
                    f"Přeskakuji {user.username}: nelze vybrat jídlo pro skupinu."
                ))
                continue

            # Najdi nebo vytvoř objednávku na den
            order, created = Order.objects.get_or_create(
                user=user,
                datum_vydeje=target_date,
                defaults=self.build_order_defaults(user, target_date),
            )

            if not created:
                # lehká synchronizace existující objednávky
                changed = False
                defaults = self.build_order_defaults(user, target_date)
                for key, value in defaults.items():
                    if hasattr(order, key) and getattr(order, key) != value:
                        setattr(order, key, value)
                        changed = True
                if changed:
                    order.save()

            # smaž staré položky objednávky a založ přesně jednu demo položku
            order.items.all().delete()

            # vytvoření položky objednávky
            self.create_order_item(order, jidlo, quantity)

            count += 1
            suffix = "vytvořena" if created else "aktualizována"
            self.stdout.write(f" - {user.username}: {jidlo.nazev} ({suffix})")

        return count

    def build_order_defaults(self, user, target_date):
        """
        Vrací bezpečné defaulty podle polí, která na modelu Order skutečně existují.
        """
        defaults = {}
        field_names = {f.name for f in Order._meta.get_fields()}

        if "datum_vydeje" in field_names:
            defaults["datum_vydeje"] = target_date

        if "stav" in field_names:
            # pokud model Order používá stav
            try:
                defaults["stav"] = "OBJEDNANO"
            except Exception:
                pass

        if "celkova_cena" in field_names:
            defaults["celkova_cena"] = Decimal("0")

        return defaults

    def create_order_item(self, order, jidlo, quantity):
        """
        Bezpečné vytvoření OrderItem podle skutečných polí modelu.
        Předpoklad z projektu: OrderItem má order, menu_item, quantity.
        Pokud bude potřeba, snadno upravíme.
        """
        field_names = {f.name for f in OrderItem._meta.get_fields()}

        if "menu_item" not in field_names:
            raise RuntimeError("Model OrderItem nemá pole 'menu_item'. Seed je potřeba upravit na tvůj aktuální model.")

        # v projektu se objednává přes PolozkaJidelnicku
        from jidelnicek.models import PolozkaJidelnicku

        menu_item = (
            PolozkaJidelnicku.objects
            .filter(jidlo=jidlo)
            .select_related("jidlo")
            .first()
        )

        if not menu_item:
            raise RuntimeError(
                f"Pro jídlo '{jidlo.nazev}' neexistuje žádná PolozkaJidelnicku. "
                f"Nejprve spusť seed_demo_data --reset-menu."
            )

        kwargs = {
            "order": order,
            "menu_item": menu_item,
            "quantity": quantity,
        }

        if "unit_price" in field_names and hasattr(jidlo, "cena"):
            kwargs["unit_price"] = jidlo.cena

        if "line_total" in field_names and hasattr(jidlo, "cena"):
            kwargs["line_total"] = jidlo.cena * Decimal(quantity)

        OrderItem.objects.create(**kwargs)