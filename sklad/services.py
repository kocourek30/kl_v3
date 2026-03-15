# sklad/services.py
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from objednavky.models import OrderItem
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

# -------------------------------------------------------------------
# Spotřební koš – množství v gramech a tabulka plnění
# -------------------------------------------------------------------


def spocitej_spotrebu_sk_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Vrátí dict { 'MASO': Decimal(gramy), ... } za měsíc,
    přepočteno na g a zohledněno koeficientem SK.
    Počítá se z pohybů skladu typu VYDEJ.
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

        mnozstvi_g = s.mnozstvi_do_gramu(pohyb.mnozstvi or Decimal("0"))
        koef = s.koeficient_sk or Decimal("1")
        sk_gramy = mnozstvi_g * koef

        vysledky[s.skupina_sk] += sk_gramy

    return vysledky


def priprav_radky_spotrebi_kos_tabulka(
    rok: int,
    mesic: int,
    stravovaci_skupina,
    pocet_stravniku: int,
    date_from=None,
    date_to=None,
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

    - pokud je date_from/date_to, skutečnost se počítá z tohoto rozsahu
      přes OrderItem + receptury (jako v dashboardu),
    - jinak se použije původní měsíční výpočet přes PohybSkladu.
    """
    pocet = Decimal(pocet_stravniku or 0)

    # ------------------------------------------------------------
    # 1) SKUTEČNOST – dvě větve podle toho, zda máme date_from/date_to
    # ------------------------------------------------------------
    if date_from and date_to:
        # režim libovolného období – počítáme z objednávek
        qs_orders = OrderItem.objects.filter(
            order__datum_vydeje__gte=date_from,
            order__datum_vydeje__lte=date_to,
        ).select_related("menu_item__jidlo")

        if stravovaci_skupina is not None:
            qs_orders = qs_orders.filter(
                order__user__stravovaci_skupina=stravovaci_skupina
            )

        skutecnost = defaultdict(Decimal)

        qs_orders = qs_orders.prefetch_related(
            "menu_item__jidlo__receptura__surovina"
        )

        for item in qs_orders:
            jidlo = item.menu_item.jidlo
            pocet_porci = Decimal(item.quantity or 0)
            for pol in jidlo.receptura.all():
                surovina = pol.surovina
                if surovina.skupina_sk == "NONE":
                    continue

                mnozstvi = (pol.mnozstvi_na_porci or Decimal("0")) * pocet_porci
                # přepočet na gramy + koeficient SK
                mnozstvi_g = surovina.mnozstvi_do_gramu(mnozstvi)
                koef = surovina.koeficient_sk or Decimal("1")
                sk_gramy = mnozstvi_g * koef

                skutecnost[surovina.skupina_sk] += sk_gramy
    else:
        # původní měsíční režim přes pohyby skladu
        skutecnost = spocitej_spotrebu_sk_mesic(rok, mesic, stravovaci_skupina)

    # ------------------------------------------------------------
    # 2) NORMY + TOLERANCE
    # ------------------------------------------------------------
    normy_qs = NormaSpotrebnihoKose.objects.filter(
        stravovaci_skupina=stravovaci_skupina
    )
    normy_map = {n.skupina_sk: n.norma_g_mesic for n in normy_qs}

    toler_qs = ToleranceSpotrebnihoKose.objects.filter(
        stravovaci_skupina=stravovaci_skupina
    )
    toler_map = {t.skupina_sk: (t.min_pct, t.max_pct) for t in toler_qs}

    # ------------------------------------------------------------
    # 3) Sestavení řádků tabulky
    # ------------------------------------------------------------
    radky = []

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


# -------------------------------------------------------------------
# Počet porcí (strávníkoden) za měsíc
# -------------------------------------------------------------------


def spocitej_stravnikodny_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Celkový počet 'strávníkoden' za měsíc = součet porcí (OrderItem.quantity).
    """
    qs = OrderItem.objects.filter(
        order__datum_vydeje__year=rok,
        order__datum_vydeje__month=mesic,
    )

    if stravovaci_skupina is not None:
        qs = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)

    total = qs.aggregate(celkem=Sum("quantity"))["celkem"] or 0
    return Decimal(total)


# -------------------------------------------------------------------
# Náklady na suroviny – souhrn + podle skupin SK
# -------------------------------------------------------------------


def spocitej_naklady_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Vrátí dict s finančními údaji za měsíc:
    {
        "prijmy": Decimal,       # celková hodnota příjmů (nákupy)
        "vydeje": Decimal,       # celková hodnota výdejů (spotřeba)
        "bilance": Decimal,      # vydeje = náklady na suroviny
        "pocet_porci": Decimal,  # počet vydaných porcí (strávníkoden)
        "cena_na_porci": Decimal # náklady / porce (nebo 0)
    }
    """
    qs = PohybSkladu.objects.filter(
        datum__year=rok,
        datum__month=mesic,
    ).select_related("surovina", "vydejka")

    if stravovaci_skupina is not None:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    prijmy_sum = Decimal("0")
    vydeje_sum = Decimal("0")

    for pohyb in qs:
        if pohyb.cena_za_jednotku is None:
            continue

        celkem = (pohyb.mnozstvi or Decimal("0")) * (
            pohyb.cena_za_jednotku or Decimal("0")
        )

        if pohyb.typ == "PRIJEM":
            prijmy_sum += celkem
        elif pohyb.typ == "VYDEJ":
            vydeje_sum += celkem

    pocet_porci = spocitej_stravnikodny_mesic(rok, mesic, stravovaci_skupina)

    if pocet_porci > 0:
        cena_na_porci = vydeje_sum / pocet_porci
    else:
        cena_na_porci = Decimal("0")

    return {
        "prijmy": prijmy_sum,
        "vydeje": vydeje_sum,
        "bilance": vydeje_sum,
        "pocet_porci": pocet_porci,
        "cena_na_porci": cena_na_porci,
    }


def priprav_naklady_podle_skupin_sk(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Vrátí list řádků ve formátu:
    [
      {
        "skupina_kod": "MASO",
        "skupina_nazev": "maso",
        "naklady": Decimal (celkové náklady za výdeje v této SK skupině),
      },
      ...
    ]
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

    naklady_map = defaultdict(Decimal)

    for pohyb in qs:
        s = pohyb.surovina
        if s.skupina_sk == "NONE":
            continue

        if pohyb.cena_za_jednotku is None:
            continue

        celkem = (pohyb.mnozstvi or Decimal("0")) * (
            pohyb.cena_za_jednotku or Decimal("0")
        )
        naklady_map[s.skupina_sk] += celkem

    radky = []
    for kod, nazev in SKUPINY_SK_PORADI:
        radky.append(
            {
                "skupina_kod": kod,
                "skupina_nazev": nazev,
                "naklady": naklady_map.get(kod, Decimal("0")),
            }
        )

    return radky
