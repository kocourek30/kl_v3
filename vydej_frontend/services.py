from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from canteen_settings.models import MealPickupTime
from objednavky.models import Order, OrderItem
from vydej.models import PolozkaUctenky, VydejniUctenka


VYDEJ_STAVY = ["objednano", "zalozena-obsluhou", "castecne-vydano"]


def aktualni_druhy_jidel_ids():
    now = timezone.localtime(timezone.now()).time()
    return list(
        MealPickupTime.objects.filter(
            pickup_from__lte=now,
            pickup_to__gte=now,
        ).values_list("druh_jidla_id", flat=True)
    )


def _uctenka_pro_objednavku(order, user):
    uctenka, _ = VydejniUctenka.objects.select_for_update().get_or_create(
        order=order,
        defaults={
            "datum_vydeje": timezone.now(),
            "vydal": user,
            "celkova_cena": Decimal("0"),
            "celkova_dotace": Decimal("0"),
        },
    )
    if not uctenka.vydal_id and user:
        uctenka.vydal = user
    return uctenka


def _pridej_polozku_na_uctenku(uctenka, item):
    cena_za_kus = item.cena
    puvodni_cena = item.menu_item.jidlo.cena
    dotace_za_kus = puvodni_cena - cena_za_kus

    PolozkaUctenky.objects.create(
        uctenka=uctenka,
        nazev_jidla=item.menu_item.jidlo.nazev,
        druh_jidla=item.menu_item.druh_jidla.nazev,
        mnozstvi=item.quantity,
        cena_za_kus=cena_za_kus,
        dotace_za_kus=dotace_za_kus,
    )

    uctenka.celkova_cena += cena_za_kus * item.quantity
    uctenka.celkova_dotace += dotace_za_kus * item.quantity
    return f"{item.quantity}× {item.menu_item.jidlo.nazev}"


def _uzavri_vydane_polozky(order, uctenka, items, issued_at):
    vydane_polozky = []
    for item in items:
        vydane_polozky.append(_pridej_polozku_na_uctenku(uctenka, item))
        item.vydano = True
        item.datum_vydani = issued_at
        item.save(update_fields=["vydano", "datum_vydani"])

    if order.items.filter(vydano=False).exists():
        order.status = "castecne-vydano"
    else:
        order.status = "vydano"

    order.datum_vydani = issued_at
    order.save(update_fields=["status", "datum_vydani", "updated_at"])
    uctenka.save(update_fields=["vydal", "celkova_cena", "celkova_dotace"])
    return vydane_polozky


@transaction.atomic
def vydat_objednavku(order_id, user):
    order = (
        Order.objects
        .select_for_update()
        .select_related("user")
        .prefetch_related("items__menu_item__jidlo", "items__menu_item__druh_jidla")
        .get(pk=order_id)
    )

    if order.status not in VYDEJ_STAVY:
        raise ValidationError("Objednávka nemůže být vydána v aktuálním stavu.")

    current_meal_type_ids = aktualni_druhy_jidel_ids()
    if not current_meal_type_ids:
        raise ValidationError("Nyní není žádný výdejní čas.")

    items_to_issue = list(
        OrderItem.objects
        .select_for_update()
        .select_related("menu_item__jidlo", "menu_item__druh_jidla")
        .filter(
            order=order,
            vydano=False,
            menu_item__druh_jidla_id__in=current_meal_type_ids,
        )
    )
    if not items_to_issue:
        raise ValidationError("Žádné položky k vydání v aktuálním čase.")

    issued_at = timezone.now()
    uctenka = _uctenka_pro_objednavku(order, user)
    vydane_polozky = _uzavri_vydane_polozky(order, uctenka, items_to_issue, issued_at)

    return {
        "order": order,
        "uctenka": uctenka,
        "vydane_polozky": vydane_polozky,
        "partial": order.status == "castecne-vydano",
    }


@transaction.atomic
def vydat_polozku(item_id, user):
    item = (
        OrderItem.objects
        .select_for_update()
        .select_related("order__user", "menu_item__jidlo", "menu_item__druh_jidla")
        .get(pk=item_id)
    )
    order = Order.objects.select_for_update().get(pk=item.order_id)

    if order.status not in VYDEJ_STAVY:
        raise ValidationError("Objednávka nemůže být vydána v aktuálním stavu.")
    if item.vydano:
        raise ValidationError("Položka už byla vydána.")

    current_meal_type_ids = aktualni_druhy_jidel_ids()
    if not current_meal_type_ids:
        raise ValidationError("Nyní není žádný výdejní čas.")
    if item.menu_item.druh_jidla_id not in current_meal_type_ids:
        raise ValidationError("Tato položka není v aktuálním výdejním čase.")

    issued_at = timezone.now()
    uctenka = _uctenka_pro_objednavku(order, user)
    vydane_polozky = _uzavri_vydane_polozky(order, uctenka, [item], issued_at)

    return {
        "order": order,
        "item": item,
        "uctenka": uctenka,
        "vydane_polozky": vydane_polozky,
        "order_complete": order.status == "vydano",
    }
