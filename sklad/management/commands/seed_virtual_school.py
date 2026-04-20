import calendar
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ankety.models import AnketniOtazka, HodnoceniJidla, OdpovedHodnoceni
from dotace.models import Dotace, DotacniPolitika, SkupinoveNastaveni
from fakturace.models import FakturacniDavka, FakturacniPolozka
from jidelnicek.models import Alergen, DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from pokladna.models import DPHSkupina, PLUKategorie, PLUPolozka, Pokladna, PokladnaTile
from pokladna.services import (
    potvrdit_qr_platbu,
    pridej_polozku,
    stornuj_doklad,
    uzavri_denni_uzaverku,
    uzavri_doklad,
    vytvor_doklad,
    zahaj_qr_platbu,
)
from users.models import StravovaciSkupina, Vklad


DEFAULT_PASSWORD = "Test123456!"
RNG_SEED = 20260419


FIRST_NAMES = [
    "Adam", "Barbora", "Cyril", "Daniela", "Ema", "Filip", "Gabriela", "Hynek",
    "Ivana", "Jakub", "Karolína", "Lukáš", "Magdaléna", "Matěj", "Natálie",
    "Ondřej", "Petra", "Radek", "Sára", "Tereza", "Viktor", "Zuzana",
]

LAST_NAMES = [
    "Novák", "Svoboda", "Dvořák", "Černý", "Procházka", "Kučera", "Veselý",
    "Horák", "Němec", "Pokorný", "Marek", "Král", "Růžička", "Beneš",
    "Fiala", "Sedláček", "Urban", "Kříž", "Šimek", "Kovář",
]

ALLERGENS = [
    ("1 Obiloviny obsahující lepek", "fa-solid fa-wheat-awn"),
    ("3 Vejce", "fa-solid fa-egg"),
    ("4 Ryby", "fa-solid fa-fish"),
    ("6 Sójové boby", "fa-solid fa-seedling"),
    ("7 Mléko", "fa-solid fa-cow"),
    ("8 Skořápkové plody", "fa-solid fa-seedling"),
    ("9 Celer", "fa-solid fa-carrot"),
    ("10 Hořčice", "fa-solid fa-jar"),
]

ANKETA_OTAZKY = [
    "Jak vám jídlo chutnalo?",
    "Byla porce dostatečná?",
    "Bylo jídlo správně teplé a čerstvé?",
    "Objednal/a byste si toto jídlo znovu?",
]

PLU_DATA = [
    ("Jogurt bílý", "Doplňkový prodej", "15.00", "#54ae43", "fa-solid fa-bowl-food"),
    ("Jablko", "Doplňkový prodej", "9.00", "#54ae43", "fa-solid fa-apple-whole"),
    ("Bageta sýrová", "Doplňkový prodej", "39.00", "#f28f28", "fa-solid fa-bread-slice"),
    ("Minerální voda", "Nápoje", "18.00", "#0d6efd", "fa-solid fa-bottle-water"),
    ("Čaj do kelímku", "Nápoje", "12.00", "#198754", "fa-solid fa-mug-hot"),
    ("Káva školní", "Nápoje", "25.00", "#212529", "fa-solid fa-mug-saucer"),
    ("Doplňkový prodej 12 %", "Doplňkový prodej", "32.00", "#008b8b", "fa-solid fa-cart-shopping"),
    ("Oběd pro hosta", "Jídelna", "96.00", "#f28f28", "fa-solid fa-utensils"),
]


class Command(BaseCommand):
    help = "Naplní databázi kompletní virtuální školou pro prezentaci aplikace."

    def add_arguments(self, parser):
        parser.add_argument(
            "--students-per-group",
            type=int,
            default=30,
            help="Počet virtuálních strávníků v každé skupině 15+.",
        )
        parser.add_argument(
            "--history-months",
            type=int,
            default=4,
            help="Kolik měsíců provozní historie vytvořit.",
        )
        parser.add_argument(
            "--basket-month",
            default="",
            help="Měsíc pro přesně vycházející spotřební koš ve formátu RRRR-MM. Výchozí je příští měsíc.",
        )
        parser.add_argument(
            "--cash-days",
            type=int,
            default=25,
            help="Počet posledních pracovních dnů pro pokladní prodeje.",
        )
        parser.add_argument(
            "--future-months",
            type=int,
            default=3,
            help="Kolik měsíců budoucích jídelníčků a objednávek vytvořit.",
        )

    def handle(self, *args, **options):
        rng = random.Random(RNG_SEED)
        students_per_group = max(3, int(options["students_per_group"]))
        history_months = max(1, int(options["history_months"]))
        cash_days = max(1, int(options["cash_days"]))
        future_months = max(1, int(options["future_months"]))
        basket_month = options["basket_month"] or self._next_month_value(timezone.localdate())

        self.stdout.write(self.style.NOTICE("Připravuji základní demo data..."))
        call_command("seed_sou_users", verbosity=1)
        call_command("seed_demo_data", verbosity=0)
        call_command("seed_spotrebni_kos_2025", verbosity=0)

        with transaction.atomic():
            allergens = self._seed_allergens()
            users_count = self._seed_virtual_students(students_per_group, allergens, rng)
            deposits_count = self._seed_account_deposits(rng)
            account_policy_count = self._seed_account_policy_after_deposits()

        self.stdout.write(self.style.NOTICE("Vytvářím historické jídelníčky, objednávky a skladové odpisy..."))
        call_command("seed_historical_operations", "--months", str(history_months), verbosity=1)

        self.stdout.write(self.style.NOTICE("Vytvářím přesný prezentační měsíc spotřebního koše..."))
        call_command(
            "seed_presentation_spotrebni_kos",
            "--month",
            basket_month,
            "--days",
            "20",
            "--users-per-group",
            str(students_per_group),
            verbosity=1,
        )

        self.stdout.write(self.style.NOTICE("Vytvářím budoucí jídelníčky a objednávky..."))
        future_orders_count, future_items_count = self._seed_future_operation(
            basket_month,
            future_months,
            rng,
        )

        self.stdout.write(self.style.NOTICE("Doplňuji dotace a fakturační dávky za minulá období..."))
        subsidies_count, invoice_batches_count = self._seed_subsidies_and_invoices(history_months)

        with transaction.atomic():
            pokladna = self._seed_cash_register()
            plu_items = self._seed_plu_items(pokladna)
            sales_count, stornos_count, closures_count = self._seed_cash_sales(
                pokladna,
                plu_items,
                cash_days,
                rng,
            )
            ratings_count = self._seed_surveys(rng)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Virtuální škola pro prezentaci je naplněná."))
        self.stdout.write(f"Virtuální strávníci: {users_count}")
        self.stdout.write(f"Vklady na konta: {deposits_count}")
        self.stdout.write(f"Nastavení debetních kont: {account_policy_count}")
        self.stdout.write(f"Pokladní prodeje: {sales_count}")
        self.stdout.write(f"Pokladní storna: {stornos_count}")
        self.stdout.write(f"Denní uzávěrky pokladny: {closures_count}")
        self.stdout.write(f"Hodnocení jídel v anketě: {ratings_count}")
        self.stdout.write(f"Budoucí objednávky: {future_orders_count}")
        self.stdout.write(f"Budoucí položky objednávek: {future_items_count}")
        self.stdout.write(f"Dotace: {subsidies_count}")
        self.stdout.write(f"Fakturační dávky: {invoice_batches_count}")
        self.stdout.write(f"Spotřební koš pro prezentaci: zvol měsíc {basket_month}.")
        self.stdout.write(f"Výchozí heslo virtuálních účtů: {DEFAULT_PASSWORD}")

    def _next_month_value(self, today):
        year = today.year
        month = today.month + 1
        if month == 13:
            year += 1
            month = 1
        return f"{year:04d}-{month:02d}"

    def _parse_month(self, value):
        year_text, month_text = value.split("-", 1)
        return int(year_text), int(month_text)

    def _add_months(self, value, months):
        year = value.year + ((value.month - 1 + months) // 12)
        month = ((value.month - 1 + months) % 12) + 1
        return date(year, month, 1)

    def _month_end(self, value):
        return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])

    def _seed_allergens(self):
        allergens = []
        for name, icon in ALLERGENS:
            allergen, _ = Alergen.objects.update_or_create(
                nazev=name,
                defaults={"ikona": icon},
            )
            allergens.append(allergen)

        jidla = list(Jidlo.objects.order_by("id")[:160])
        for index, jidlo in enumerate(jidla):
            selected = []
            if "kuř" in jidlo.nazev.lower() or "rajsk" in jidlo.nazev.lower():
                selected.extend(allergens[:2])
            if "ryb" in jidlo.nazev.lower():
                selected.append(allergens[2])
            if "mlék" in jidlo.nazev.lower() or "jogurt" in jidlo.nazev.lower() or "kaše" in jidlo.nazev.lower():
                selected.append(allergens[4])
            if not selected and index % 5 == 0:
                selected.append(allergens[index % len(allergens)])
            if selected:
                jidlo.alergeny.add(*selected)
        return allergens

    def _seed_virtual_students(self, students_per_group, allergens, rng):
        User = get_user_model()
        student_group, _ = Group.objects.get_or_create(name="Student")
        groups = {
            group.kod: group
            for group in StravovaciSkupina.objects.filter(kod__in=["DS15+", "DM15+", "PS15+"])
        }
        created_or_updated = 0
        for group_code, group in groups.items():
            prefix = group_code[:2].lower()
            for number in range(1, students_per_group + 1):
                username = f"virtual.{prefix}{number:03d}"
                first_name = FIRST_NAMES[(number + len(prefix)) % len(FIRST_NAMES)]
                last_name = LAST_NAMES[(number * 3) % len(LAST_NAMES)]
                user, _ = User.objects.update_or_create(
                    username=username,
                    defaults={
                        "email": f"{username}@virtualni-skola.local",
                        "first_name": first_name,
                        "last_name": last_name,
                        "is_staff": False,
                        "is_superuser": False,
                        "is_active": True,
                        "stravovaci_skupina": group,
                        "osobni_cislo": f"{group_code.replace('+', '')}-{number:04d}",
                    },
                )
                user.set_password(DEFAULT_PASSWORD)
                user.save()
                user.groups.set([student_group])
                if number % 7 == 0:
                    user.alergeny.set(rng.sample(allergens, k=1))
                elif number % 13 == 0:
                    user.alergeny.set(rng.sample(allergens, k=2))
                created_or_updated += 1
        return created_or_updated

    def _seed_account_deposits(self, rng):
        User = get_user_model()
        users = list(
            User.objects.filter(username__startswith="virtual.", stravovaci_skupina__isnull=False)
            .order_by("username")
        )
        created = 0
        for user in users:
            if Vklad.objects.filter(uzivatel=user, poznamka="Virtuální škola - počáteční kredit").exists():
                continue
            amount = Decimal(rng.choice(["1200.00", "1500.00", "1800.00", "2200.00"]))
            try:
                Vklad.objects.create(
                    uzivatel=user,
                    castka=amount,
                    zpusob_uhrady=rng.choice([Vklad.ZPUSOB_HOTOVOST, Vklad.ZPUSOB_KARTA, Vklad.ZPUSOB_QR]),
                    poznamka="Virtuální škola - počáteční kredit",
                )
                created += 1
            except ValidationError:
                continue
        return created

    def _seed_account_policy_after_deposits(self):
        student_group, _ = Group.objects.get_or_create(name="Student")
        SkupinoveNastaveni.objects.update_or_create(
            skupina=student_group,
            defaults={
                "cerpani_debit": True,
                "nutnost_dobit": False,
                "debit_limit": Decimal("-20000.00"),
            },
        )
        return 1

    def _seed_cash_register(self):
        pokladna, _ = Pokladna.objects.update_or_create(
            nazev="Pokladna 1",
            defaults={
                "popis": "Prezentační školní pokladna",
                "aktivni": True,
                "hotovostni_zustatek": Decimal("5000.00"),
                "qr_iban": "CZ6508000000192000145399",
                "qr_bic": "GIBACZPX",
                "qr_prijemce": "KlikniJídlo demo",
                "qr_zprava": "Školní pokladna",
            },
        )
        return pokladna

    def _foods_by_kind(self):
        result = {"Polévka": [], "Hlavní jídlo": [], "Dezert": []}
        for jidlo in (
            Jidlo.objects
            .select_related("druh")
            .prefetch_related("komponenty_jidla")
            .exclude(nazev__startswith="Prezentační")
            .order_by("nazev")
        ):
            if jidlo.druh and jidlo.druh.nazev in result and jidlo.komponenty_jidla.exists():
                result[jidlo.druh.nazev].append(jidlo)
        return result

    def _ensure_week_menu(self, week_start, rng):
        week_end = week_start + timedelta(days=4)
        menu = (
            Jidelnicek.objects
            .filter(platnost_od__lte=week_start, platnost_do__gte=week_end)
            .order_by("platnost_od", "id")
            .first()
        )
        if not menu:
            menu = Jidelnicek.objects.create(
                platnost_od=week_start,
                platnost_do=week_end,
                ikona="fa-solid fa-calendar-days",
            )

        foods = self._foods_by_kind()
        druhy = {druh.nazev: druh for druh in DruhJidla.objects.filter(nazev__in=foods.keys())}
        usable_count = menu.polozky.exclude(jidlo__nazev__startswith="Prezentační").count()
        for index in range(usable_count, 15):
            kind = ("Polévka", "Hlavní jídlo", "Dezert")[index % 3]
            pool = foods.get(kind) or []
            if not pool or kind not in druhy:
                continue
            jidlo = pool[(index * 17 + rng.randrange(len(pool))) % len(pool)]
            PolozkaJidelnicku.objects.get_or_create(
                jidelnicek=menu,
                druh_jidla=druhy[kind],
                jidlo=jidlo,
            )
        return menu

    def _menu_items_for_day(self, menu, day):
        day_index = day.weekday()
        items = list(
            menu.polozky
            .select_related("druh_jidla", "jidlo")
            .exclude(jidlo__nazev__startswith="Prezentační")
            .order_by("id")
        )
        return items[day_index * 3:day_index * 3 + 3]

    def _seed_future_operation(self, basket_month, future_months, rng):
        basket_year, basket_month_number = self._parse_month(basket_month)
        start = self._add_months(date(basket_year, basket_month_number, 1), 1)
        end = self._month_end(self._add_months(start, future_months - 1))
        users = list(
            get_user_model().objects
            .filter(username__startswith="virtual.", stravovaci_skupina__isnull=False, is_active=True)
            .select_related("stravovaci_skupina")
            .order_by("username")
        )
        orders_count = 0
        items_count = 0
        current = start
        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            week_start = current - timedelta(days=current.weekday())
            menu = self._ensure_week_menu(week_start, rng)
            menu_items = self._menu_items_for_day(menu, current)
            by_kind = {item.druh_jidla.nazev: item for item in menu_items}
            if "Hlavní jídlo" not in by_kind:
                current += timedelta(days=1)
                continue

            for user in users:
                attendance = self._future_attendance(user, rng)
                if not attendance:
                    continue
                order, created = Order.objects.update_or_create(
                    user=user,
                    datum_vydeje=current,
                    defaults={
                        "status": "objednano",
                        "datum_vydani": None,
                    },
                )
                if created:
                    orders_count += 1
                if not order.items.filter(vydano=True).exists():
                    order.items.all().delete()
                selected = self._future_items_for_user(user, by_kind, rng)
                for item in selected:
                    OrderItem.objects.update_or_create(
                        order=order,
                        menu_item=item,
                        defaults={
                            "quantity": 1,
                            "cena": item.jidlo.cena,
                            "vydano": False,
                            "datum_vydani": None,
                        },
                    )
                    items_count += 1
            current += timedelta(days=1)
        return orders_count, items_count

    def _future_attendance(self, user, rng):
        code = user.stravovaci_skupina.kod if user.stravovaci_skupina_id else ""
        if code == "DM15+":
            return rng.random() < 0.92
        if code == "PS15+":
            return rng.random() < 0.88
        return rng.random() < 0.78

    def _future_items_for_user(self, user, by_kind, rng):
        code = user.stravovaci_skupina.kod if user.stravovaci_skupina_id else ""
        selected = []
        if code == "DM15+":
            selected.extend([by_kind.get("Polévka"), by_kind.get("Hlavní jídlo")])
            if rng.random() < 0.50:
                selected.append(by_kind.get("Dezert"))
        elif code == "PS15+":
            if rng.random() < 0.45:
                selected.append(by_kind.get("Polévka"))
            selected.append(by_kind.get("Hlavní jídlo"))
            if rng.random() < 0.22:
                selected.append(by_kind.get("Dezert"))
        else:
            if rng.random() < 0.35:
                selected.append(by_kind.get("Polévka"))
            selected.append(by_kind.get("Hlavní jídlo"))
            if rng.random() < 0.18:
                selected.append(by_kind.get("Dezert"))
        return [item for item in selected if item is not None]

    def _seed_subsidies_and_invoices(self, history_months):
        student_group, _ = Group.objects.get_or_create(name="Student")
        policy, _ = DotacniPolitika.objects.update_or_create(
            skupina=student_group,
            defaults={
                "procento": Decimal("0.00"),
                "castka": Decimal("25.00"),
                "mesicni_limit": 0,
            },
        )
        today = timezone.localdate()
        first_month = self._add_months(date(today.year, today.month, 1), -history_months)
        last_month = self._add_months(date(today.year, today.month, 1), -1)
        subsidies_count = 0
        invoice_count = 0
        current = first_month
        while current <= last_month:
            month_end = self._month_end(current)
            issued_items = (
                OrderItem.objects
                .filter(
                    order__datum_vydeje__gte=current,
                    order__datum_vydeje__lte=month_end,
                    order__user__username__startswith="virtual.",
                    vydano=True,
                )
                .select_related("order__user")
            )
            for item in issued_items[:2500]:
                _, created = Dotace.objects.get_or_create(
                    uzivatel=item.order.user,
                    politika=policy,
                    datum=item.order.datum_vydeje,
                    castka=policy.castka,
                )
                if created:
                    subsidies_count += 1

            total_subsidy = Dotace.objects.filter(
                politika=policy,
                datum__gte=current,
                datum__lte=month_end,
            ).aggregate(total=Sum("castka"))["total"] or Decimal("0")
            total_sales = issued_items.aggregate(total=Sum("cena"))["total"] or Decimal("0")
            batch, _ = FakturacniDavka.objects.update_or_create(
                rok=current.year,
                mesic=current.month,
                defaults={
                    "datum_od": current,
                    "datum_do": month_end,
                    "stav": FakturacniDavka.STAV_UZAVRENO,
                    "dotace_celkem": total_subsidy,
                    "srazky_celkem": total_sales,
                    "polozek": 2,
                    "poznamka": "Prezentační automatická fakturační dávka.",
                },
            )
            batch.polozky.all().delete()
            if total_subsidy:
                FakturacniPolozka.objects.create(
                    davka=batch,
                    typ=FakturacniPolozka.TYP_DOTACE,
                    username_snapshot="zrizovatel",
                    jmeno_snapshot="Souhrn dotací",
                    skupina_snapshot="Student",
                    pocet_porci=issued_items.count(),
                    castka=total_subsidy,
                    detail=f"Dotace za období {current:%m/%Y}.",
                )
            if total_sales:
                FakturacniPolozka.objects.create(
                    davka=batch,
                    typ=FakturacniPolozka.TYP_SRAZKA,
                    username_snapshot="virtualni-skola",
                    jmeno_snapshot="Souhrn stravného",
                    skupina_snapshot="Virtuální škola",
                    pocet_porci=issued_items.count(),
                    castka=total_sales,
                    detail=f"Modelová fakturace stravného za období {current:%m/%Y}.",
                )
            invoice_count += 1
            current = self._add_months(current, 1)
        return subsidies_count, invoice_count

    def _seed_plu_items(self, pokladna):
        dph_12, _ = DPHSkupina.objects.update_or_create(
            nazev="Snížená sazba 12 %",
            defaults={"sazba": Decimal("12.00")},
        )
        categories = {}
        for _name, category_name, *_rest in PLU_DATA:
            categories[category_name], _ = PLUKategorie.objects.get_or_create(nazev=category_name)

        plu_items = []
        for order, (name, category_name, price, bg, icon) in enumerate(PLU_DATA, start=1):
            plu, _ = PLUPolozka.objects.update_or_create(
                nazev=name,
                defaults={
                    "cena": Decimal(price),
                    "dph_skupina": dph_12,
                    "kategorie": categories[category_name],
                    "typ": PLUPolozka.TYP_RECEPTURA,
                    "aktivni": True,
                },
            )
            PokladnaTile.objects.update_or_create(
                pokladna=pokladna,
                plu=plu,
                defaults={
                    "nazev": name,
                    "barva_pozadi": bg if bg in dict(PokladnaTile.BARVY_POZADI) else "#54ae43",
                    "barva_pozadi_custom": "" if bg in dict(PokladnaTile.BARVY_POZADI) else bg,
                    "barva_textu": "#ffffff",
                    "font_bold": True,
                    "font_size_px": 16,
                    "ikona": icon if icon in dict(PokladnaTile.ICON_CHOICES) else "fa-solid fa-utensils",
                    "poradi": order,
                    "aktivni": True,
                },
            )
            plu_items.append(plu)
        return plu_items

    def _seed_cash_sales(self, pokladna, plu_items, cash_days, rng):
        User = get_user_model()
        obsluha = User.objects.filter(username="pokladna").first() or User.objects.filter(is_staff=True).first()
        customers = list(User.objects.filter(username__startswith="virtual.").order_by("username")[:40])
        days = self._last_workdays(timezone.localdate(), cash_days)
        sales_count = 0
        stornos_count = 0
        closed_days = set()
        payment_methods = [
            "HOTOVOST",
            "HOTOVOST",
            "KARTA",
            "KARTA",
            "QR",
            "KONTO",
        ]

        for day in days:
            daily_count = rng.randint(8, 18)
            for index in range(daily_count):
                customer = rng.choice(customers) if customers and rng.random() < 0.45 else None
                doklad = vytvor_doklad(pokladna, obsluha, zakaznik=customer)
                for _ in range(rng.randint(1, 3)):
                    plu = rng.choice(plu_items)
                    qty = Decimal(str(rng.choice([1, 1, 1, 2])))
                    pridej_polozku(doklad, plu, qty)
                payment = rng.choice(payment_methods)
                if payment == "KONTO" and not customer:
                    payment = "HOTOVOST"
                try:
                    if payment == "QR":
                        zahaj_qr_platbu(doklad, user=obsluha)
                        potvrdit_qr_platbu(doklad, user=obsluha)
                    else:
                        uzavri_doklad(doklad, payment, user=obsluha)
                except ValidationError:
                    uzavri_doklad(doklad, "HOTOVOST", user=obsluha)

                sold_at = self._sale_datetime(day, index, daily_count)
                self._move_cash_document_to_time(doklad, sold_at)
                sales_count += 1
                closed_days.add(day)

                if index == daily_count - 1 and rng.random() < 0.28:
                    try:
                        stornuj_doklad(doklad, user=obsluha, duvod="Prezentační storno účtenky")
                        self._move_cash_document_to_time(doklad, sold_at + timedelta(minutes=3))
                        stornos_count += 1
                    except ValidationError:
                        pass

        closures_count = 0
        for day in sorted(closed_days):
            try:
                uzavri_denni_uzaverku(
                    pokladna,
                    day,
                    user=obsluha,
                    hotovost_spoctena=None,
                    poznamka="Prezentační denní uzávěrka",
                )
                closures_count += 1
            except ValidationError as exc:
                self.stdout.write(self.style.WARNING(f"Uzávěrka {day:%d.%m.%Y} přeskočena: {exc}"))

        return sales_count, stornos_count, closures_count

    def _last_workdays(self, today, count):
        days = []
        current = today - timedelta(days=1)
        while len(days) < count:
            if current.weekday() < 5:
                days.append(current)
            current -= timedelta(days=1)
        return list(reversed(days))

    def _sale_datetime(self, day, index, total):
        start = datetime.combine(day, time(hour=7, minute=15))
        minute = int((index + 1) * (Decimal("480") / Decimal(max(total, 1))))
        return timezone.make_aware(start + timedelta(minutes=minute))

    def _move_cash_document_to_time(self, doklad, value):
        updates = {"datum": value}
        if doklad.uzavren_at:
            updates["uzavren_at"] = value
        if doklad.stornovano_at:
            updates["stornovano_at"] = value
        if doklad.qr_vytvoren_at:
            updates["qr_vytvoren_at"] = value
        if doklad.qr_potvrzen_at:
            updates["qr_potvrzen_at"] = value
        type(doklad).objects.filter(pk=doklad.pk).update(**updates)
        doklad.refresh_from_db()

    def _seed_surveys(self, rng):
        questions = []
        for order, text in enumerate(ANKETA_OTAZKY, start=1):
            question, _ = AnketniOtazka.objects.update_or_create(
                text=text,
                defaults={
                    "napoveda": "Hodnocení 1 až 5, kde 5 je nejlepší.",
                    "aktivni": True,
                    "povinna": True,
                    "poradi": order,
                },
            )
            questions.append(question)

        order_items = list(
            OrderItem.objects.filter(vydano=True, hodnoceni__isnull=True)
            .select_related("order__user", "menu_item__jidlo")
            .order_by("-order__datum_vydeje")[:220]
        )
        created = 0
        notes = [
            "",
            "",
            "Výborné, dal/a bych si znovu.",
            "Porce byla akorát.",
            "Příště bych uvítal/a méně soli.",
            "Dezert byl skvělý.",
        ]
        for item in order_items:
            hodnoceni = HodnoceniJidla.objects.create(
                user=item.order.user,
                order_item=item,
                datum_vydeje=item.order.datum_vydeje,
                jidlo_nazev=item.menu_item.jidlo.nazev,
                poznamka=rng.choice(notes),
                vytvoreno=self._rating_datetime(item.order.datum_vydeje, rng),
            )
            base_score = rng.choice([3, 4, 4, 4, 5, 5])
            for question in questions:
                score = max(1, min(5, base_score + rng.choice([-1, 0, 0, 1])))
                OdpovedHodnoceni.objects.create(
                    hodnoceni_jidla=hodnoceni,
                    otazka=question,
                    znamka=score,
                )
            created += 1
        return created

    def _rating_datetime(self, day, rng):
        value = datetime.combine(day, time(hour=rng.randint(12, 18), minute=rng.randint(0, 59)))
        return timezone.make_aware(value)
