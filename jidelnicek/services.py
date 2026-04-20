from datetime import datetime, timedelta, date, time
from calendar import Calendar
import logging
from urllib.parse import urlencode

from django.utils import timezone
from django.urls import reverse
from django.db import models, transaction
from django.db.models import Sum

# MODELY
from canteen_settings.models import GroupOrderLimit, OrderClosingTime
from dotace.models import DotaceProJidelniskouSkupinu, DotacniPolitika, SkupinoveNastaveni
from objednavky.models import Order, OrderItem
from jidelnicek.models import Jidelnicek, PolozkaJidelnicku
from canteen_settings.utils import is_ordering_allowed, get_order_closing_datetime

from sklad.utils import odeber_ze_skladu_pro_jidlo


logger = logging.getLogger(__name__)



@transaction.atomic
def mark_order_as_issued(order: Order):
    """
    Označí objednávku / položky jako vydané a odečte suroviny ze skladu.
    Volat při skutečném výdeji (u vás ve vydej_frontend).
    """
    if order.status in ("vydano", "nevyzvednuto"):
        return  # už kompletně řešená objednávka

    now = timezone.now()

    for item in order.items.select_related("menu_item__jidlo").all():
        if item.vydano:
            continue

        jidlo = item.menu_item.jidlo
        pocet = item.quantity

        ok, _ = odeber_ze_skladu_pro_jidlo(jidlo, pocet)
        if not ok:
            raise ValueError(f"Nedostatek surovin pro {jidlo.nazev}")

        item.vydano = True
        item.datum_vydani = now
        item.save(update_fields=["vydano", "datum_vydani"])

    order.status = "vydano"
    order.datum_vydani = now
    order.save(update_fields=["status", "datum_vydani"])


@transaction.atomic
def mark_order_as_not_picked(order: Order):
    """
    Označí objednávku jako nevyzvednutou BEZ dalšího odečtu ze skladu.
    Použij, pokud už byla vydaná / připravená a chceš pouze přepnout stav.
    """
    if order.status == "nevyzvednuto":
        return

    if not order.items.filter(vydano=True).exists():
        # případně můžeš řešit jinak
        pass

    order.status = "nevyzvednuto"
    if not order.datum_vydani:
        order.datum_vydani = timezone.now()
    order.save(update_fields=["status", "datum_vydani"])


# ✅ NAHRAĎ FUNKCI can_order_for_date
def can_order_for_date(user=None, target_date=None):
    """Kontroluje, zda lze objednávat na dané datum podle nastavení uzavírací doby"""
    if user and getattr(user, "is_staff", False):
        return True, ""

    if not target_date:
        return True, ""

    try:
        if not is_ordering_allowed(target_date):
            closing_datetime = get_order_closing_datetime(target_date)
            if closing_datetime:
                msg = (
                    f"Uzávěrka objednávek na {target_date.strftime('%d.%m.%Y')} "
                    f"byla {closing_datetime.strftime('%d.%m.%Y v %H:%M')}"
                )
            else:
                msg = (
                    f"Objednávky na {target_date.strftime('%d.%m.%Y')} nejsou povoleny"
                )
            return False, msg

        return True, ""

    except Exception:
        logger.exception("Chyba při kontrole možnosti objednání.")
        return True, ""


def check_group_limit(user, menu_item, target_date, quantity):
    """Kontroluje limit objednávek podle skupiny a druhu jídla"""
    if user.is_staff:
        return True, ""

    user_group = user.groups.first()
    if not user_group:
        return True, ""

    limit_setting = GroupOrderLimit.objects.filter(
        group=user_group,
        druh_jidla=menu_item.druh_jidla,
    ).first()

    if not limit_setting or limit_setting.max_orders_per_day == 0:
        return True, ""

    current_orders = (
        OrderItem.objects.filter(
            order__user=user,
            order__datum_vydeje=target_date,
            menu_item__druh_jidla=menu_item.druh_jidla,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )

    if current_orders + quantity > limit_setting.max_orders_per_day:
        return False, (
            f"Limit {limit_setting.max_orders_per_day} ks "
            f"{menu_item.druh_jidla.nazev} za den pro skupinu {user_group.name}!"
        )
    return True, ""


def get_effective_closing_time(target_date):
    """Vrátí uzávěrkový čas pro objednávky na target_date"""
    try:
        closing_setting = OrderClosingTime.objects.filter(je_aktivni=True).first()
        if closing_setting:
            advance_days = getattr(closing_setting, "advance_days", 1)
            closing_time_obj = getattr(
                closing_setting, "closing_time", time(17, 0, 0)
            )
        else:
            advance_days = 1
            closing_time_obj = time(17, 0, 0)

        closing_date = target_date - timedelta(days=advance_days)
        closing_datetime = timezone.make_aware(
            datetime.combine(closing_date, closing_time_obj),
            timezone.get_current_timezone(),
        )
        return closing_datetime
    except Exception:
        closing_date = target_date - timedelta(days=1)
        return timezone.make_aware(
            datetime.combine(closing_date, time(17, 0, 0)),
            timezone.get_current_timezone(),
        )


def get_user_order_items(user):
    """Vrátí VŠECHNY BUDOUCÍ objednané položky uživatele (včetně dneška)
    S informací, zda lze zrušit podle nových pravidel."""
    from datetime import date as date_class
    from objednavky.views import can_cancel_order_for_menuitem_date

    items = (
        OrderItem.objects.filter(
            order__user=user,
            order__datum_vydeje__gte=date_class.today(),
            order__status__in=["zalozena-obsluhou", "objednano"],
        )
        .select_related("order", "menu_item__jidlo", "menu_item__druh_jidla")
        .order_by("order__datum_vydeje", "menu_item__id")
    )

    items_list = []
    for item in items:
        target_date = item.order.datum_vydeje
        menu_item = item.menu_item

        can_cancel, _ = can_cancel_order_for_menuitem_date(user, menu_item, target_date)

        item.can_cancel = can_cancel
        item.is_closed = not can_cancel
        item.total_price = item.quantity * item.cena
        item.target_date = target_date

        items_list.append(item)

    return items_list


def get_user_price_for_item(user, item):
    try:
        base_price = getattr(item.jidlo, "cena", 0)
        if base_price == 0:
            return 0

        dotacni_politika = None
        for group in user.groups.all():
            try:
                dotacni_politika = group.dotacni_politika
                break
            except DotacniPolitika.DoesNotExist:
                continue

        if not dotacni_politika:
            return base_price

        specific_dotace = DotaceProJidelniskouSkupinu.objects.filter(
            dotacni_politika=dotacni_politika,
            jidelniskova_skupina=item.druh_jidla,
        ).first()

        if specific_dotace:
            procento = specific_dotace.procento or dotacni_politika.procento
            castka = specific_dotace.castka or dotacni_politika.castka
        else:
            procento = dotacni_politika.procento
            castka = dotacni_politika.castka

        if procento > 0:
            sleva = base_price * (procento / 100)
            final_price = max(0, base_price - sleva)
        elif castka > 0:
            final_price = max(0, base_price - castka)
        else:
            final_price = base_price

        return round(final_price, 2)
    except Exception:
        return getattr(item.jidlo, "cena", 0)


def check_user_balance_for_item(user, item_price):
    """Kontroluje zůstatek uživatele pro objednávku položky"""
    try:
        zustatek = user.aktualni_zustatek or 0
        zustatek = float(zustatek)
        item_price = float(item_price)
        predikce_zustatek = zustatek - item_price

        if predikce_zustatek < 0:
            nastaveni = None
            for group in user.groups.all():
                try:
                    nastaveni = group.nastaveni
                    break
                except SkupinoveNastaveni.DoesNotExist:
                    continue

            if nastaveni and nastaveni.nutnost_dobit and zustatek < item_price:
                error_info = {
                    "type": "insufficient_balance",
                    "required": item_price,
                    "current": zustatek,
                    "message": "Nedostatečný zůstatek",
                }
                return False, error_info

            if nastaveni and nastaveni.cerpani_debit:
                debit_limit = float(nastaveni.debit_limit or 0)
                if predikce_zustatek < debit_limit:
                    error_info = {
                        "type": "predicted_debit_limit",
                        "required": debit_limit,
                        "current": predikce_zustatek,
                        "predicted": True,
                        "message": "Objednávka by překročila debetní limit",
                    }
                    return False, error_info

            if nastaveni and zustatek < float(getattr(nastaveni, "debit_limit", 0)):
                debit_limit = float(nastaveni.debit_limit or 0)
                error_info = {
                    "type": "debit_limit",
                    "required": debit_limit,
                    "current": zustatek,
                    "message": "Překročen debetní limit",
                }
                return False, error_info

            if not nastaveni and zustatek < item_price:
                error_info = {
                    "type": "insufficient_balance",
                    "required": item_price,
                    "current": zustatek,
                    "message": "Nedostatečný zůstatek",
                }
                return False, error_info

        return True, None
    except Exception:
        return True, None


def validate_item_for_display(user, item, target_date):
    """
    Validuje položku pro zobrazení (stavy, limity, ceny) - S current_order_item_id
    + nastavuje hide_quantity podle GroupOrderLimit
    """
    # ZÁKLADNÍ DEFAULTY
    item.order_status = "none"
    item.can_order = True
    item.can_cancel = False  # výchozí: nejde rušit
    item.validation_error = None
    item.balance_info = None
    item.current_quantity = 0
    item.current_order_item_id = None
    item.max_order_quantity = 10
    item.closing_info = ""
    item.display_price = get_user_price_for_item(user, item)
    item.hide_quantity = False

    # ✅ SKRYTÍ MNOŽSTVÍ PODLE GROUP LIMITU
    user_group = user.groups.first()
    if user_group:
        limit_setting = GroupOrderLimit.objects.filter(
            group=user_group,
            druh_jidla=item.druh_jidla,
        ).first()

        if limit_setting:
            if limit_setting.max_orders_per_day == 1:
                item.hide_quantity = True
                item.max_order_quantity = 1
            elif limit_setting.max_orders_per_day > 1:
                item.hide_quantity = False
                item.max_order_quantity = limit_setting.max_orders_per_day

    # ✅ ČASOVÉ PRAVIDLO PRO OBJEDNÁNÍ/ZMĚNU – PER POLOŽKA
    from objednavky.views import (
        can_order_for_menuitem_date,
        can_cancel_order_for_menuitem_date,
    )

    can_order_time, time_msg = can_order_for_menuitem_date(user, item, target_date)
    can_cancel_time, cancel_msg = can_cancel_order_for_menuitem_date(
        user, item, target_date
    )

    is_closed_for_order = not can_order_time

    if is_closed_for_order:
        closing_datetime = get_order_closing_datetime(target_date)
        if closing_datetime:
            item.closing_info = f"Uzavřeno {closing_datetime.strftime('%d.%m. %H:%M')}"
        else:
            item.closing_info = "Objednávky uzavřeny"

    # ✅ NAJDI OBJEDNÁVKU UŽIVATELE
    try:
        user_order = OrderItem.objects.get(
            menu_item=item,
            order__user=user,
            order__datum_vydeje=target_date,
        )
        item.order_status = "ordered"
        item.current_quantity = user_order.quantity
        item.current_order_item_id = user_order.id

        item.can_order = can_order_time
        item.can_cancel = can_cancel_time

    except OrderItem.DoesNotExist:
        if is_closed_for_order:
            item.order_status = "closed"
            item.validation_error = "order_closed"
            item.can_order = False
            item.can_cancel = False
        else:
            item.order_status = "active"
            can_order_balance, balance_info = check_user_balance_for_item(
                user, item.display_price
            )
            if not can_order_balance:
                item.balance_info = balance_info
                item.can_order = False
                item.validation_error = balance_info["type"]
            else:
                item.can_order = True
                item.can_cancel = False

    # ✅ GROUP LIMIT (jen pokud může objednávat)
    if item.order_status in ["active", "ordered"] and item.can_order:
        quantity_check, limit_error = check_group_limit(user, item, target_date, 1)
        if not quantity_check:
            item.can_order = False
            item.validation_error = "group_limit"
            group_limit = get_group_order_limit(user, item.druh_jidla)
            if group_limit > 0:
                item.max_order_quantity = group_limit - item.current_quantity


def get_group_order_limit(user, druh_jidla):
    """Vrátí maximální počet objednávek pro skupinu a druh jídla"""
    try:
        user_group = user.groups.first()
        if not user_group:
            return 0

        limit_obj = GroupOrderLimit.objects.filter(
            group=user_group,
            druh_jidla=druh_jidla,
        ).first()
        return limit_obj.max_orders_per_day if limit_obj else 0
    except Exception:
        return 0


# ✅ Pomocná funkce: filtrování položek podle skupiny uživatele
def _filter_items_for_user_group(user, items_qs):
    group = user.groups.first()
    if not group:
        # uživatel bez skupiny → vidí jen druhy bez omezení
        return items_qs.filter(
            models.Q(druh_jidla__viditelne_pro_skupiny__isnull=True)
        ).distinct()

    return items_qs.filter(
        models.Q(druh_jidla__viditelne_pro_skupiny=group)
        | models.Q(druh_jidla__viditelne_pro_skupiny__isnull=True)
    ).distinct()


def build_calendar_context(selected_date):
    """Vrátí data pro kalendář (dny, dny s jídelníčkem, navigace)"""
    first_day_month = selected_date.replace(day=1)
    cal = Calendar(firstweekday=0)
    calendar_weeks = list(
        cal.monthdatescalendar(selected_date.year, selected_date.month)
    )

    if first_day_month.month == 12:
        last_day_month = date(first_day_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day_month = first_day_month.replace(
            month=first_day_month.month + 1, day=1
        ) - timedelta(days=1)

    days_with_menu = set()
    jidelnicka_mesic = Jidelnicek.objects.filter(
        platnost_od__lte=last_day_month,
        platnost_do__gte=first_day_month,
    )

    for j in jidelnicka_mesic:
        overlap_start = max(j.platnost_od, first_day_month)
        overlap_end = min(j.platnost_do, last_day_month)

        current = overlap_start
        while current <= overlap_end:
            days_with_menu.add(current)
            current += timedelta(days=1)

    prev_month_date = first_day_month.replace(
        month=first_day_month.month - 1 if first_day_month.month > 1 else 12,
        year=first_day_month.year - 1 if first_day_month.month == 1 else first_day_month.year,
    )
    next_month_date = first_day_month.replace(
        month=first_day_month.month + 1 if first_day_month.month < 12 else 1,
        year=first_day_month.year + 1 if first_day_month.month == 12 else first_day_month.year,
    )

    return {
        "current_month": selected_date,
        "prev_month": prev_month_date,
        "next_month": next_month_date,
        "calendar_weeks": calendar_weeks,
        "days_with_menu": days_with_menu,
    }


def build_day_menu_context(user, selected_date):
    """Build context pro den - ZOBRAZÍ I UZAVŘENÉ DNY"""
    menu_items = PolozkaJidelnicku.objects.none()
    jidelnicky_den = Jidelnicek.objects.filter(
        platnost_od__lte=selected_date,
        platnost_do__gte=selected_date,
    )

    if jidelnicky_den.exists():
        menu_items = (
            PolozkaJidelnicku.objects.filter(jidelnicek__in=jidelnicky_den)
            .select_related("jidelnicek", "jidlo", "druh_jidla")
            .prefetch_related("jidlo__alergeny")
            .order_by("druh_jidla__nazev", "jidlo__nazev")
        )

        # ✅ filtrovat podle skupiny uživatele
        menu_items = _filter_items_for_user_group(user, menu_items)

        for item in menu_items:
            validate_item_for_display(user, item, selected_date)
            item.target_date = selected_date
            item.common_allergens = item.jidlo.spolecne_alergeny(user)

    menu_items_grouped = {}
    for item in menu_items:
        druh = item.druh_jidla
        if druh not in menu_items_grouped:
            menu_items_grouped[druh] = []
        menu_items_grouped[druh].append(item)

    return {
        "menu_items": menu_items,
        "menu_items_grouped": menu_items_grouped,
    }


def build_week_menu_context(user, selected_date):
    """Build context pro týden - zobrazí všechny dny s jídelníčkem."""
    menu_items_by_day = {}
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=6)

    current = week_start
    while current <= week_end:
        jidelnicky_den = Jidelnicek.objects.filter(
            platnost_od__lte=current,
            platnost_do__gte=current,
        )

        if jidelnicky_den.exists():
            day_items = (
                PolozkaJidelnicku.objects.filter(jidelnicek__in=jidelnicky_den)
                .select_related("jidelnicek", "jidlo", "druh_jidla")
                .prefetch_related("jidlo__alergeny")
                .order_by("druh_jidla__nazev", "jidlo__nazev")
            )

            # ✅ filtrovat podle skupiny uživatele
            day_items = _filter_items_for_user_group(user, day_items)

            items_list = []
            for item in day_items:
                validate_item_for_display(user, item, current)
                item.target_date = current
                items_list.append(item)
                item.common_allergens = item.jidlo.spolecne_alergeny(user) 

            if items_list:
                menu_items_by_day[current] = items_list

        current += timedelta(days=1)

    menu_items_by_day_grouped = {}
    for day, items in menu_items_by_day.items():
        day_grouped = {}
        for item in items:
            druh = item.druh_jidla
            if druh not in day_grouped:
                day_grouped[druh] = []
            day_grouped[druh].append(item)
        menu_items_by_day_grouped[day] = day_grouped

    return {
        "menu_items_by_day": menu_items_by_day,
        "menu_items_by_day_grouped": menu_items_by_day_grouped,
        "week_start": week_start,
        "week_end": week_end,
    }


def build_month_menu_context(user, first_day_month, last_day_month):
    """Build context pro měsíc - zobrazí všechny dny s jídelníčkem."""
    menu_items_by_day = {}

    current_date = first_day_month

    while current_date <= last_day_month:
        jidelnicky_den = Jidelnicek.objects.filter(
            platnost_od__lte=current_date,
            platnost_do__gte=current_date,
        )

        if jidelnicky_den.exists():
            day_items = (
                PolozkaJidelnicku.objects.filter(jidelnicek__in=jidelnicky_den)
                .select_related("jidelnicek", "jidlo", "druh_jidla")
                .prefetch_related("jidlo__alergeny")
                .order_by("druh_jidla__nazev", "jidlo__nazev")
            )

            # ✅ filtrovat podle skupiny uživatele
            day_items = _filter_items_for_user_group(user, day_items)

            items_list = []
            for item in day_items:
                validate_item_for_display(user, item, current_date)
                item.target_date = current_date
                items_list.append(item)
                item.common_allergens = item.jidlo.spolecne_alergeny(user) 

            if items_list:
                menu_items_by_day[current_date] = items_list

        current_date += timedelta(days=1)

    menu_items_by_day_grouped = {}
    for day, items in menu_items_by_day.items():
        day_grouped = {}
        for item in items:
            druh = item.druh_jidla
            if druh not in day_grouped:
                day_grouped[druh] = []
            day_grouped[druh].append(item)
        menu_items_by_day_grouped[day] = day_grouped

    return {
        "menu_items_by_day": menu_items_by_day,
        "menu_items_by_day_grouped": menu_items_by_day_grouped,
    }


def build_dashboard_redirect_from_post(request):
    """Vytvoří redirect na dashboard s původními parametry"""
    params = {}
    for param in ["filter", "date", "month", "year"]:
        value = request.POST.get(param)
        if value:
            params[param] = value

    scroll_pos = request.POST.get("scroll_position")
    if scroll_pos:
        params["scroll"] = scroll_pos

    query_string = urlencode(params) if params else ""

    dashboard_url = "/jidelnicek/dashboard/"
    return f"{dashboard_url}?{query_string}" if query_string else dashboard_url
