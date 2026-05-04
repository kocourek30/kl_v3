from datetime import date

from django.core.management.base import BaseCommand

from fakturace.services import vytvor_nebo_prepocitej_davku


class Command(BaseCommand):
    help = "Vytvoří nebo přepočítá měsíční fakturační dávku pro dotace a srážky ze mzdy."

    def add_arguments(self, parser):
        today = date.today()
        default_month = today.month - 1 or 12
        default_year = today.year if today.month > 1 else today.year - 1
        parser.add_argument("--rok", type=int, default=default_year)
        parser.add_argument("--mesic", type=int, default=default_month)

    def handle(self, *args, **options):
        davka = vytvor_nebo_prepocitej_davku(options["rok"], options["mesic"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Fakturační dávka {davka.mesic:02d}/{davka.rok} hotová: "
                f"dotace {davka.dotace_celkem} Kč, srážky {davka.srazky_celkem} Kč, položek {davka.polozek}."
            )
        )
