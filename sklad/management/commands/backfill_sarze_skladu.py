from django.core.management.base import BaseCommand
from django.db import transaction

from sklad.models import PolozkaPrijmu, SarzeSkladu, StavSkladu, Surovina
from sklad.services import stav_sarze_podle_data, vytvor_nebo_aktualizuj_sarzi_z_prijmu


class Command(BaseCommand):
    help = "Doplní šaržový sklad ze starších položek příjemek."

    def _dorovnej_sarze_na_aktualni_stav(self):
        """
        Historická data neměla vazbu výdejů na šarže. Po založení šarží z příjemek
        proto zůstatky dorovnáme na aktuální StavSkladu tak, jako by se dřívější
        výdeje odepisovaly metodou FEFO.
        """
        upraveno = 0
        for surovina in Surovina.objects.all().iterator():
            stav = StavSkladu.objects.filter(surovina=surovina).first()
            cilovy_stav = stav.mnozstvi if stav else 0
            sarze_qs = SarzeSkladu.objects.filter(surovina=surovina, mnozstvi_zbyva__gt=0)
            aktualni_sarze = sum((sarze.mnozstvi_zbyva or 0) for sarze in sarze_qs)
            nadbytek = aktualni_sarze - cilovy_stav
            if nadbytek <= 0:
                continue

            for sarze in sarze_qs.order_by("datum_spotreby", "id"):
                if nadbytek <= 0:
                    break
                odebrat = min(sarze.mnozstvi_zbyva or 0, nadbytek)
                if odebrat <= 0:
                    continue
                sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or 0) - odebrat
                sarze.stav = stav_sarze_podle_data(sarze)
                sarze.save(update_fields=["mnozstvi_zbyva", "stav"])
                nadbytek -= odebrat
                upraveno += 1
        return upraveno

    @transaction.atomic
    def handle(self, *args, **options):
        count = 0
        qs = (
            PolozkaPrijmu.objects
            .filter(prijem__uzavreny=True, prijem__stornovano=False)
            .select_related("surovina", "prijem")
            .order_by("prijem__datum", "id")
        )
        for polozka in qs:
            vytvor_nebo_aktualizuj_sarzi_z_prijmu(polozka)
            count += 1

        upraveno = self._dorovnej_sarze_na_aktualni_stav()
        self.stdout.write(self.style.SUCCESS(f"Doplněno / aktualizováno šarží: {count}"))
        self.stdout.write(self.style.SUCCESS(f"Dorovnáno šarží podle aktuálního stavu skladu: {upraveno}"))
