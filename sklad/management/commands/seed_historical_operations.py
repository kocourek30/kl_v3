from datetime import date, timedelta
from decimal import Decimal
import random

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidlo, Jidelnicek, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from sklad.models import (
    PolozkaPrijmu,
    PrijemSkladu,
    Surovina,
    Vydejka,
)
from sklad.services import generate_vydejka_from_orders, uzavri_prijem, uzavri_vydejku


class Command(BaseCommand):
    help = "Vytvoří historické jídelníčky, objednávky, příjemky a skladové odpisy pro demo reporty."

    def add_arguments(self, parser):
        parser.add_argument(
            "--months",
            type=int,
            default=6,
            help="Kolik měsíců historie zpětně vytvořit.",
        )
        parser.add_argument(
            "--seed-base",
            action="store_true",
            help="Před historií spustí seed_demo_data a seed_sou_users, pokud chybí data.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        months = max(1, int(options["months"]))
        if options["seed_base"]:
            self.ensure_base_data()

        today = date.today()
        start = self.month_start(today - timedelta(days=months * 31))
        end = today - timedelta(days=1)

        users = list(
            get_user_model().objects.filter(
                is_staff=False,
                stravovaci_skupina__isnull=False,
            ).select_related("stravovaci_skupina").order_by("username")
        )
        if not users:
            self.stdout.write(self.style.WARNING("Nenalezeni demo strávníci se stravovací skupinou."))
            self.stdout.write("Spusť nejdřív: python manage.py seed_sou_users --reset-passwords")
            return

        druhy = {d.nazev: d for d in DruhJidla.objects.filter(nazev__in=["Polévka", "Hlavní jídlo", "Dezert"])}
        jidla = self.jidla_podle_druhu()
        if not all(jidla.values()):
            self.stdout.write(self.style.WARNING("Chybí katalog jídel. Spouštím seed_demo_data."))
            call_command("seed_demo_data")
            druhy = {d.nazev: d for d in DruhJidla.objects.filter(nazev__in=["Polévka", "Hlavní jídlo", "Dezert"])}
            jidla = self.jidla_podle_druhu()

        prijemky = self.seed_mesicni_prijemky(start, end)
        menu_count, order_count, item_count, vydejka_count = self.seed_provoz(start, end, users, druhy, jidla)

        self.stdout.write(self.style.SUCCESS("Historický demo provoz je hotový."))
        self.stdout.write(f"Období: {start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}")
        self.stdout.write(f"Jídelníčky: {menu_count}")
        self.stdout.write(f"Objednávky: {order_count}")
        self.stdout.write(f"Položky objednávek: {item_count}")
        self.stdout.write(f"Příjemky: {prijemky}")
        self.stdout.write(f"Uzavřené výdejky / odpisy: {vydejka_count}")

    def ensure_base_data(self):
        if Jidlo.objects.count() < 100:
            call_command("seed_demo_data")
        if not get_user_model().objects.filter(is_staff=False, stravovaci_skupina__isnull=False).exists():
            call_command("seed_sou_users", "--reset-passwords")

    def month_start(self, value):
        return date(value.year, value.month, 1)

    def iter_months(self, start, end):
        current = self.month_start(start)
        while current <= end:
            yield current
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

    def jidla_podle_druhu(self):
        data = {"Polévka": [], "Hlavní jídlo": [], "Dezert": []}
        for jidlo in Jidlo.objects.select_related("druh").prefetch_related("komponenty_jidla").order_by("nazev"):
            if not jidlo.druh:
                continue
            if jidlo.druh.nazev in data and jidlo.komponenty_jidla.exists():
                data[jidlo.druh.nazev].append(jidlo)
        return data

    def seed_mesicni_prijemky(self, start, end):
        suroviny = list(Surovina.objects.order_by("nazev"))
        if not suroviny:
            return 0

        created_or_existing = 0
        for month in self.iter_months(start, end):
            popis = f"Historická demo příjemka {month.strftime('%Y-%m')}"
            prijem, created = PrijemSkladu.objects.get_or_create(
                datum=month,
                popis=popis,
                defaults={
                    "datum_dodani": month,
                    "datum_vystaveni": month,
                    "castka_faktury_celkem": Decimal("0.00"),
                },
            )
            if not prijem.polozky.exists():
                for surovina in suroviny:
                    mnozstvi = self.mesicni_mnozstvi_prijmu(surovina)
                    if mnozstvi <= 0:
                        continue
                    PolozkaPrijmu.objects.create(
                        prijem=prijem,
                        surovina=surovina,
                        mnozstvi=mnozstvi,
                        jednotkova_cena=surovina.prumerna_cena_za_jednotku or Decimal("1.0000"),
                        sarze=f"HIST-{month.strftime('%Y%m')}-{surovina.id}",
                        datum_spotreby=month + timedelta(days=self.expirace_dnu(surovina)),
                    )
            if not prijem.uzavreny:
                uzavri_prijem(prijem)
            created_or_existing += 1
        return created_or_existing

    def mesicni_mnozstvi_prijmu(self, surovina):
        if surovina.jednotka == Surovina.JEDNOTKA_KS:
            return Decimal("900.000")
        if surovina.jednotka in (Surovina.JEDNOTKA_KG, Surovina.JEDNOTKA_L):
            return Decimal("250.000")
        if surovina.jednotka in (Surovina.JEDNOTKA_G, Surovina.JEDNOTKA_ML):
            return Decimal("250000.000")
        return Decimal("0")

    def expirace_dnu(self, surovina):
        if surovina.skupina_sk in (Surovina.SK_MLEKO, Surovina.SK_RYBY, Surovina.SK_MASO):
            return 21
        if surovina.skupina_sk == Surovina.SK_ZELENINA_OVOCE:
            return 35
        return 180

    def seed_provoz(self, start, end, users, druhy, jidla):
        rng = random.Random(20260414)
        menu_count = 0
        order_count = 0
        item_count = 0
        vydejka_count = 0

        current = start
        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            week_start = current - timedelta(days=current.weekday())
            week_end = week_start + timedelta(days=4)
            menu = self.get_or_create_week_menu(week_start, week_end, druhy, jidla, rng)
            menu_count += 1 if current == week_start else 0

            denni_polozky = self.menu_items_for_day(menu, current)
            if not denni_polozky:
                current += timedelta(days=1)
                continue

            for user in users:
                if not self.user_attends(user, current, rng):
                    continue
                order, _ = Order.objects.update_or_create(
                    user=user,
                    datum_vydeje=current,
                    defaults={
                        "status": "vydano",
                        "datum_vydani": timezone.make_aware(
                            timezone.datetime.combine(current, timezone.datetime.min.time())
                        ) + timedelta(hours=12),
                    },
                )
                order_count += 1
                for menu_item in self.items_for_user(user, denni_polozky, rng):
                    _, created = OrderItem.objects.update_or_create(
                        order=order,
                        menu_item=menu_item,
                        defaults={
                            "quantity": 1,
                            "cena": menu_item.jidlo.cena,
                            "vydano": True,
                            "datum_vydani": order.datum_vydani,
                        },
                    )
                    if created:
                        item_count += 1

            if OrderItem.objects.filter(order__datum_vydeje=current).exists():
                vydejka = Vydejka.objects.filter(
                    datum=current,
                    stravovaci_skupina__isnull=True,
                    typ_stravy="",
                    stornovano=False,
                ).first()
                if vydejka is None:
                    vydejka, _ = generate_vydejka_from_orders(current, None, "")
                if not vydejka.uzavreny:
                    uzavri_vydejku(vydejka)
                    vydejka_count += 1

            current += timedelta(days=1)

        return menu_count, order_count, item_count, vydejka_count

    def get_or_create_week_menu(self, week_start, week_end, druhy, jidla, rng):
        menu, _ = Jidelnicek.objects.get_or_create(
            platnost_od=week_start,
            platnost_do=week_end,
            defaults={"ikona": "bi-calendar-week"},
        )

        existing_count = menu.polozky.count()
        if existing_count >= 15:
            return menu

        missing = 15 - existing_count
        for slot in range(missing):
            day_index = (existing_count + slot) // 3
            druh_nazev = ("Polévka", "Hlavní jídlo", "Dezert")[(existing_count + slot) % 3]
            pool = jidla[druh_nazev]
            jidlo = pool[(day_index * 37 + rng.randrange(len(pool))) % len(pool)]
            PolozkaJidelnicku.objects.get_or_create(
                jidelnicek=menu,
                druh_jidla=druhy[druh_nazev],
                jidlo=jidlo,
            )
        return menu

    def menu_items_for_day(self, menu, day):
        day_index = day.weekday()
        polozky = list(menu.polozky.select_related("druh_jidla", "jidlo").order_by("id"))
        start = day_index * 3
        return polozky[start:start + 3]

    def user_attends(self, user, day, rng):
        kod = user.stravovaci_skupina.kod
        if kod == "DM15+":
            return rng.random() < 0.95
        if kod == "PS15+":
            return rng.random() < 0.88
        return rng.random() < 0.82

    def items_for_user(self, user, denni_polozky, rng):
        kod = user.stravovaci_skupina.kod
        by_name = {item.druh_jidla.nazev: item for item in denni_polozky}
        selected = []

        if kod == "DM15+":
            selected.extend([by_name.get("Polévka"), by_name.get("Hlavní jídlo")])
            if rng.random() < 0.55:
                selected.append(by_name.get("Dezert"))
        elif kod == "PS15+":
            if rng.random() < 0.60:
                selected.append(by_name.get("Polévka"))
            selected.append(by_name.get("Hlavní jídlo"))
            if rng.random() < 0.25:
                selected.append(by_name.get("Dezert"))
        else:
            if rng.random() < 0.45:
                selected.append(by_name.get("Polévka"))
            selected.append(by_name.get("Hlavní jídlo"))
            if rng.random() < 0.18:
                selected.append(by_name.get("Dezert"))

        return [item for item in selected if item is not None]
