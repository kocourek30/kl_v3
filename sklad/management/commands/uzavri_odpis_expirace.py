from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from sklad.models import OdpisExpirace, SarzeSkladu
from sklad.services import aktualizuj_stavy_sarzi, souhrn_odpisu_expirace, uzavri_odpis_expirace


class Command(BaseCommand):
    help = "Vytvoří a uzavře odpis expirovaných šarží typu 'Spotřebujte do'."

    def add_arguments(self, parser):
        parser.add_argument(
            "--datum",
            default=date.today().isoformat(),
            help="Datum odpisu ve formátu RRRR-MM-DD. Výchozí je dnešek.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Jen vypíše počet expirovaných šarží bez vytvoření dokladu.",
        )

    def handle(self, *args, **options):
        datum = parse_date(options["datum"])
        if datum is None:
            raise CommandError("Datum musí být ve formátu RRRR-MM-DD.")

        aktualizuj_stavy_sarzi(dnes=datum)
        qs = SarzeSkladu.objects.filter(
            stav=SarzeSkladu.STAV_EXPIROVANA,
            mnozstvi_zbyva__gt=0,
            datum_spotreby__lte=datum,
        )
        pocet = qs.count()
        if options["dry_run"]:
            self.stdout.write(f"Expirovaných šarží k odpisu: {pocet}")
            return
        if not pocet:
            self.stdout.write(self.style.WARNING("Neexistují žádné expirované šarže k odpisu."))
            return

        odpis = OdpisExpirace.objects.create(
            datum=datum,
            popis=f"Automatický odpis expirovaných šarží k {datum.strftime('%d.%m.%Y')}",
        )
        uzavri_odpis_expirace(odpis)
        souhrn = souhrn_odpisu_expirace(odpis)

        self.stdout.write(self.style.SUCCESS(f"Uzavřen odpis expirace #{odpis.id}."))
        self.stdout.write(f"Odepsaných položek: {souhrn['pocet_pohybu']}")
        self.stdout.write(f"Hodnota odpisu: {souhrn['hodnota_celkem']:.2f} Kč")
