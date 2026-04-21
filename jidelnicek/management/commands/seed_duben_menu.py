from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku


@dataclass(frozen=True)
class MealSpec:
    nazev: str
    cena: Decimal


MEAL_LIBRARY: dict[str, list[MealSpec]] = {
    "Snídaně": [
        MealSpec("Houska, šunka, sýr, zelenina", Decimal("32.00")),
        MealSpec("Míchaná vejce, chléb, zelenina", Decimal("34.00")),
        MealSpec("Pomazánka z tuňáka, rohlík, paprika", Decimal("33.00")),
        MealSpec("Šunková pěna, vícezrnný chléb", Decimal("31.00")),
        MealSpec("Párky, hořčice, pečivo", Decimal("36.00")),
        MealSpec("Lučina, bageta, rajče", Decimal("30.00")),
        MealSpec("Vaječná pomazánka, chléb, okurka", Decimal("31.00")),
    ],
    "1. Svačina": [
        MealSpec("Jablko a cereální tyčinka", Decimal("18.00")),
        MealSpec("Bílý jogurt s medem", Decimal("19.00")),
        MealSpec("Banán a ovesná sušenka", Decimal("18.00")),
        MealSpec("Tvarohový krém s ovocem", Decimal("21.00")),
        MealSpec("Hruška a müsli kuličky", Decimal("18.00")),
    ],
    "Oběd": [
        MealSpec("Kuřecí řízek, bramborová kaše", Decimal("112.00")),
        MealSpec("Vepřový guláš, houskový knedlík", Decimal("114.00")),
        MealSpec("Smažený květák, vařené brambory", Decimal("105.00")),
        MealSpec("Hovězí na česneku, rýže", Decimal("118.00")),
        MealSpec("Kuřecí nudličky na kari, jasmínová rýže", Decimal("115.00")),
        MealSpec("Rajská omáčka, hovězí maso, těstoviny", Decimal("117.00")),
        MealSpec("Zapečené těstoviny se sýrem a brokolicí", Decimal("106.00")),
        MealSpec("Pečené rybí filé, brambory, salát", Decimal("119.00")),
        MealSpec("Svíčková na smetaně, houskový knedlík", Decimal("123.00")),
        MealSpec("Krůtí plátek, kuskus se zeleninou", Decimal("116.00")),
    ],
    "2. Svačina": [
        MealSpec("Rohlík s pomazánkovým máslem", Decimal("17.00")),
        MealSpec("Sýrový croissant", Decimal("19.00")),
        MealSpec("Toast se šunkou a sýrem", Decimal("21.00")),
        MealSpec("Ovocný kefír", Decimal("18.00")),
        MealSpec("Toustový chléb s lučinou", Decimal("18.00")),
    ],
    "Večeře": [
        MealSpec("Kuřecí plátek, šťouchané brambory", Decimal("82.00")),
        MealSpec("Těstovinový salát s kuřecím masem", Decimal("78.00")),
        MealSpec("Zapečené brambory se sýrem", Decimal("76.00")),
        MealSpec("Sekaná, bramborový salát", Decimal("84.00")),
        MealSpec("Rizoto se zeleninou a sýrem", Decimal("77.00")),
        MealSpec("Pečené kuře, rýže", Decimal("83.00")),
    ],
    "2. Večeře": [
        MealSpec("Bílý jogurt", Decimal("14.00")),
        MealSpec("Tavený sýr a rohlík", Decimal("16.00")),
        MealSpec("Ovoce sezóny", Decimal("13.00")),
        MealSpec("Pudinkový kelímek", Decimal("15.00")),
        MealSpec("Cottage a křehký chléb", Decimal("16.00")),
    ],
}


class Command(BaseCommand):
    help = "Naplní testovací jídla a jídelníček od dneška do konce dubna podle existujících druhů jídel."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=None, help="Rok pro seed menu.")
        parser.add_argument("--month", type=int, default=None, help="Měsíc pro seed menu.")
        parser.add_argument(
            "--start-date",
            type=str,
            default=None,
            help="Volitelný počáteční den ve formátu YYYY-MM-DD. Výchozí je dnešek.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        year = options["year"] or today.year
        month = options["month"] or today.month

        if options["start_date"]:
            try:
                start = date.fromisoformat(options["start_date"])
            except ValueError as exc:
                raise CommandError("Neplatné --start-date, použij YYYY-MM-DD.") from exc
        else:
            start = today

        if start.year != year or start.month != month:
            raise CommandError("Počáteční datum musí ležet ve stejném měsíci a roce jako seed.")

        end = date(year, month, monthrange(year, month)[1])
        if start > end:
            raise CommandError("Počáteční datum nesmí být po konci zvoleného měsíce.")

        druhy = {d.nazev: d for d in DruhJidla.objects.all()}
        missing = [nazev for nazev in MEAL_LIBRARY if nazev not in druhy]
        if missing:
            raise CommandError(
                "V databázi chybí druhy jídel: "
                + ", ".join(missing)
                + ". Nejprve je založ nebo uprav seed."
            )

        self._validate_overlaps(start, end)
        self._seed_jidla(druhy)
        self._replace_menu_range(start, end, druhy)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed jídelníčku hotový pro období {start.isoformat()} až {end.isoformat()}."
            )
        )

    def _validate_overlaps(self, start: date, end: date) -> None:
        overlaps = Jidelnicek.objects.filter(platnost_od__lte=end, platnost_do__gte=start)
        partial = overlaps.exclude(platnost_od__gte=start, platnost_do__lte=end)
        if partial.exists():
            labels = ", ".join(
                f"{item.platnost_od} až {item.platnost_do}" for item in partial.order_by("platnost_od")
            )
            raise CommandError(
                "V zadaném období existují překrývající se vícedenní jídelníčky, které seed z bezpečnostních důvodů nemaže: "
                + labels
            )

    def _seed_jidla(self, druhy: dict[str, DruhJidla]) -> None:
        for druh_nazev, meals in MEAL_LIBRARY.items():
            druh = druhy[druh_nazev]
            for meal in meals:
                jidlo, created = Jidlo.objects.get_or_create(
                    nazev=meal.nazev,
                    druh=druh,
                    defaults={"cena": meal.cena},
                )
                if not created and jidlo.cena != meal.cena:
                    jidlo.cena = meal.cena
                    jidlo.save(update_fields=["cena"])

    def _replace_menu_range(self, start: date, end: date, druhy: dict[str, DruhJidla]) -> None:
        existing = Jidelnicek.objects.filter(
            platnost_od__gte=start,
            platnost_do__lte=end,
        )
        deleted_count = existing.count()
        if deleted_count:
            existing.delete()
            self.stdout.write(f"Smazáno existujících jídelníčků v rozsahu: {deleted_count}")

        day_count = (end - start).days + 1
        for offset in range(day_count):
            current_day = start + timedelta(days=offset)
            jidelnicek = Jidelnicek.objects.create(
                platnost_od=current_day,
                platnost_do=current_day,
                ikona="",
            )

            for druh_nazev, meals in MEAL_LIBRARY.items():
                meal = meals[offset % len(meals)]
                jidlo = Jidlo.objects.get(nazev=meal.nazev, druh=druhy[druh_nazev])
                PolozkaJidelnicku.objects.create(
                    jidelnicek=jidelnicek,
                    druh_jidla=druhy[druh_nazev],
                    jidlo=jidlo,
                )
