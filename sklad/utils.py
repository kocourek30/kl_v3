from decimal import Decimal
from typing import Tuple, Dict

from django.db import transaction

from jidelnicek.models import Jidlo
from .models import StavSkladu


def spocitej_odecet_pro_jidlo(jidlo: Jidlo, pocet_porci: int) -> Tuple[bool, Dict]:
    """
    Spočítá, kolik surovin by se odečetlo pro dané jídlo a počet porcí.
    Vrací (ok, detaily), kde detaily je dict:
      {surovina: {"potreba": x, "stav": y, "po_odecetu": z}}
    Nic fakticky neodečítá.
    """
    spotreba = jidlo.vypocitej_spotrebu_surovin(pocet_porci)
    vysledek = {}
    ok = True

    for surovina, potreba in spotreba.items():
        try:
            stav = surovina.stav  # OneToOne StavSkladu
            aktualni = stav.mnozstvi
        except StavSkladu.DoesNotExist:
            aktualni = Decimal("0")

        po_odecetu = aktualni - potreba
        if po_odecetu < 0:
            ok = False

        vysledek[surovina] = {
            "potreba": potreba,
            "stav": aktualni,
            "po_odecetu": po_odecetu,
        }

    return ok, vysledek

@transaction.atomic
def odeber_ze_skladu_pro_jidlo(jidlo: Jidlo, pocet_porci: int) -> Tuple[bool, Dict]:
    """
    Pokusí se odečíst suroviny ze skladu podle receptury jídla a počtu porcí.
    Vrací (ok, detaily) – detaily stejné jako u spocitej_odecet_pro_jidlo.
    Pokud není dost zásob, nic neodečte (atomic).
    """
    ok, detaily = spocitej_odecet_pro_jidlo(jidlo, pocet_porci)

    if not ok:
        # nedostatek surovin – nic neměníme
        return False, detaily

    # dost surovin – provedeme odečet
    for surovina, info in detaily.items():
        try:
            stav = surovina.stav
        except StavSkladu.DoesNotExist:
            # nemáme stav, přeskakujeme nebo můžeme vyhodit výjimku
            continue

        nove_mnozstvi = info["po_odecetu"]
        stav.mnozstvi = nove_mnozstvi
        stav.save(update_fields=["mnozstvi"])

    return True, detaily
