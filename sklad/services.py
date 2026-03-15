# sklad/services.py
from collections import defaultdict
from decimal import Decimal

from .models import (
    PohybSkladu,
    NormaSpotrebnihoKose,
    Surovina,
    ToleranceSpotrebnihoKose,
)


SKUPINY_SK_PORADI = [
    ("MASO", "maso"),
    ("RYBY", "ryby"),
    ("MLEX", "mléko a mléčné výrobky"),
    ("OBIL", "obiloviny"),
    ("LUST", "luštěniny"),
    ("ZEL", "zelenina"),
    ("OVO", "ovoce"),
    ("BRAM", "brambory"),
    ("TUKY", "tuky volné"),
    ("CUKR", "cukr volný"),
]


def spocitej_spotrebu_sk_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Vrátí dict { 'MASO': Decimal(gramy), ... } za měsíc,
    přepočteno na g a zohledněno koeficientem SK.
    """
    qs = (
        PohybSkladu.objects.filter(
            typ="VYDEJ",
            vydejka__datum__year=rok,
            vydejka__datum__month=mesic,
        )
        .select_related("surovina", "vydejka")
    )

    if stravovaci_skupina is not None:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    vysledky = defaultdict(Decimal)

    for pohyb in qs:
        s: Surovina = pohyb.surovina
        if s.skupina_sk == "NONE":
            continue

        mnozstvi_g = s.mnozstvi_do_gramu(pohyb.mnozstvi)
        koef = s.koeficient_sk or Decimal("1")
        sk_gramy = mnozstvi_g * koef

        vysledky[s.skupina_sk] += sk_gramy

    return vysledky


def priprav_radky_spotrebi_kos_tabulka(
    rok: int,
    mesic: int,
    stravovaci_skupina,
    pocet_stravniku: int,
):
    """
    Vrátí list řádků pro tabulku ve formátu:
    [
      {
        "skupina_kod": "MASO",
        "skupina_nazev": "maso",
        "norma_g": Decimal,
        "skutecnost_g": Decimal,
        "rozdil_g": Decimal,
        "skutecnost_pct": Decimal,
        "min_pct": Decimal,
        "max_pct": Decimal | None,
        "splneno": bool,
        "stav": str,
      },
      ...
    ]
    Seřazeno podle SKUPINY_SK_PORADI.
    """
    skutecnost = spocitej_spotrebu_sk_mesic(rok, mesic, stravovaci_skupina)

    normy_qs = NormaSpotrebnihoKose.objects.filter(
        stravovaci_skupina=stravovaci_skupina
    )
    normy_map = {n.skupina_sk: n.norma_g_mesic for n in normy_qs}

    toler_qs = ToleranceSpotrebnihoKose.objects.filter(
        stravovaci_skupina=stravovaci_skupina
    )
    toler_map = {t.skupina_sk: (t.min_pct, t.max_pct) for t in toler_qs}

    radky = []
    pocet = Decimal(pocet_stravniku or 0)

    for kod, nazev in SKUPINY_SK_PORADI:
        norma_na_1 = normy_map.get(kod, Decimal("0"))
        norma_celkem = norma_na_1 * pocet

        skutecnost_g = skutecnost.get(kod, Decimal("0"))
        rozdil_g = skutecnost_g - norma_celkem

        if norma_celkem:
            skutecnost_pct = (skutecnost_g / norma_celkem) * Decimal("100")
        else:
            skutecnost_pct = Decimal("0")

        # výchozí metodické hodnoty, když není v adminu nic vyplněno
        default_min = Decimal("75")
        default_max = Decimal("125")

        min_pct, max_pct = toler_map.get(kod, (default_min, default_max))

        if norma_celkem == 0:
            splneno = False
            stav = "bez normy"
        else:
            if min_pct is not None and skutecnost_pct < min_pct:
                splneno = False
                stav = "pod"
            elif max_pct is not None and skutecnost_pct > max_pct:
                splneno = False
                stav = "nad"
            else:
                splneno = True
                stav = "v toleranci"

        radky.append(
            {
                "skupina_kod": kod,
                "skupina_nazev": nazev,
                "norma_g": norma_celkem,
                "skutecnost_g": skutecnost_g,
                "rozdil_g": rozdil_g,
                "skutecnost_pct": skutecnost_pct,
                "min_pct": min_pct,
                "max_pct": max_pct,
                "splneno": splneno,
                "stav": stav,
            }
        )

    return radky
