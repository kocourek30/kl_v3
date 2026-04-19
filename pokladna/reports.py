from decimal import Decimal

from django.db.models import Count, Sum

from .models import PLUPolozka, PokladniDoklad, PokladniPolozka


def doklady_za_obdobi(pokladna, datum_od, datum_do):
    return PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav=PokladniDoklad.STAV_UZAVRENO,
        datum__date__gte=datum_od,
        datum__date__lte=datum_do,
    )


def trzby_podle_plateb(doklady):
    radky = []
    for kod, nazev in PokladniDoklad.ZPUSOBY_PLATBY:
        qs = doklady.filter(zpusob_platby=kod)
        radky.append({
            "kod": kod,
            "nazev": nazev,
            "castka": qs.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0"),
            "pocet": qs.count(),
        })
    return radky


def dph_souhrn(doklady):
    return (
        PokladniPolozka.objects
        .filter(doklad__in=doklady)
        .values("dph_sazba")
        .annotate(
            zaklad=Sum("zaklad_dph"),
            dph=Sum("castka_dph"),
            celkem=Sum("castka_celkem"),
        )
        .order_by("dph_sazba")
    )


def trzby_podle_druhu(doklady):
    typy_plu = dict(PLUPolozka.TYPY)
    radky = []
    qs = (
        PokladniPolozka.objects
        .filter(doklad__in=doklady)
        .values("plu__typ")
        .annotate(
            mnozstvi=Sum("mnozstvi"),
            zaklad=Sum("zaklad_dph"),
            dph=Sum("castka_dph"),
            celkem=Sum("castka_celkem"),
            pocet_radku=Count("id"),
        )
        .order_by("plu__typ")
    )
    for radek in qs:
        radky.append({
            **radek,
            "nazev": typy_plu.get(radek["plu__typ"], radek["plu__typ"] or "Bez druhu"),
        })
    return radky


def plu_obraty(doklady):
    return (
        PokladniPolozka.objects
        .filter(doklad__in=doklady)
        .values("plu_id", "nazev_snapshot")
        .annotate(
            mnozstvi=Sum("mnozstvi"),
            obrat=Sum("castka_celkem"),
            pocet_radku=Count("id"),
        )
        .order_by("-obrat", "nazev_snapshot")
    )
