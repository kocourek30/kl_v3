from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from canteen_settings.models import OrderClosingTime
from objednavky.models import Order, OrderItem

from .models import PolozkaUctenky, VydejniUctenka


ISSUEABLE_ORDER_STATUSES = {"objednano", "zalozena-obsluhou", "castecne-vydano"}
CANCELLED_ORDER_STATUSES = {"zruseno-uzivatelem", "zruseno-obsluhou"}


def _receipt_for_order(order, issued_by):
    receipt, _ = VydejniUctenka.objects.select_for_update().get_or_create(
        order=order,
        defaults={
            "datum_vydeje": timezone.now(),
            "vydal": issued_by,
            "celkova_cena": Decimal("0.00"),
            "celkova_dotace": Decimal("0.00"),
        },
    )
    if issued_by and not receipt.vydal_id:
        receipt.vydal = issued_by
    return receipt


def _add_receipt_item(receipt, item):
    original_price = Decimal(str(item.menu_item.jidlo.cena or 0))
    unit_price = Decimal(str(item.cena or 0))
    subsidy_per_item = max(Decimal("0.00"), original_price - unit_price)

    PolozkaUctenky.objects.create(
        uctenka=receipt,
        nazev_jidla=item.menu_item.jidlo.nazev,
        druh_jidla=item.menu_item.druh_jidla.nazev,
        mnozstvi=item.quantity,
        cena_za_kus=unit_price,
        dotace_za_kus=subsidy_per_item,
    )

    receipt.celkova_cena += unit_price * item.quantity
    receipt.celkova_dotace += subsidy_per_item * item.quantity


@transaction.atomic
def issue_order_from_admin(order_id, issued_by):
    order = (
        Order.objects.select_for_update()
        .select_related("user")
        .prefetch_related("items__menu_item__jidlo", "items__menu_item__druh_jidla")
        .get(pk=order_id)
    )

    if order.status not in ISSUEABLE_ORDER_STATUSES:
        raise ValidationError("Objednávka nemůže být vydána v aktuálním stavu.")

    pending_items = list(
        OrderItem.objects.select_for_update()
        .select_related("menu_item__jidlo", "menu_item__druh_jidla")
        .filter(order=order, vydano=False)
    )
    if not pending_items:
        raise ValidationError("Objednávka už nemá žádné nevydané položky.")

    issued_at = timezone.now()
    was_partial = order.status == "castecne-vydano"
    receipt = _receipt_for_order(order, issued_by)
    issued_labels = []

    for item in pending_items:
        _add_receipt_item(receipt, item)
        item.vydano = True
        item.datum_vydani = issued_at
        item.save(update_fields=["vydano", "datum_vydani"])
        issued_labels.append(f"{item.quantity}× {item.menu_item.jidlo.nazev}")

    order.status = "vydano"
    order.datum_vydani = issued_at
    order.save(update_fields=["status", "datum_vydani", "updated_at"])
    receipt.datum_vydeje = issued_at
    receipt.save(update_fields=["datum_vydeje", "vydal", "celkova_cena", "celkova_dotace"])

    return {
        "order": order,
        "receipt": receipt,
        "issued_labels": issued_labels,
        "already_partial": was_partial,
    }


@transaction.atomic
def cancel_receipt_and_order(receipt_id, cancelled_by):
    receipt = (
        VydejniUctenka.objects.select_for_update()
        .select_related("order__user")
        .prefetch_related("order__items", "polozky")
        .get(pk=receipt_id)
    )
    order = Order.objects.select_for_update().get(pk=receipt.order_id)

    order.status = "zruseno-obsluhou"
    order.storno_user = cancelled_by
    order.storno_datum = timezone.now()
    order.datum_vydani = None
    order.save(update_fields=["status", "storno_user", "storno_datum", "datum_vydani", "updated_at"])

    order.items.update(vydano=False, datum_vydani=None)
    receipt.polozky.all().delete()
    receipt.delete()
    return order


def get_order_closing_info(target_date):
    try:
        settings = OrderClosingTime.objects.first()
        if not settings:
            return {"uzavreno": False, "uzavreno_text": "Neznámá uzávěrka", "cas_do_uzavirky": None}

        closing_date = target_date - timedelta(days=settings.advance_days)
        closing_dt = timezone.datetime.combine(closing_date, settings.closing_time)
        closing_dt = timezone.make_aware(closing_dt, timezone.get_current_timezone())
        now = timezone.now()

        if now >= closing_dt:
            return {"uzavreno": True, "uzavreno_text": "Uzavřeno", "cas_do_uzavirky": None}

        delta = closing_dt - now
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        if days > 0:
            countdown = f"{days}d {hours}h"
        elif hours > 0:
            countdown = f"{hours}h {minutes}m"
        else:
            countdown = f"{minutes}m"

        return {
            "uzavreno": False,
            "uzavreno_text": f"Uzavře za {countdown}",
            "cas_do_uzavirky": countdown,
        }
    except Exception:
        return {"uzavreno": False, "uzavreno_text": "Neznámá uzávěrka", "cas_do_uzavirky": None}


def build_kitchen_overview(target_date):
    orders = (
        Order.objects.filter(
            datum_vydeje=target_date,
            status__in=["objednano", "zalozena-obsluhou", "castecne-vydano"],
        )
        .select_related("user")
        .prefetch_related("items__menu_item__jidlo", "items__menu_item__druh_jidla")
    )

    stats = OrderedDict()
    total_orders = 0
    total_portions = 0

    sorted_orders = sorted(orders, key=lambda order: ((order.user.last_name or ""), (order.user.first_name or ""), order.id))
    for order in sorted_orders:
        total_orders += 1
        for item in order.items.all():
            if item.vydano:
                continue

            food_type_name = item.menu_item.druh_jidla.nazev
            meal_name = item.menu_item.jidlo.nazev
            stats.setdefault(food_type_name, OrderedDict())
            stats[food_type_name].setdefault(meal_name, {"celkem": 0, "uzivatele": []})

            stats[food_type_name][meal_name]["celkem"] += item.quantity
            stats[food_type_name][meal_name]["uzivatele"].append(
                {
                    "jmeno": order.user.get_full_name() or order.user.username,
                    "mnozstvi": item.quantity,
                    "order_id": order.id,
                    "status": order.get_status_display(),
                }
            )
            total_portions += item.quantity

    return {
        "stats": stats,
        "total_objednavek": total_orders,
        "total_porci": total_portions,
        "uzavirka_info": get_order_closing_info(target_date),
    }


def build_issue_board(start_date=None, days_ahead=7):
    start_date = start_date or date.today()
    end_date = start_date + timedelta(days=max(0, days_ahead - 1))

    orders = (
        Order.objects.filter(
            datum_vydeje__gte=start_date,
            datum_vydeje__lte=end_date,
            status__in=ISSUEABLE_ORDER_STATUSES,
        )
        .select_related("user")
        .prefetch_related("items__menu_item__jidlo", "items__menu_item__druh_jidla")
        .order_by("datum_vydeje", "user__last_name", "user__first_name", "id")
    )

    by_day = OrderedDict()
    for offset in range(days_ahead):
        current_day = start_date + timedelta(days=offset)
        by_day[current_day] = {
            "datum": current_day,
            "datum_formatted": current_day.strftime("%d.%m.%Y"),
            "druhy": OrderedDict(),
            "total_objednavek": 0,
            "total_kusu": 0,
            "uzavirka_info": get_order_closing_info(current_day),
        }

    for order in orders:
        day_bucket = by_day.setdefault(
            order.datum_vydeje,
            {
                "datum": order.datum_vydeje,
                "datum_formatted": order.datum_vydeje.strftime("%d.%m.%Y"),
                "druhy": OrderedDict(),
                "total_objednavek": 0,
                "total_kusu": 0,
                "uzavirka_info": get_order_closing_info(order.datum_vydeje),
            },
        )
        day_bucket["total_objednavek"] += 1

        for item in order.items.all():
            if item.vydano:
                continue

            meal_type = item.menu_item.druh_jidla.nazev
            meal_name = item.menu_item.jidlo.nazev
            day_bucket["druhy"].setdefault(meal_type, OrderedDict())
            meal_bucket = day_bucket["druhy"][meal_type].setdefault(
                meal_name,
                {"celkem": 0, "uzivatele": []},
            )
            meal_bucket["celkem"] += item.quantity
            meal_bucket["uzivatele"].append(
                {
                    "jmeno": order.user.get_full_name() or order.user.username,
                    "mnozstvi": item.quantity,
                    "order_id": order.id,
                }
            )
            day_bucket["total_kusu"] += item.quantity

    non_empty_days = [data for data in by_day.values() if data["total_objednavek"] > 0]
    return {
        "stats_vsechny_dny": list(by_day.values()),
        "neprazdne_dny": non_empty_days,
        "total_dni_s_vydejem": len(non_empty_days),
        "total_objednavek": sum(day["total_objednavek"] for day in by_day.values()),
        "total_kusu": sum(day["total_kusu"] for day in by_day.values()),
    }
