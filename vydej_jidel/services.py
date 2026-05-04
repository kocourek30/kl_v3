from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from canteen_settings.models import MealPickupTime
from objednavky.models import Order, OrderItem
from vydej.models import StornovaneObjednavky, VydejniUctenka

from .models import VydajiciCas, VydejSettings


PENDING_STATUSES = ["objednano", "zalozena-obsluhou", "castecne-vydano"]
CANCELLED_STATUSES = ["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"]


def _meal_window_state(pickup, now_time):
    if pickup.pickup_from <= now_time <= pickup.pickup_to:
        return "active", "Probíhá právě teď"
    if now_time < pickup.pickup_from:
        return "upcoming", "Nadcházející výdej"
    return "past", "Už proběhlo"


def build_canteen_admin_dashboard():
    today = timezone.localdate()
    now = timezone.localtime()
    now_time = now.time()

    settings_obj, _ = VydejSettings.objects.get_or_create()

    pending_orders_qs = Order.objects.filter(
        datum_vydeje=today,
        status__in=PENDING_STATUSES,
    ).select_related("user")

    pending_items_qs = OrderItem.objects.filter(
        order__datum_vydeje=today,
        order__status__in=PENDING_STATUSES,
        vydano=False,
    ).select_related("menu_item__jidlo", "menu_item__druh_jidla")

    pending_orders_count = pending_orders_qs.count()
    pending_users_count = pending_orders_qs.values("user_id").distinct().count()
    pending_portions = pending_items_qs.aggregate(total=Sum("quantity"))["total"] or 0

    issued_receipts_qs = VydejniUctenka.objects.filter(datum_vydeje__date=today).select_related("order__user", "vydal")
    issued_receipts_count = issued_receipts_qs.count()
    issued_portions = issued_receipts_qs.aggregate(total=Sum("polozky__mnozstvi"))["total"] or 0

    cancelled_today_count = Order.objects.filter(
        datum_vydeje=today,
        status__in=CANCELLED_STATUSES,
    ).count()

    stale_pending_count = Order.objects.filter(
        datum_vydeje__lt=today,
        status__in=PENDING_STATUSES,
    ).count()

    pickup_times = list(
        MealPickupTime.objects.select_related("druh_jidla").order_by("pickup_from", "druh_jidla__poradi", "druh_jidla__nazev")
    )
    counts_by_type = {
        item["menu_item__druh_jidla"]: item["total"]
        for item in pending_items_qs.values("menu_item__druh_jidla").annotate(total=Sum("quantity"))
    }

    meal_windows = []
    for pickup in pickup_times:
        state, note = _meal_window_state(pickup, now_time)
        queue_count = counts_by_type.get(pickup.druh_jidla_id, 0)
        meal_windows.append(
            {
                "label": pickup.druh_jidla.nazev,
                "time_range": f"{pickup.pickup_from.strftime('%H:%M')}–{pickup.pickup_to.strftime('%H:%M')}",
                "queue_count": queue_count,
                "state": state,
                "note": note,
            }
        )

    active_windows = [window for window in meal_windows if window["state"] == "active"]
    upcoming_windows = [window for window in meal_windows if window["state"] == "upcoming"]

    top_meals = list(
        pending_items_qs.values("menu_item__jidlo__nazev", "menu_item__druh_jidla__nazev")
        .annotate(total=Sum("quantity"))
        .order_by("-total", "menu_item__druh_jidla__nazev", "menu_item__jidlo__nazev")[:6]
    )

    notices = []
    if not pickup_times:
        notices.append(
            {
                "tone": "danger",
                "title": "Chybí výdejní časy jídel",
                "text": "Bez výdejních časů nebude mít obsluha ani kuchyň správný časový kontext pro provoz.",
            }
        )
    elif not active_windows and upcoming_windows:
        notices.append(
            {
                "tone": "warning",
                "title": "Aktuálně neběží žádné výdejní okno",
                "text": f"Nejbližší výdej začne v {upcoming_windows[0]['time_range']} pro {upcoming_windows[0]['label'].lower()}.",
            }
        )
    elif active_windows:
        labels = ", ".join(window["label"] for window in active_windows)
        notices.append(
            {
                "tone": "success",
                "title": "Výdej právě běží",
                "text": f"Aktivní okna: {labels}. Obsluha může vydávat bez přepínání kontextu.",
            }
        )

    if stale_pending_count:
        notices.append(
            {
                "tone": "danger",
                "title": "Zůstaly rozpracované starší objednávky",
                "text": f"{stale_pending_count} objednávek má datum výdeje v minulosti a stále nejsou uzavřené.",
            }
        )

    if pending_portions >= 120:
        notices.append(
            {
                "tone": "warning",
                "title": "Silný provoz",
                "text": f"Dnes čeká {pending_portions} porcí k výdeji. Doporučené je mít otevřený i kuchyňský přehled.",
            }
        )

    if not notices:
        notices.append(
            {
                "tone": "neutral",
                "title": "Provoz bez výstrah",
                "text": "Momentálně nejsou detekované žádné zásadní provozní odchylky.",
            }
        )

    recent_activity = []
    for receipt in issued_receipts_qs.order_by("-datum_vydeje")[:5]:
        recent_activity.append(
            {
                "tone": "success",
                "title": receipt.order.user.get_full_name() or receipt.order.user.username,
                "meta": receipt.datum_vydeje.strftime("%d.%m.%Y %H:%M"),
                "detail": f"Vydáno {receipt.polozky.aggregate(total=Sum('mnozstvi'))['total'] or 0} porcí",
            }
        )

    for order in Order.objects.filter(
        datum_vydeje=today,
        status__in=CANCELLED_STATUSES,
    ).select_related("user").order_by("-updated_at")[:3]:
        recent_activity.append(
            {
                "tone": "danger" if order.status == "nevyzvednuto" else "warning",
                "title": order.user.get_full_name() or order.user.username,
                "meta": order.updated_at.strftime("%d.%m.%Y %H:%M"),
                "detail": order.get_status_display(),
            }
        )

    recent_activity = sorted(recent_activity, key=lambda item: item["meta"], reverse=True)[:7]

    quick_links = [
        {
            "label": "Živý výdej",
            "description": "Hlavní obrazovka pro obsluhu při výdeji jídel a RFID.",
            "url": reverse("vydej_frontend:dashboard"),
            "icon": "fas fa-concierge-bell",
            "tone": "primary",
        },
        {
            "label": "Přehled pro kuchyni",
            "description": "Co je třeba připravit a dovydat pro dnešní výdej.",
            "url": f"{reverse('admin:vydej_prehledprokuchyni_changelist')}?datum={today.isoformat()}",
            "icon": "fas fa-kitchen-set",
            "tone": "light",
        },
        {
            "label": "Fronta objednávek",
            "description": "Rozpracované objednávky připravené k výdeji.",
            "url": reverse("admin:vydej_vydejorder_changelist"),
            "icon": "fas fa-list-check",
            "tone": "light",
        },
        {
            "label": "Vydané účtenky",
            "description": "Kontrola vydaných objednávek a dohledání účtenek.",
            "url": reverse("admin:vydej_vydejniuctenka_changelist"),
            "icon": "fas fa-receipt",
            "tone": "light",
        },
        {
            "label": "Storna a nevyzvednuté",
            "description": "Rychlý přístup na problematické a stornované objednávky.",
            "url": reverse("admin:vydej_stornovaneobjednavky_changelist"),
            "icon": "fas fa-triangle-exclamation",
            "tone": "danger",
        },
        {
            "label": "Admin přehled",
            "description": "Úlohy administrace, health-check a rychlé systémové akce.",
            "url": reverse("admin:admin_dashboard_dashboardtask_changelist"),
            "icon": "fas fa-screwdriver-wrench",
            "tone": "accent",
        },
    ]

    settings_links = [
        {
            "label": "Výdejní časy jídel",
            "description": "Nastavení časových oken pro jednotlivé druhy jídel.",
            "url": reverse("admin:vydej_jidel_vydajicicas_changelist"),
        },
        {
            "label": "Timeout výdeje",
            "description": "Za jak dlouho se objednávka při obsluze automaticky dokončí.",
            "url": reverse("admin:vydej_jidel_vydejsettings_changelist"),
        },
    ]

    return {
        "today": today,
        "now": now,
        "settings_obj": settings_obj,
        "pending_orders_count": pending_orders_count,
        "pending_users_count": pending_users_count,
        "pending_portions": pending_portions,
        "issued_receipts_count": issued_receipts_count,
        "issued_portions": issued_portions,
        "cancelled_today_count": cancelled_today_count,
        "active_window_count": len(active_windows),
        "meal_windows": meal_windows,
        "active_windows": active_windows,
        "upcoming_windows": upcoming_windows[:3],
        "top_meals": top_meals,
        "notices": notices,
        "recent_activity": recent_activity,
        "quick_links": quick_links,
        "settings_links": settings_links,
        "stale_pending_count": stale_pending_count,
    }
