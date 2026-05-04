from datetime import datetime, timedelta

from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone

from canteen_settings.models import MealPickupTime
from objednavky.models import Order, OrderItem
from vydej.models import VydejSettings, VydejniUctenka


PENDING_STATUSES = ["objednano", "zalozena-obsluhou", "castecne-vydano"]
CANCELLED_STATUSES = ["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"]


def _humanize_delta(delta):
    total_seconds = max(int(delta.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def _window_status(today, pickup, now):
    start_dt = timezone.make_aware(
        datetime.combine(today, pickup.pickup_from),
        timezone.get_current_timezone(),
    )
    end_dt = timezone.make_aware(
        datetime.combine(today, pickup.pickup_to),
        timezone.get_current_timezone(),
    )
    if start_dt <= now <= end_dt:
        return {
            "state": "active",
            "badge": "Právě běží",
            "countdown": f"končí za {_humanize_delta(end_dt - now)}",
        }
    if now < start_dt:
        return {
            "state": "upcoming",
            "badge": "Začíná brzy",
            "countdown": f"za {_humanize_delta(start_dt - now)}",
        }
    return {
        "state": "finished",
        "badge": "Proběhlo",
        "countdown": f"skončilo před {_humanize_delta(now - end_dt)}",
    }


def _format_user_label(user):
    if not user:
        return "Neznámý strávník"
    full_name = user.get_full_name().strip()
    return full_name or getattr(user, "username", "Bez jména")


def _build_kitchen_window(today):
    return {
        "label": "Přehled pro kuchyni",
        "description": "Samostatné okno s dnešní výrobou a čekajícími porcemi.",
        "icon": "fas fa-kitchen-set",
        "url": f"{reverse('admin:vydej_prehledprokuchyni_changelist')}?datum={today.isoformat()}",
    }


def _build_production_groups(pending_items_qs):
    grouped = list(
        pending_items_qs.values(
            "menu_item__druh_jidla",
            "menu_item__druh_jidla__nazev",
            "menu_item__druh_jidla__ikona",
            "menu_item__druh_jidla__poradi",
        )
        .annotate(
            portions=Sum("quantity"),
            order_count=Count("order", distinct=True),
            meal_count=Count("menu_item__jidlo", distinct=True),
        )
        .order_by("menu_item__druh_jidla__poradi", "menu_item__druh_jidla__nazev")
    )

    meal_names_by_type = {}
    for item in (
        pending_items_qs.values(
            "menu_item__druh_jidla",
            "menu_item__jidlo__nazev",
        )
        .annotate(portions=Sum("quantity"))
        .order_by("menu_item__druh_jidla", "-portions", "menu_item__jidlo__nazev")
    ):
        meal_names_by_type.setdefault(item["menu_item__druh_jidla"], [])
        if len(meal_names_by_type[item["menu_item__druh_jidla"]]) < 3:
            meal_names_by_type[item["menu_item__druh_jidla"]].append(item["menu_item__jidlo__nazev"])

    production_groups = []
    for group in grouped:
        meal_names = meal_names_by_type.get(group["menu_item__druh_jidla"], [])
        production_groups.append(
            {
                "id": group["menu_item__druh_jidla"],
                "label": group["menu_item__druh_jidla__nazev"] or "Bez druhu",
                "portions": group["portions"] or 0,
                "order_count": group["order_count"] or 0,
                "meal_count": group["meal_count"] or 0,
                "meal_names": meal_names,
            }
        )
    return production_groups


def _build_windows(today, now, pickup_times, counts_by_type):
    windows = []
    for pickup in pickup_times:
        counts = counts_by_type.get(pickup.druh_jidla_id, {"portions": 0, "order_count": 0})
        status = _window_status(today, pickup, now)
        windows.append(
            {
                "label": pickup.druh_jidla.nazev,
                "time_range": f"{pickup.pickup_from:%H:%M} - {pickup.pickup_to:%H:%M}",
                "queue_count": counts["portions"],
                "order_count": counts["order_count"],
                "state": status["state"],
                "badge": status["badge"],
                "countdown": status["countdown"],
            }
        )
    return windows


def _build_live_issue_feed(today):
    receipts = (
        VydejniUctenka.objects.filter(datum_vydeje__date=today)
        .select_related("order__user", "vydal")
        .prefetch_related("polozky")
        .order_by("-datum_vydeje")[:10]
    )

    feed = []
    for receipt in receipts:
        items = list(receipt.polozky.all())
        portions = sum(item.mnozstvi for item in items)
        item_names = [f"{item.nazev_jidla} ({item.mnozstvi}x)" for item in items[:2]]
        if len(items) > 2:
            item_names.append(f"+ {len(items) - 2} další")
        feed.append(
            {
                "time": timezone.localtime(receipt.datum_vydeje).strftime("%H:%M"),
                "user_label": _format_user_label(receipt.order.user),
                "operator_label": _format_user_label(receipt.vydal) if receipt.vydal else "Systém / terminál",
                "portions": portions,
                "items_summary": " • ".join(item_names) if item_names else "Bez položek",
                "amount": receipt.celkova_cena,
            }
        )
    return feed


def _build_notices(total_pending_portions, windows, current_issue_feed):
    notices = []
    if total_pending_portions == 0:
        notices.append(
            {
                "tone": "success",
                "title": "Dnešní fronta je čistá",
                "text": "Zatím nejsou žádné nevydané porce. Dashboard bude dál hlídat nové objednávky.",
            }
        )
    active_windows = [window for window in windows if window["state"] == "active"]
    if active_windows:
        current = active_windows[0]
        notices.append(
            {
                "tone": "warning",
                "title": f"Běží výdej: {current['label']}",
                "text": f"Aktuálně je potřeba odbavit {current['queue_count']} porcí. Okno {current['countdown']}.",
            }
        )
    upcoming = [window for window in windows if window["state"] == "upcoming"]
    if upcoming:
        next_window = upcoming[0]
        notices.append(
            {
                "tone": "neutral",
                "title": f"Další vlna: {next_window['label']}",
                "text": f"Začne {next_window['countdown']} a čeká v ní {next_window['queue_count']} porcí.",
            }
        )
    if not current_issue_feed:
        notices.append(
            {
                "tone": "neutral",
                "title": "Zatím nic nebylo vydáno",
                "text": "Jakmile obsluha začne vydávat, poslední výdeje se budou zobrazovat průběžně tady.",
            }
        )
    return notices[:3]


def build_canteen_staff_dashboard():
    today = timezone.localdate()
    now = timezone.localtime()
    settings_obj, _ = VydejSettings.objects.get_or_create()

    pending_orders_qs = Order.objects.filter(datum_vydeje=today, status__in=PENDING_STATUSES)
    pending_items_qs = (
        OrderItem.objects.filter(
            order__datum_vydeje=today,
            order__status__in=PENDING_STATUSES,
            vydano=False,
        )
        .select_related("order__user", "menu_item__jidlo", "menu_item__druh_jidla")
    )

    pending_orders_count = pending_orders_qs.count()
    pending_users_count = pending_orders_qs.values("user").distinct().count()
    total_pending_portions = pending_items_qs.aggregate(total=Sum("quantity"))["total"] or 0

    issued_receipts_qs = VydejniUctenka.objects.filter(datum_vydeje__date=today)
    issued_receipts_count = issued_receipts_qs.count()
    issued_portions = (
        issued_receipts_qs.values("polozky__id").aggregate(total=Sum("polozky__mnozstvi"))["total"] or 0
    )

    cancelled_today_count = Order.objects.filter(datum_vydeje=today, status__in=CANCELLED_STATUSES).count()

    production_groups = _build_production_groups(pending_items_qs)
    counts_by_type = {group["id"]: group for group in production_groups}

    pickup_times = MealPickupTime.objects.select_related("druh_jidla").order_by(
        "pickup_from",
        "druh_jidla__poradi",
        "druh_jidla__nazev",
    )
    windows = _build_windows(today, now, pickup_times, counts_by_type)
    current_issue_feed = _build_live_issue_feed(today)
    notices = _build_notices(total_pending_portions, windows, current_issue_feed)

    return {
        "today": today,
        "now": now,
        "last_updated": now,
        "settings_obj": settings_obj,
        "pending_orders_count": pending_orders_count,
        "pending_users_count": pending_users_count,
        "pending_portions": total_pending_portions,
        "issued_receipts_count": issued_receipts_count,
        "issued_portions": issued_portions,
        "cancelled_today_count": cancelled_today_count,
        "production_groups": production_groups,
        "meal_windows": windows,
        "current_issue_feed": current_issue_feed,
        "kitchen_window": _build_kitchen_window(today),
        "notices": notices,
    }
