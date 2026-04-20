import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from sklad.models import (
    JidloKomponenta,
    KomponentaJidla,
    KomponentaSurovina,
    SarzeSkladu,
    StavSkladu,
    Surovina,
    Vydejka,
)
from sklad.services import (
    generate_vydejka_from_orders,
    priprav_radky_spotrebi_kos_tabulka,
    uzavri_vydejku,
)
from users.models import StravovaciSkupina


PRESENTATION_MARKER = "Prezentační spotřební koš"


INGREDIENTS_15_PLUS_LUNCH = [
    {
        "nazev": "Kuřecí maso prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_MASO,
        "mnozstvi_na_porci": Decimal("65.000"),
        "cena": Decimal("0.1450"),
    },
    {
        "nazev": "Rybí filé prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_RYBY,
        "mnozstvi_na_porci": Decimal("16.000"),
        "cena": Decimal("0.1800"),
    },
    {
        "nazev": "Mléko prezentační",
        "jednotka": Surovina.JEDNOTKA_ML,
        "skupina_sk": Surovina.SK_MLEKO,
        "mnozstvi_na_porci": Decimal("111.000"),
        "cena": Decimal("0.0220"),
    },
    {
        "nazev": "Řepkový olej prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_TUKY,
        "mnozstvi_na_porci": Decimal("17.000"),
        "cena": Decimal("0.0520"),
    },
    {
        "nazev": "Cukr prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_CUKRY,
        "mnozstvi_na_porci": Decimal("14.000"),
        "cena": Decimal("0.0210"),
    },
    {
        "nazev": "Zelenina a ovoce prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_ZELENINA_OVOCE,
        "mnozstvi_na_porci": Decimal("233.000"),
        "cena": Decimal("0.0400"),
    },
    {
        "nazev": "Brambory prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_BRAMBORY,
        "mnozstvi_na_porci": Decimal("132.000"),
        "cena": Decimal("0.0120"),
    },
    {
        "nazev": "Bulgur celozrnný prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_CELOZRNNE,
        "mnozstvi_na_porci": Decimal("25.000"),
        "cena": Decimal("0.0310"),
    },
    {
        "nazev": "Čočka prezentační",
        "jednotka": Surovina.JEDNOTKA_G,
        "skupina_sk": Surovina.SK_LUSTENINY,
        "mnozstvi_na_porci": Decimal("15.000"),
        "cena": Decimal("0.0470"),
    },
]


class Command(BaseCommand):
    help = (
        "Naplní databázi prezentačními objednávkami a uzavřenými výdejkami tak, "
        "aby měsíční spotřební koš pro skupiny 15+ vycházel v toleranci."
    )

    def add_arguments(self, parser):
        dnes = timezone.localdate()
        parser.add_argument(
            "--month",
            default=f"{dnes.year:04d}-{dnes.month:02d}",
            help="Měsíc pro naplnění ve formátu RRRR-MM. Výchozí je aktuální měsíc.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=20,
            help="Počet pracovních dnů, které se mají naplnit. Výchozí je 20.",
        )
        parser.add_argument(
            "--groups",
            default="DS15+,DM15+,PS15+",
            help="Kódy stravovacích skupin oddělené čárkou.",
        )
        parser.add_argument(
            "--users-per-group",
            type=int,
            default=3,
            help="Kolik demo strávníků z každé skupiny objedná každý den. Výchozí odpovídá demo SOU seedu.",
        )
    def handle(self, *args, **options):
        year, month = self._parse_month(options["month"])
        days = self._workdays(year, month, options["days"])
        if not days:
            raise CommandError("Pro zadaný měsíc nebyl nalezen žádný pracovní den.")

        group_codes = [code.strip() for code in options["groups"].split(",") if code.strip()]
        users_per_group = max(1, int(options["users_per_group"]))

        call_command("seed_sou_users", verbosity=0)
        call_command("seed_spotrebni_kos_2025", verbosity=0)

        with transaction.atomic():
            groups = self._get_groups(group_codes)
            suroviny = self._ensure_ingredients()
            jidlo = self._ensure_balanced_meal(suroviny)
            total_portions = len(days) * users_per_group * len(groups)
            self._ensure_stock(suroviny, total_portions, year, month)

            created_orders = 0
            created_order_items = 0
            closed_vydejky = 0
            skipped_closed = 0

            close_user = get_user_model().objects.filter(is_superuser=True).order_by("id").first()

            for group in groups:
                users = self._get_users_for_group(group, users_per_group)
                for day in days:
                    existing_closed = Vydejka.objects.filter(
                        datum=day,
                        stravovaci_skupina=group,
                        typ_stravy=Vydejka.TYP_STRAVY_OBED,
                        uzavreny=True,
                        stornovano=False,
                    ).first()
                    if existing_closed:
                        skipped_closed += 1
                        continue

                    menu_item = self._ensure_menu_item(day, jidlo)
                    for user in users:
                        order, order_created = Order.objects.update_or_create(
                            user=user,
                            datum_vydeje=day,
                            defaults={
                                "status": "vydano",
                                "datum_vydani": self._aware_noon(day),
                            },
                        )
                        if order_created:
                            created_orders += 1
                        order.items.all().delete()
                        OrderItem.objects.create(
                            order=order,
                            menu_item=menu_item,
                            quantity=1,
                            cena=jidlo.cena,
                            vydano=True,
                            datum_vydani=self._aware_noon(day),
                        )
                        created_order_items += 1

                    vydejka, _created = generate_vydejka_from_orders(
                        day,
                        group,
                        Vydejka.TYP_STRAVY_OBED,
                    )
                    vydejka.popis = f"{PRESENTATION_MARKER} 15+ / {year:04d}-{month:02d}"
                    vydejka.save(update_fields=["popis"])
                    uzavri_vydejku(vydejka, user=close_user)
                    closed_vydejky += 1

        self.stdout.write(self.style.SUCCESS("Prezentační data spotřebního koše byla naplněna."))
        self.stdout.write(f"Období: {days[0].strftime('%d.%m.%Y')} - {days[-1].strftime('%d.%m.%Y')}")
        self.stdout.write(f"Skupiny: {', '.join(group.kod for group in groups)}")
        self.stdout.write(f"Objednávky: {created_orders}, položky objednávek: {created_order_items}")
        self.stdout.write(f"Uzavřené výdejky: {closed_vydejky}, přeskočené uzavřené výdejky: {skipped_closed}")
        self._print_report_preview(year, month, days[0], days[-1], groups)

    def _parse_month(self, value):
        try:
            year_str, month_str = value.split("-", 1)
            year = int(year_str)
            month = int(month_str)
            date(year, month, 1)
            return year, month
        except (TypeError, ValueError) as exc:
            raise CommandError("Měsíc musí být ve formátu RRRR-MM, například 2026-04.") from exc

    def _workdays(self, year, month, limit):
        last_day = calendar.monthrange(year, month)[1]
        days = [
            date(year, month, day)
            for day in range(1, last_day + 1)
            if date(year, month, day).weekday() < 5
        ]
        return days[: max(1, int(limit))]

    def _get_groups(self, group_codes):
        groups = list(StravovaciSkupina.objects.filter(kod__in=group_codes).order_by("kod"))
        found = {group.kod for group in groups}
        missing = [code for code in group_codes if code not in found]
        if missing:
            raise CommandError(f"Chybí stravovací skupiny: {', '.join(missing)}")
        return groups

    def _get_users_for_group(self, group, limit):
        User = get_user_model()
        users = list(
            User.objects.filter(stravovaci_skupina=group, is_active=True)
            .filter(username__startswith="student.")
            .union(
                User.objects.filter(
                    stravovaci_skupina=group,
                    is_active=True,
                    username__startswith="virtual.",
                )
            )
            .order_by("username")[:limit]
        )
        if len(users) < limit:
            raise CommandError(
                f"Skupina {group.kod} má jen {len(users)} demo strávníků. "
                "Spusť seed_sou_users nebo sniž --users-per-group."
            )
        return users

    def _ensure_ingredients(self):
        suroviny = []
        for item in INGREDIENTS_15_PLUS_LUNCH:
            surovina, _created = Surovina.objects.update_or_create(
                nazev=item["nazev"],
                defaults={
                    "jednotka": item["jednotka"],
                    "skupina_sk": item["skupina_sk"],
                    "prumerna_cena_za_jednotku": item["cena"],
                },
            )
            surovina._presentation_amount = item["mnozstvi_na_porci"]
            surovina._presentation_price = item["cena"]
            suroviny.append(surovina)
        return suroviny

    def _ensure_balanced_meal(self, suroviny):
        druh, _created = DruhJidla.objects.get_or_create(
            nazev="Hlavní jídlo",
            defaults={"ikona": "fas fa-utensils"},
        )
        jidlo, _created = Jidlo.objects.update_or_create(
            nazev="Prezentační vyvážený oběd 15+",
            defaults={
                "cena": Decimal("89.00"),
                "druh": druh,
                "sk_bile_maso": True,
            },
        )
        komponenta, _created = KomponentaJidla.objects.update_or_create(
            nazev="Prezentační normová skladba 15+",
            defaults={
                "typ": KomponentaJidla.TYP_OSTATNI,
                "aktivni": True,
                "porce_text": "1 normovaná porce",
                "poznamka": (
                    "Kalibrovaná komponenta pro prezentační data. "
                    "Množství odpovídá normám spotřebního koše pro 15+ oběd."
                ),
            },
        )
        for surovina in suroviny:
            KomponentaSurovina.objects.update_or_create(
                komponenta=komponenta,
                surovina=surovina,
                defaults={"mnozstvi_na_porci": surovina._presentation_amount},
            )
        JidloKomponenta.objects.update_or_create(
            jidlo=jidlo,
            komponenta=komponenta,
            defaults={
                "mnozstvi_nasobek": Decimal("1.000"),
                "poradi": 1,
                "povinna": True,
            },
        )
        return jidlo

    def _ensure_stock(self, suroviny, total_portions, year, month):
        expires_at = date(year, month, 1) + timedelta(days=365)
        sarze_name = f"PREZ-SK-{year:04d}{month:02d}"
        for surovina in suroviny:
            required = (surovina._presentation_amount * Decimal(total_portions) * Decimal("1.25")).quantize(
                Decimal("0.001")
            )
            stav, _created = StavSkladu.objects.get_or_create(
                surovina=surovina,
                defaults={"mnozstvi": Decimal("0"), "min_mnozstvi": Decimal("0")},
            )
            if (stav.mnozstvi or Decimal("0")) < required:
                stav.mnozstvi = required
            if (stav.min_mnozstvi or Decimal("0")) <= 0:
                stav.min_mnozstvi = (required * Decimal("0.10")).quantize(Decimal("0.001"))
            stav.save(update_fields=["mnozstvi", "min_mnozstvi"])

            sarze, _created = SarzeSkladu.objects.update_or_create(
                surovina=surovina,
                sarze=sarze_name,
                defaults={
                    "typ_data_spotreby": "POUZITELNOST",
                    "datum_spotreby": expires_at,
                    "mnozstvi_prijato": required,
                    "mnozstvi_zbyva": required,
                    "cena_za_jednotku": surovina._presentation_price,
                    "stav": SarzeSkladu.STAV_POUZITELNA,
                    "poznamka": PRESENTATION_MARKER,
                },
            )
            if sarze.mnozstvi_zbyva < required:
                sarze.mnozstvi_zbyva = required
                sarze.mnozstvi_prijato = max(sarze.mnozstvi_prijato, required)
                sarze.stav = SarzeSkladu.STAV_POUZITELNA
                sarze.save(update_fields=["mnozstvi_zbyva", "mnozstvi_prijato", "stav"])

    def _ensure_menu_item(self, day, jidlo):
        jidelnicek = (
            Jidelnicek.objects.filter(platnost_od__lte=day, platnost_do__gte=day)
            .order_by("platnost_od", "id")
            .first()
        )
        if not jidelnicek:
            week_start = day - timedelta(days=day.weekday())
            week_end = week_start + timedelta(days=4)
            jidelnicek = Jidelnicek.objects.create(
                platnost_od=week_start,
                platnost_do=week_end,
                ikona="fas fa-calendar-days",
            )
        druh = jidlo.druh or DruhJidla.objects.get(nazev="Hlavní jídlo")
        menu_item, _created = PolozkaJidelnicku.objects.get_or_create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        return menu_item

    def _aware_noon(self, day):
        return timezone.make_aware(datetime.combine(day, time(hour=12)))

    def _print_report_preview(self, year, month, date_from, date_to, groups):
        for group in groups:
            rows = priprav_radky_spotrebi_kos_tabulka(
                year,
                month,
                stravovaci_skupina=group,
                date_from=date_from,
                date_to=date_to,
            )
            ok = sum(1 for row in rows if row["stav"] == "ok")
            total = len(rows)
            self.stdout.write(f"Kontrola {group.kod}: {ok}/{total} skupin v toleranci.")
            for row in rows:
                pct = row["skutecnost_pct"].quantize(Decimal("0.01"))
                self.stdout.write(f"  - {row['skupina_nazev']}: {pct} % / {row['stav']}")
