from datetime import timedelta

from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from canteen_settings.models import MealPickupTime
from objednavky.models import Order, OrderItem
from vydej.models import StornovaneObjednavky, VydejSettings, VydejniUctenka


PENDING_STATUSES = ["objednano", "zalozena-obsluhou", "castecne-vydano"]
CANCELLED_STATUSES = ["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"]


def _meal_window_state(pickup, now_time):
    if pickup.pickup_from <= now_time <= pickup.pickup_to:
        return "active", "Probíhá právě teď"
    if now_time < pickup.pickup_from:
        return "upcoming", "Nadcházející výdej"
    return "past", "Už proběhlo"


def _build_recent_activity(today):
    recent_activity = []

    for receipt in (
        VydejniUctenka.objects.filter(datum_vydeje__date=today)
        .select_related("order__user", "vydal")
        .order_by("-datum_vydeje")[:6]
    ):
        recent_activity.append(
            {
                "tone": "success",
                "title": receipt.order.user.get_full_name() or receipt.order.user.username,
                "meta": receipt.datum_vydeje.strftime("%d.%m.%Y %H:%M"),
                "detail": f"Vydáno {receipt.polozky.aggregate(total=Sum('mnozstvi'))['total'] or 0} porcí",
                "sort_value": receipt.datum_vydeje,
            }
        )

    for storno in (
        StornovaneObjednavky.objects.filter(datum_vydeje=today, status__in=CANCELLED_STATUSES)
        .select_related("user", "storno_user")
        .order_by("-updated_at")[:4]
    ):
        recent_activity.append(
            {
                "tone": "danger" if storno.status == "nevyzvednuto" else "warning",
                "title": storno.user.get_full_name() or storno.user.username,
                "meta": storno.updated_at.strftime("%d.%m.%Y %H:%M"),
                "detail": storno.get_status_display(),
                "sort_value": storno.updated_at,
            }
        )

    recent_activity.sort(key=lambda item: item["sort_value"], reverse=True)
    return recent_activity[:8]


def _build_quick_links(today):
    return [
        {
            "label": "Živý výdej",
            "description": "Hlavní provozní obrazovka obsluhy pro vydávání jídel a RFID.",
            "url": reverse("vydej_frontend:dashboard"),
            "icon": "fas fa-concierge-bell",
            "tone": "primary",
            "new_window": True,
        },
        {
            "label": "Přehled pro kuchyni",
            "description": "Denní přehled výroby a výdeje pro kuchyň a koordinaci směny.",
            "url": f"{reverse('admin:vydej_prehledprokuchyni_changelist')}?datum={today.isoformat()}",
            "icon": "fas fa-kitchen-set",
            "tone": "light",
            "new_window": True,
        },
        {
            "label": "Fronta objednávek",
            "description": "Rozpracované objednávky připravené k výdeji nebo dovýdeji.",
            "url": reverse("admin:vydej_vydejorder_changelist"),
            "icon": "fas fa-list-check",
            "tone": "light",
            "new_window": True,
        },
        {
            "label": "Vydané účtenky",
            "description": "Dohledání již vydaných objednávek a kontrola účtenek.",
            "url": reverse("admin:vydej_vydejniuctenka_changelist"),
            "icon": "fas fa-receipt",
            "tone": "light",
            "new_window": True,
        },
        {
            "label": "Storna a nevyzvednuté",
            "description": "Problematické případy, které vyžadují zásah nebo kontrolu.",
            "url": reverse("admin:vydej_stornovaneobjednavky_changelist"),
            "icon": "fas fa-triangle-exclamation",
            "tone": "danger",
            "new_window": True,
        },
    ]


def _build_settings_links():
    return [
        {
            "label": "Výdejní časy jídel",
            "description": "Správa časových oken výdeje podle druhu jídla.",
            "url": reverse("admin:canteen_settings_mealpickuptime_changelist"),
            "new_window": True,
        },
        {
            "label": "Uzávěrky objednávek",
            "description": "Kontrola uzávěrek a pravidel pro objednávání a rušení jídel.",
            "url": reverse("admin:canteen_settings_orderclosingtime_changelist"),
            "new_window": True,
        },
        {
            "label": "Provozní výjimky",
            "description": "Mimořádné dny, výluky a výjimky v běžném provozu jídelny.",
            "url": reverse("admin:canteen_settings_operatingexceptions_changelist"),
            "new_window": True,
        },
        {
            "label": "Timeout výdeje",
            "description": "Za kolik sekund se nalezená objednávka automaticky dokončí.",
            "url": reverse("admin:provoz_jidelny_nastavenivydaje_changelist"),
            "new_window": False,
        },
    ]


def build_canteen_staff_dashboard():
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

    issued_receipts_qs = VydejniUctenka.objects.filter(datum_vydeje__date=today)
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
        MealPickupTime.objects.select_related("druh_jidla").order_by(
            "pickup_from",
            "druh_jidla__poradi",
            "druh_jidla__nazev",
        )
    )
    counts_by_type = {
        item["menu_item__druh_jidla"]: item["total"]
        for item in pending_items_qs.values("menu_item__druh_jidla").annotate(total=Sum("quantity"))
    }

    meal_windows = []
    for pickup in pickup_times:
        state, note = _meal_window_state(pickup, now_time)
        meal_windows.append(
            {
                "label": pickup.druh_jidla.nazev,
                "time_range": f"{pickup.pickup_from.strftime('%H:%M')}–{pickup.pickup_to.strftime('%H:%M')}",
                "queue_count": counts_by_type.get(pickup.druh_jidla_id, 0),
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
                "text": "Bez nich obsluha a kuchyň nevidí správný rytmus dne a provozní kontext.",
            }
        )
    elif active_windows:
        labels = ", ".join(window["label"] for window in active_windows)
        notices.append(
            {
                "tone": "success",
                "title": "Výdej právě běží",
                "text": f"Aktivní okna: {labels}. Obsluha může pracovat bez přepínání kontextu.",
            }
        )
    elif upcoming_windows:
        notices.append(
            {
                "tone": "warning",
                "title": "Další výdejní vlna teprve přijde",
                "text": f"Nejbližší okno začne v {upcoming_windows[0]['time_range']} pro {upcoming_windows[0]['label'].lower()}.",
            }
        )

    if stale_pending_count:
        notices.append(
            {
                "tone": "danger",
                "title": "Zůstaly starší rozpracované objednávky",
                "text": f"{stale_pending_count} objednávek má datum výdeje v minulosti a stále nejsou uzavřené.",
            }
        )

    if pending_portions >= 120:
        notices.append(
            {
                "tone": "warning",
                "title": "Silnější provoz",
                "text": f"Dnes čeká {pending_portions} porcí k výdeji. Doporučujeme mít otevřený i kuchyňský přehled.",
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
        "upcoming_windows": upcoming_windows[:3],
        "top_meals": top_meals,
        "notices": notices,
        "recent_activity": _build_recent_activity(today),
        "quick_links": _build_quick_links(today),
        "settings_links": _build_settings_links(),
        "stale_pending_count": stale_pending_count,
    }

