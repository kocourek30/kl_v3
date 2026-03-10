# sklad/services.py
from collections import defaultdict
from decimal import Decimal
from datetime import date

from .models import PohybSkladu

def spocitej_spotrebu_sk_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Vrátí dict { 'MASO': Decimal(...), 'RYBY': Decimal(...), ... }
    za daný měsíc (podle data výdejky).
    """
    qs = PohybSkladu.objects.filter(
        typ="VYDEJ",
        vydejka__datum__year=rok,
        vydejka__datum__month=mesic,
    ).select_related("surovina", "vydejka")

    if stravovaci_skupina is not None:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    vysledky = defaultdict(Decimal)
    for pohyb in qs:
        sk = pohyb.surovina.skupina_sk  # MASO, RYBY, ...
        koef = pohyb.surovina.koeficient_sk or Decimal("1.0")
        vysledky[sk] += pohyb.mnozstvi * koef

    return vysledky
