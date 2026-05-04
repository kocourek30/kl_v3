import calendar
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum

from dotace.models import Dotace
from objednavky.models import OrderItem
from users.group_utils import get_primary_effective_group

from .models import FakturacniDavka, FakturacniNastaveni, FakturacniPolozka


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
ORDER_TOTAL = ExpressionWrapper(F("quantity") * F("cena"), output_field=MONEY_FIELD)


def money(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def mesicni_obdobi(rok, mesic):
    posledni_den = calendar.monthrange(int(rok), int(mesic))[1]
    from datetime import date

    return date(int(rok), int(mesic), 1), date(int(rok), int(mesic), posledni_den)


def _full_name(user):
    return user.get_full_name() or user.username


def _group_name(user):
    if getattr(user, "stravovaci_skupina_id", None):
        return str(user.stravovaci_skupina)
    group = get_primary_effective_group(user)
    return group.name if group else "Bez skupiny"


def spocitej_fakturacni_polozky(rok, mesic, nastaveni=None):
    nastaveni = nastaveni or FakturacniNastaveni.get_solo()
    datum_od, datum_do = mesicni_obdobi(rok, mesic)
    rows = []

    if nastaveni.fakturovat_dotace:
        dotace_qs = (
            Dotace.objects
            .filter(datum__gte=datum_od, datum__lte=datum_do)
            .select_related("uzivatel__stravovaci_skupina", "politika__skupina")
        )
        for user_id in dotace_qs.values_list("uzivatel_id", flat=True).distinct():
            user_dotace = dotace_qs.filter(uzivatel_id=user_id)
            user = user_dotace.first().uzivatel
            castka = money(user_dotace.aggregate(total=Sum("castka"))["total"])
            if not castka:
                continue
            rows.append({
                "typ": FakturacniPolozka.TYP_DOTACE,
                "uzivatel": user,
                "username_snapshot": user.username,
                "jmeno_snapshot": _full_name(user),
                "osobni_cislo_snapshot": user.osobni_cislo or "",
                "skupina_snapshot": _group_name(user),
                "pocet_porci": Decimal(user_dotace.count()),
                "castka": castka,
                "detail": "Připsané dotace na konto strávníka.",
            })

    zamestnanecke_skupiny = list(nastaveni.zamestnanecke_skupiny.all())
    if zamestnanecke_skupiny:
        User = get_user_model()
        zamestnanci = User.objects.filter(groups__in=zamestnanecke_skupiny).distinct()
        stav_filter = Q(order__status="vydano")
        if nastaveni.zahrnout_nevyzvednute:
            stav_filter |= Q(order__status="nevyzvednuto")
        order_items = (
            OrderItem.objects
            .filter(
                order__user__in=zamestnanci,
                order__datum_vydeje__gte=datum_od,
                order__datum_vydeje__lte=datum_do,
            )
            .filter(Q(vydano=True) | stav_filter)
            .exclude(order__status__in=["zruseno-uzivatelem", "zruseno-obsluhou"])
            .select_related("order__user__stravovaci_skupina")
        )
        for user_id in order_items.values_list("order__user_id", flat=True).distinct():
            user_items = order_items.filter(order__user_id=user_id)
            first_item = user_items.first()
            user = first_item.order.user
            castka = money(user_items.aggregate(total=Sum(ORDER_TOTAL))["total"])
            porci = user_items.aggregate(total=Sum("quantity"))["total"] or 0
            if not castka:
                continue
            rows.append({
                "typ": FakturacniPolozka.TYP_SRAZKA,
                "uzivatel": user,
                "username_snapshot": user.username,
                "jmeno_snapshot": _full_name(user),
                "osobni_cislo_snapshot": user.osobni_cislo or "",
                "skupina_snapshot": _group_name(user),
                "pocet_porci": Decimal(str(porci)),
                "castka": castka,
                "detail": "Objednávky zaměstnance k měsíční srážce ze mzdy.",
            })

    return datum_od, datum_do, rows


@transaction.atomic
def vytvor_nebo_prepocitej_davku(rok, mesic, user=None):
    datum_od, datum_do, rows = spocitej_fakturacni_polozky(rok, mesic)
    davka, _ = FakturacniDavka.objects.select_for_update().get_or_create(
        rok=rok,
        mesic=mesic,
        defaults={
            "datum_od": datum_od,
            "datum_do": datum_do,
            "vytvoril": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    if davka.stav == FakturacniDavka.STAV_UZAVRENO:
        raise ValueError("Uzavřenou fakturační dávku nelze přepočítat.")

    davka.datum_od = datum_od
    davka.datum_do = datum_do
    if user and getattr(user, "is_authenticated", False) and not davka.vytvoril_id:
        davka.vytvoril = user
    davka.polozky.all().delete()

    polozky = [FakturacniPolozka(davka=davka, **row) for row in rows]
    FakturacniPolozka.objects.bulk_create(polozky)

    dotace = sum((row["castka"] for row in rows if row["typ"] == FakturacniPolozka.TYP_DOTACE), Decimal("0"))
    srazky = sum((row["castka"] for row in rows if row["typ"] == FakturacniPolozka.TYP_SRAZKA), Decimal("0"))
    davka.dotace_celkem = money(dotace)
    davka.srazky_celkem = money(srazky)
    davka.polozek = len(rows)
    davka.save(update_fields=[
        "datum_od",
        "datum_do",
        "vytvoril",
        "dotace_celkem",
        "srazky_celkem",
        "polozek",
    ])
    return davka
