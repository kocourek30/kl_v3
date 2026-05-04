from datetime import datetime, date, timedelta
import logging
from django.shortcuts import redirect, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.urls import reverse
from urllib.parse import urlencode
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.db.models import Sum, Q
from django.db import transaction
from decimal import Decimal
from collections import defaultdict

from .services import (
    get_user_price_for_item,
    get_effective_closing_time,
    get_group_order_limit,
    check_user_balance_for_item,
    validate_item_for_display,
    can_user_access_menu_item,
    build_calendar_context,
    build_day_menu_context,
    build_week_menu_context,
    build_month_menu_context,
    build_dashboard_redirect_from_post,
    can_order_for_date,
    check_group_limit,
)

from canteen_settings.models import (
    CanteenContact, MealPickupTime, OperatingDays, OperatingExceptions
)


from objednavky.models import Order, OrderItem, OrderValidator, OrderCancellationLog
from objednavky.services import validate_order_quantity
from jidelnicek.models import PolozkaJidelnicku, Jidelnicek
from dotace.models import SkupinoveNastaveni
from ankety.services import anketni_prehled_uzivatele
from users.group_utils import get_first_group_setting
from users.group_utils import get_effective_user_groups


logger = logging.getLogger(__name__)
OPEN_ORDER_STATUSES = ["zalozena-obsluhou", "objednano"]


def sort_druhy_by_priority(items_by_druh):
    if not items_by_druh:
        return {}

    sorted_keys = sorted(
        items_by_druh.keys(),
        key=lambda druh: (
            getattr(druh, 'poradi', 100),
            getattr(druh, 'nazev', str(druh)),
        ),
    )
    return {key: items_by_druh[key] for key in sorted_keys}


def count_menu_items_for_range(user, start_date, end_date):
    """Lehké spočítání položek v intervalu bez drahé validace položek."""
    if start_date > end_date:
        return 0

    jidelnicky = list(
        Jidelnicek.objects.filter(
            platnost_od__lte=end_date,
            platnost_do__gte=start_date,
        ).only("id", "platnost_od", "platnost_do")
    )
    if not jidelnicky:
        return 0

    jidelnicek_ids = [j.id for j in jidelnicky]
    day_items = PolozkaJidelnicku.objects.filter(jidelnicek_id__in=jidelnicek_ids)
    if not request_user_is_superuser(user):
        user_groups = get_effective_user_groups(user)
        if user_groups:
            day_items = day_items.filter(
                Q(druh_jidla__viditelne_pro_skupiny__in=user_groups)
                | Q(druh_jidla__viditelne_pro_skupiny__isnull=True)
            )
        else:
            day_items = day_items.filter(druh_jidla__viditelne_pro_skupiny__isnull=True)

    items_by_jidelnicek = {}
    for row in day_items.values("jidelnicek_id", "id").distinct():
        items_by_jidelnicek.setdefault(row["jidelnicek_id"], set()).add(row["id"])

    total = 0
    current_day = start_date
    while current_day <= end_date:
        active_item_ids = set()
        for j in jidelnicky:
            if j.platnost_od <= current_day <= j.platnost_do:
                active_item_ids.update(items_by_jidelnicek.get(j.id, set()))
        total += len(active_item_ids)
        current_day += timedelta(days=1)

    return total


def request_user_is_superuser(user):
    return getattr(user, "is_superuser", False)


def get_item_name(item):
    for field_name in ['nazev', 'name', 'title', 'nazev_jidla']:
        if hasattr(item, field_name):
            return str(getattr(item, field_name))
    try:
        return str(item)
    except:
        return f"Položka ID={item.id}"


def get_user_balance_settings(user):
    try:
        nastaveni = get_first_group_setting(user)
        if nastaveni:
            return {
                'cerpani_debit': nastaveni.cerpani_debit,
                'nutnost_dobit': nastaveni.nutnost_dobit,
                'debit_limit': nastaveni.debit_limit,
            }
    except Exception:
        logger.exception("Chyba při načítání nastavení zůstatku.")
    return {
        'cerpani_debit': False,
        'nutnost_dobit': True,
        'debit_limit': Decimal('0'),
    }


def get_user_balance(user):
    """Jednotný zdroj pravdy pro zůstatek uživatele."""
    try:
        return Decimal(str(user.aktualni_zustatek or 0))
    except Exception:
        logger.exception("Chyba při načtení aktuálního zůstatku uživatele.")
        return Decimal("0")

def update_user_balance(user, amount_change):
    """
    Zůstatek uživatele je počítaná hodnota z vkladů a objednávek.
    Položka objednávky se ukládá v téže transakci, takže není potřeba
    samostatně zapisovat zůstatek do uživatelského modelu.
    """
    return True


from objednavky.views import (
    can_order_for_menuitem_date,
    can_cancel_order_for_menuitem_date,
)
# nebo z místa, kde ty funkce máš – důležité je je naimportovat


@login_required
def menu_item_partial(request):
    menu_item_id = request.GET.get('menu_item_id')
    menu_date_str = request.GET.get('menu_date')

    if not menu_item_id or not menu_date_str:
        return JsonResponse({'error': 'missing_params'}, status=400)

    try:
        menu_item = PolozkaJidelnicku.objects.get(id=menu_item_id)
        target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()
    except PolozkaJidelnicku.DoesNotExist:
        return JsonResponse({'error': 'not_found'}, status=404)
    except ValueError:
        return JsonResponse({'error': 'bad_date'}, status=400)

    if not can_user_access_menu_item(request.user, menu_item):
        return JsonResponse({'error': 'not_found'}, status=404)

    validate_item_for_display(request.user, menu_item, target_date)

    order_item = OrderItem.objects.filter(
        order__user=request.user,
        order__datum_vydeje=target_date,
        menu_item=menu_item
    ).first()

    # stav objednávky
    if order_item:
        order_status = "ordered"
        current_quantity = order_item.quantity
    else:
        order_status = ""
        current_quantity = 0

    # flagy pro šablonu
    can_order, _ = can_order_for_menuitem_date(request.user, menu_item, target_date)
    can_cancel, _ = can_cancel_order_for_menuitem_date(request.user, menu_item, target_date)

    common_allergens = menu_item.jidlo.spolecne_alergeny(request.user)

    context = {
        'item': menu_item,
        'date': target_date,
        'current_order_item_id': order_item.id if order_item else None,
        'current_quantity': current_quantity,
        'order_status': order_status,
        'can_order': can_order,
        'can_cancel': can_cancel,
        'common_allergens': common_allergens,   
    }

    html = render_to_string('jidelnicek_item.html', context, request=request)
    return JsonResponse({'html': html})


@login_required
def my_orders_partial(request):
    my_day_orders = get_user_day_orders(request.user)
    html = render_to_string('includes/_my_orders.html', {
        'my_day_orders': my_day_orders,
    }, request=request)
    return JsonResponse({'html': html})


def get_first_menu_day_from(from_date: date) -> date | None:
    """
    Najde první den s jídelníčkem od from_date (včetně) dál.
    """
    nearest_menu = (
        Jidelnicek.objects
        .filter(platnost_do__gte=from_date)
        .order_by('platnost_od')
        .first()
    )
    if not nearest_menu:
        return None
    return max(nearest_menu.platnost_od, from_date)


def get_first_menu_day_in_month(year: int, month: int) -> date | None:
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    nearest_menu = (
        Jidelnicek.objects.filter(
            platnost_od__lte=month_end,
            platnost_do__gte=month_start,
        )
        .order_by("platnost_od")
        .first()
    )
    if not nearest_menu:
        return None
    return max(nearest_menu.platnost_od, month_start)


def get_first_orderable_menu_day_in_month(user, year: int, month: int) -> date | None:
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    current = max(month_start, date.today())
    while current <= month_end:
        has_menu = Jidelnicek.objects.filter(
            platnost_od__lte=current,
            platnost_do__gte=current,
        ).exists()
        if has_menu:
            can_order, _ = can_order_for_date(user, current)
            if can_order:
                return current
        current += timedelta(days=1)
    return None


def get_user_day_orders(user):
    """
    Vrátí budoucí objednávky uživatele seskupené po dnech pro odhlášky.
    """
    day_groups = []

    items = (
        OrderItem.objects.filter(
            order__user=user,
            order__datum_vydeje__gte=date.today(),
            order__status__in=OPEN_ORDER_STATUSES,
        )
        .select_related("order", "menu_item__jidlo", "menu_item__druh_jidla")
        .order_by(
            "order__datum_vydeje",
            "menu_item__druh_jidla__poradi",
            "menu_item__druh_jidla__nazev",
            "menu_item__jidlo__nazev",
            "menu_item__id",
        )
    )

    grouped = defaultdict(list)
    for item in items:
        grouped[item.order.datum_vydeje].append(item)

    for target_date in sorted(grouped.keys()):
        day_items = grouped[target_date]
        for i in day_items:
            i.line_total = (i.quantity or 0) * (i.cena or 0)
        total_qty = sum(int(i.quantity or 0) for i in day_items)
        total_price = sum((i.quantity or 0) * (i.cena or 0) for i in day_items)

        requires_reason = False
        for i in day_items:
            can_cancel, _ = can_cancel_order_for_menuitem_date(
                user,
                i.menu_item,
                target_date,
            )
            if not can_cancel:
                requires_reason = True
                break

        day_groups.append(
            {
                "date": target_date,
                "items": day_items,
                "total_qty": total_qty,
                "total_price": total_price,
                "requires_reason": requires_reason,
            }
        )

    return day_groups


def build_day_actions_map(user, day_orders, menu_items_by_day):
    """
    Připraví akce pro levý panel (objednat/odhlásit celý den) po jednotlivých dnech.
    """
    actions = {}
    day_orders_by_date = {entry["date"]: entry for entry in day_orders}
    is_staff_mode = bool(user.is_staff or user.is_superuser)

    for target_date, day_items in menu_items_by_day.items():
        order_entry = day_orders_by_date.get(target_date)
        if order_entry:
            order_items = order_entry.get("items", [])
            actions[target_date] = {
                "mode": "cancel",
                "has_order": True,
                "date": target_date,
                "label": target_date.strftime("%d.%m.%Y"),
                "requires_reason": bool(order_entry.get("requires_reason")),
                "total_price": order_entry.get("total_price") or Decimal("0"),
                "total_qty": order_entry.get("total_qty") or 0,
                "first_menu_item_id": order_items[0].menu_item_id if order_items else None,
                "items": [
                    {
                        "name": oi.menu_item.jidlo.nazev,
                        "qty": oi.quantity or 0,
                        "line_total": getattr(oi, "line_total", (oi.quantity or 0) * (oi.cena or 0)),
                    }
                    for oi in order_items
                ],
            }
            continue

        can_order_day, order_reason = can_order_for_date(user, target_date)
        actions[target_date] = {
            "mode": "order" if (is_staff_mode and can_order_day and bool(day_items)) else "none",
            "has_order": False,
            "date": target_date,
            "label": target_date.strftime("%d.%m.%Y"),
            "order_reason": order_reason or "",
            "total_items": len(day_items or []),
        }

    return actions


@login_required
def dashboard(request):
    """Hlavní dashboard - data jen od dneška dál, kalendář lze listovat libovolně"""
    today = date.today()

    date_str = request.GET.get('date')
    month = request.GET.get('month')
    year = request.GET.get('year')
    filter_type = request.GET.get('filter', 'date')

    # ✅ PRIORITA 1: reference_date pro week/month z URL date parametru
    reference_date = None
    if date_str:
        try:
            reference_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # ✅ PRIORITA 2: selected_date pro kalendář a denní zobrazení
    # Pro týdenní/denní pohled musí mít "date" přednost, aby kalendář
    # i obsah byly ve stejném měsíci/týdnu.
    if filter_type in {"date", "week"} and reference_date:
        selected_date = reference_date
    elif month and year:
        month_int = int(month)
        year_int = int(year)

        if (
            reference_date
            and reference_date.year == year_int
            and reference_date.month == month_int
        ):
            selected_date = reference_date
        else:
            selected_date = (
                get_first_orderable_menu_day_in_month(request.user, year_int, month_int)
                or get_first_menu_day_in_month(year_int, month_int)
                or date(year_int, month_int, 1)
            )
    elif reference_date:
        selected_date = reference_date
    else:
        selected_date = today

    if filter_type not in {"date", "week", "month"}:
        filter_type = "date"

    # ✅ Kalendář vždy podle selected_date
    first_day_month = selected_date.replace(day=1)
    if first_day_month.month == 12:
        last_day_month = date(first_day_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day_month = first_day_month.replace(month=first_day_month.month + 1, day=1) - timedelta(days=1)

    calendar_ctx = build_calendar_context(selected_date)

    # ✅ DEN - vždy selected_date (pro denní tab + badge)
    day_ctx = build_day_menu_context(request.user, selected_date)

    week_reference = reference_date or selected_date
    week_ctx = {
        'menu_items_by_day': {},
        'menu_items_by_day_grouped': {},
        'week_start': week_reference - timedelta(days=week_reference.weekday()),
        'week_end': week_reference - timedelta(days=week_reference.weekday()) + timedelta(days=6),
    }
    month_ctx = {
        'menu_items_by_day': {},
        'menu_items_by_day_grouped': {},
    }

    month_first = first_day_month

    if filter_type == 'week':
        week_ctx = build_week_menu_context(request.user, week_reference)
        if week_ctx.get('menu_items_by_day_grouped'):
            for day in week_ctx['menu_items_by_day_grouped']:
                week_ctx['menu_items_by_day_grouped'][day] = sort_druhy_by_priority(
                    week_ctx['menu_items_by_day_grouped'][day]
                )
    elif filter_type == 'month':
        month_ctx = build_month_menu_context(request.user, month_first, last_day_month)
        if month_ctx.get('menu_items_by_day_grouped'):
            for day in month_ctx['menu_items_by_day_grouped']:
                month_ctx['menu_items_by_day_grouped'][day] = sort_druhy_by_priority(
                    month_ctx['menu_items_by_day_grouped'][day]
                )

    # Seřaď DEN
    if day_ctx.get('menu_items_grouped'):
        day_ctx['menu_items_grouped'] = sort_druhy_by_priority(day_ctx['menu_items_grouped'])

    # Výběr dat podle filtru
    menu_items_by_day = {}
    if filter_type == 'week':
        menu_items_by_day_grouped = week_ctx.get('menu_items_by_day_grouped', {})
        menu_items_by_day = week_ctx.get('menu_items_by_day', {})
    elif filter_type == 'month':
        menu_items_by_day_grouped = month_ctx.get('menu_items_by_day_grouped', {})
        menu_items_by_day = month_ctx.get('menu_items_by_day', {})
    else:
        menu_items_by_day_grouped = {}
        menu_items_by_day = {}

    if filter_type == 'week':
        week_items_count = sum(len(items) for items in week_ctx.get('menu_items_by_day', {}).values())
    else:
        week_start_for_count = week_reference - timedelta(days=week_reference.weekday())
        week_end_for_count = week_start_for_count + timedelta(days=6)
        week_items_count = count_menu_items_for_range(request.user, week_start_for_count, week_end_for_count)

    if filter_type == 'month':
        month_items_count = sum(len(items) for items in month_ctx.get('menu_items_by_day', {}).values())
    else:
        month_items_count = count_menu_items_for_range(request.user, month_first, last_day_month)

    my_day_orders = get_user_day_orders(request.user)
    day_actions = build_day_actions_map(
        request.user,
        my_day_orders,
        {selected_date: day_ctx.get('menu_items', []), **menu_items_by_day},
    )
    ankety_prehled = anketni_prehled_uzivatele(request.user, today)
    my_orders = Order.objects.filter(
        user=request.user,
        datum_vydeje__month=selected_date.month,
        datum_vydeje__year=selected_date.year
    ).prefetch_related('items').order_by('-created_at')[:5]

    prev_month_target = (
        get_first_orderable_menu_day_in_month(
            request.user, calendar_ctx["prev_month"].year, calendar_ctx["prev_month"].month
        )
        or get_first_menu_day_in_month(calendar_ctx["prev_month"].year, calendar_ctx["prev_month"].month)
        or calendar_ctx["prev_month"].replace(day=1)
    )
    next_month_target = (
        get_first_orderable_menu_day_in_month(
            request.user, calendar_ctx["next_month"].year, calendar_ctx["next_month"].month
        )
        or get_first_menu_day_in_month(calendar_ctx["next_month"].year, calendar_ctx["next_month"].month)
        or calendar_ctx["next_month"].replace(day=1)
    )

    context = {
        **calendar_ctx,
        'menu_items': day_ctx.get('menu_items', []),
        'menu_items_grouped': day_ctx.get('menu_items_grouped', {}),
        'menu_items_by_day': menu_items_by_day,
        'menu_items_by_day_grouped': menu_items_by_day_grouped,
        'week_items_count': week_items_count,
        'month_items_count': month_items_count,
        'week_start': week_ctx.get('week_start'),
        'week_end': week_ctx.get('week_end'),
        'my_orders': my_orders,
        'filter': filter_type,
        'selected_date': selected_date,
        'date_str': selected_date.strftime('%Y-%m-%d'),
        'my_day_orders': my_day_orders,
        'day_actions': day_actions,
        'ankety_hodnotit': ankety_prehled["hodnotit"],
        'ankety_hotovo': ankety_prehled["hotovo"],
        'ankety_otazky_count': ankety_prehled["otazky_count"],
        'mesicni_anketa': ankety_prehled.get("mesicni_anketa", {}),
        'today': today,
        'prev_month_target_date': prev_month_target,
        'next_month_target_date': next_month_target,
    }

    context.update({
        'canteen_contact': CanteenContact.objects.first(),
        'meal_pickup_times': MealPickupTime.objects.select_related('druh_jidla').order_by(
            'druh_jidla__poradi',
            'druh_jidla__nazev',
        ),
        'provozni_dny': OperatingDays.objects.filter(is_operating=True),
        'exceptions': OperatingExceptions.objects.filter(
            date__gte=timezone.now().date()
        ).order_by('date')[:3]
    })


    return render(request, 'jidelnicek/dashboard.html', context)



def get_dashboard_url(request):
    """Vytvoří URL na dashboard s parametry"""
    params = {}
    for param in ['filter', 'date', 'month', 'year']:
        value = request.POST.get(param)
        if value: params[param] = value
    scroll_pos = request.POST.get('scroll_position')
    if scroll_pos: params['scroll'] = scroll_pos
    query_string = urlencode(params) if params else ''
    return f"/jidelnicek/dashboard/?{query_string}" if query_string else "/jidelnicek/dashboard/"


# ... (zbytek views.py zůstává stejný až do order_create_view)

@login_required
@require_POST
def order_create_view(request):
    """✅ OKAMŽITÁ OBJEDNÁVKA - S KONTROLOU GROUP LIMITU"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)
    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse(
            {
                'status': 'error',
                'message': 'Objednávky se vytváří automaticky. Uživatelé provádí pouze odhlášku celého dne.',
            },
            status=403,
        )

    create_day = str(request.POST.get("create_day", "")).lower() in {"1", "true", "yes", "on"}

    if create_day:
        menu_date_str = request.POST.get('menudate') or request.POST.get('menu_date')
        try:
            target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return JsonResponse({'status': 'error', 'message': 'Neplatné datum dne.'}, status=400)

        if target_date < timezone.localdate():
            return JsonResponse(
                {'status': 'error', 'message': f'Historický den {target_date.strftime("%d.%m.%Y")} již nelze objednat.'},
                status=400,
            )

        can_order_day, day_reason = can_order_for_date(request.user, target_date)
        if not can_order_day:
            return JsonResponse({'status': 'error', 'message': day_reason or 'Objednávky pro tento den jsou uzavřené.'}, status=400)

        day_ctx = build_day_menu_context(request.user, target_date)
        day_items = list(day_ctx.get("menu_items", []))
        if not day_items:
            return JsonResponse({'status': 'warning', 'message': 'Pro zvolený den není žádná položka jídelníčku.'})

        existing_order = Order.objects.filter(
            user=request.user,
            datum_vydeje=target_date,
            status__in=OPEN_ORDER_STATUSES,
        ).first()
        existing_by_menu_item = {}
        if existing_order:
            existing_by_menu_item = {
                oi.menu_item_id: oi
                for oi in existing_order.items.select_related("menu_item", "menu_item__jidlo", "menu_item__druh_jidla").all()
            }

        items_to_create = []
        total_added_qty = 0
        total_added_price = Decimal("0")
        for item in day_items:
            if item.id in existing_by_menu_item:
                continue

            can_order_item, item_reason = can_order_for_menuitem_date(request.user, item, target_date)
            if not can_order_item:
                return JsonResponse(
                    {
                        'status': 'error',
                        'message': item_reason or f'Položku "{item.jidlo.nazev}" nelze objednat.',
                    },
                    status=400,
                )

            can_group, group_msg = check_group_limit(request.user, item, target_date, 1)
            if not can_group:
                return JsonResponse({'status': 'error', 'message': group_msg}, status=400)

            price_per_item = get_user_price_for_item(
                request.user,
                item,
                target_date=target_date,
                quantity=1,
                exclude_order_item_id=None,
            )
            total_added_price += price_per_item
            total_added_qty += 1
            items_to_create.append((item, price_per_item))

        if not items_to_create:
            return JsonResponse(
                {
                    'status': 'warning',
                    'message': 'Všechna jídla dne už jsou objednaná.',
                    'my_orders_html': render_to_string(
                        'includes/_my_orders.html',
                        {'my_day_orders': get_user_day_orders(request.user)},
                        request=request,
                    ),
                    'refresh_menu': True,
                }
            )

        existing_qty = OrderItem.objects.filter(
            order__user=request.user,
            order__datum_vydeje=target_date
        ).aggregate(total=Sum('quantity'))['total'] or 0

        if existing_qty + total_added_qty > 10:
            return JsonResponse({'status': 'error', 'message': 'Denní limit 10 porcí by byl překročen.'}, status=400)

        ok_balance, balance_reason = OrderValidator.check_user_balance(request.user, total_added_price)
        if not ok_balance:
            if balance_reason == "debit_limit_exceeded":
                return JsonResponse({'status': 'error', 'message': 'Objednávka dne by překročila debetní limit.'}, status=400)
            return JsonResponse({'status': 'error', 'message': 'Nedostatečný zůstatek pro objednání celého dne.'}, status=400)

        try:
            with transaction.atomic():
                order, _ = Order.objects.select_for_update().get_or_create(
                    user=request.user,
                    datum_vydeje=target_date
                )
                for item, price_per_item in items_to_create:
                    OrderItem.objects.create(
                        order=order,
                        menu_item=item,
                        quantity=1,
                        cena=price_per_item,
                    )
        except Exception:
            logger.exception("Chyba při hromadném objednání dne.")
            return JsonResponse({'status': 'error', 'message': 'Objednání celého dne se nepodařilo dokončit.'}, status=500)

        my_orders_html = render_to_string(
            'includes/_my_orders.html',
            {'my_day_orders': get_user_day_orders(request.user)},
            request=request,
        )
        final_balance = get_user_balance(request.user)
        balance_class = '' if final_balance >= 0 else ''
        return JsonResponse(
            {
                'status': 'success',
                'message': f'Objednán celý den {target_date.strftime("%d.%m.%Y")} ({len(items_to_create)} položek).',
                'my_orders_html': my_orders_html,
                'navbar_balance': f"{final_balance:.0f} Kč",
                'navbar_balance_class': balance_class,
                'balance': float(final_balance),
                'refresh_menu': True,
                'refresh_date': target_date.strftime("%Y-%m-%d"),
            }
        )

    menu_item_id = request.POST.get('menu_item_id') or request.POST.get('menuitemid')
    menu_date_str = request.POST.get('menudate') or request.POST.get('menu_date')
    try:
        quantity = validate_order_quantity(request.POST.get('quantity', 1))
    except Exception as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else "Neplatné množství."
        return JsonResponse({'status': 'error', 'message': msg}, status=400)

    try:
        menu_item = PolozkaJidelnicku.objects.select_related('jidlo', 'druh_jidla').get(id=menu_item_id)
        target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()
        item_name = get_item_name(menu_item)

        if target_date < timezone.localdate():
            return JsonResponse(
                {
                    'status': 'error',
                    'message': f'Objednávky na {target_date.strftime("%d.%m.%Y")} již nelze vytvářet ani měnit.',
                }
            )

        if not can_user_access_menu_item(request.user, menu_item):
            return JsonResponse(
                {'status': 'error', 'message': 'Tento druh jídla pro vás není dostupný.'},
                status=403,
            )

        # 1. Validace časová – per položka (druh jídla)
        can_order_time, time_msg = can_order_for_menuitem_date(request.user, menu_item, target_date)
        if not can_order_time:
            return JsonResponse({'status': 'error', 'message': time_msg or 'Objednávky zavřené'})

        # 2. ✅ KONTROLA GROUP LIMITU
        can_order_group, group_msg = check_group_limit(request.user, menu_item, target_date, quantity)
        if not can_order_group:
            return JsonResponse({'status': 'error', 'message': group_msg})

        existing_order_item_for_price = OrderItem.objects.filter(
            order__user=request.user,
            order__datum_vydeje=target_date,
            menu_item=menu_item,
        ).first()
        existing_quantity_for_price = existing_order_item_for_price.quantity if existing_order_item_for_price else 0
        priced_quantity = existing_quantity_for_price + quantity

        price_per_item = get_user_price_for_item(
            request.user,
            menu_item,
            target_date=target_date,
            quantity=priced_quantity,
            exclude_order_item_id=existing_order_item_for_price.pk if existing_order_item_for_price else None,
        )
        total_price = (price_per_item * priced_quantity) - (
            existing_order_item_for_price.cena * existing_quantity_for_price
            if existing_order_item_for_price else Decimal('0')
        )

        # 3. Limit celkem na den
        existing_qty = OrderItem.objects.filter(
            order__user=request.user,
            order__datum_vydeje=target_date
        ).aggregate(total=Sum('quantity'))['total'] or 0

        if existing_qty + quantity > 10:
            return JsonResponse({'status': 'error', 'message': 'Max 10 kusů celkem za den!'})

        # 4. Kontrola zůstatku / debetu - jednotná validace domény
        ok_balance, balance_reason = OrderValidator.check_user_balance(request.user, total_price)
        if not ok_balance:
            if balance_reason == "debit_limit_exceeded":
                return JsonResponse({'status': 'error', 'message': 'Objednávka by překročila debetní limit.'})
            return JsonResponse({'status': 'error', 'message': 'Nedostatečný zůstatek.'})

        # 5. Vytvoř / uprav objednávku
        with transaction.atomic():
            order, _ = Order.objects.select_for_update().get_or_create(
                user=request.user,
                datum_vydeje=target_date
            )

            order_item, created = OrderItem.objects.get_or_create(
                order=order,
                menu_item_id=menu_item_id,
                defaults={'quantity': quantity, 'cena': price_per_item}
            )

            if not created:
                order_item.quantity += quantity
            order_item.cena = price_per_item
            order_item.save()

        # 6. Refresh (zůstatek je počítaný, není nutné zapisovat do user modelu)

        # ✅ Validuj pro hide_quantity
        validate_item_for_display(request.user, menu_item, target_date)

        order_item_final = OrderItem.objects.filter(
            order__user=request.user,
            order__datum_vydeje=target_date,
            menu_item=menu_item
        ).first()

        # Kontext pro partial
        if order_item_final:
            order_status = "ordered"
            current_qty = order_item_final.quantity
        else:
            order_status = ""
            current_qty = 0

        can_order_flag, _ = can_order_for_menuitem_date(request.user, menu_item, target_date)
        can_cancel_flag, _ = can_cancel_order_for_menuitem_date(request.user, menu_item, target_date)

        context = {
            'item': menu_item,
            'date': target_date,
            'current_order_item_id': order_item_final.id if order_item_final else None,
            'current_quantity': current_qty,
            'order_status': order_status,
            'can_order': can_order_flag,
            'can_cancel': can_cancel_flag,
            'common_allergens': menu_item.jidlo.spolecne_alergeny(request.user),
        }

        item_html = render_to_string('jidelnicek_item.html', context, request=request)
        my_orders_html = render_to_string('includes/_my_orders.html', {
            'my_day_orders': get_user_day_orders(request.user)
        }, request=request)
        # ✅ AJAX response s kompletními daty pro aktualizaci všech panelů
        final_balance = get_user_balance(request.user)
        balance_class = '' if final_balance >= 0 else ''

        return JsonResponse({
            'status': 'success',
            'message': f'✅ Přidáno {quantity}x {item_name}',
            'item_html': item_html,
            'my_orders_html': my_orders_html,
            'navbar_balance': f"{final_balance:.0f} Kč",
            'navbar_balance_class': balance_class,
            'menu_item_id': menu_item_id,
            'balance': float(final_balance),
        })

    except Exception:
        logger.exception("Chyba při vytvoření objednávky z jídelníčku.")
        return JsonResponse({'status': 'error', 'message': 'Chyba objednávky'})
@login_required
@require_POST
def order_delete_view(request):
    """✅ OKAMŽITÉ ZRUŠENÍ – podle nových pravidel, menu_item_id + menu_date."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST only'}, status=405)

    # nový formát z jidelnicek_item.html
    menu_item_id = request.POST.get('menu_item_id') or request.POST.get('menuitemid')
    menu_date_str = request.POST.get('menudate') or request.POST.get('menu_date')
    cancel_day = str(request.POST.get("cancel_day", "")).lower() in {"1", "true", "yes", "on"}
    try:
        quantity_to_remove = validate_order_quantity(request.POST.get('quantity', 1))
    except Exception as exc:
        msg = exc.messages[0] if hasattr(exc, "messages") else "Neplatné množství."
        return JsonResponse({'status': 'error', 'message': msg}, status=400)

    if not menu_item_id or not menu_date_str:
        return JsonResponse({'status': 'error', 'message': 'Chybí parametry'})

    try:
        menu_item = PolozkaJidelnicku.objects.select_related('jidlo', 'druh_jidla').get(id=menu_item_id)
        target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()
    except PolozkaJidelnicku.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Položka nenalezena'})
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Neplatné datum'})

    if cancel_day:
        reason = (request.POST.get("cancel_reason") or "").strip()
        try:
            with transaction.atomic():
                order = (
                    Order.objects.select_for_update()
                    .prefetch_related("items__menu_item__druh_jidla", "items__menu_item__jidlo")
                    .filter(
                        user=request.user,
                        datum_vydeje=target_date,
                        status__in=OPEN_ORDER_STATUSES,
                    )
                    .first()
                )

                if not order:
                    return JsonResponse(
                        {
                            "status": "warning",
                            "message": "Pro tento den už není co odhlásit.",
                            "refresh_menu": True,
                        }
                    )

                day_items = list(order.items.all())
                if not day_items:
                    order.delete()
                    return JsonResponse(
                        {
                            "status": "warning",
                            "message": "Objednávka byla prázdná a byla uklizena.",
                            "refresh_menu": True,
                        }
                    )

                late_cancel = False
                for day_item in day_items:
                    can_cancel_time, _ = can_cancel_order_for_menuitem_date(
                        request.user,
                        day_item.menu_item,
                        target_date,
                    )
                    if not can_cancel_time:
                        late_cancel = True
                        break

                if late_cancel and len(reason) < 3:
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": "Po uzávěrce je povinné uvést důvod odhlášky (min. 3 znaky).",
                        }
                    )

                total_qty = sum(int(i.quantity or 0) for i in day_items)
                total_price = sum((i.quantity or 0) * (i.cena or 0) for i in day_items)

                OrderCancellationLog.objects.create(
                    user=request.user,
                    datum_vydeje=target_date,
                    cancelled_late=late_cancel,
                    reason=reason if late_cancel else "",
                    items_count=total_qty,
                    total_price=total_price,
                )

                order.delete()

            my_orders_html = render_to_string(
                "includes/_my_orders.html",
                {"my_day_orders": get_user_day_orders(request.user)},
                request=request,
            )
            final_balance = get_user_balance(request.user)
            balance_class = '' if final_balance >= 0 else ''
            success_message = (
                "Odhláška dne byla uložena."
                if not late_cancel
                else "Odhláška po uzávěrce byla uložena včetně důvodu."
            )
            return JsonResponse(
                {
                    "status": "success",
                    "message": success_message,
                    "my_orders_html": my_orders_html,
                    "navbar_balance": f"{final_balance:.0f} Kč",
                    "navbar_balance_class": balance_class,
                    "balance": float(final_balance),
                    "refresh_menu": True,
                    "refresh_date": target_date.strftime("%Y-%m-%d"),
                }
            )
        except Exception:
            logger.exception("Chyba při denní odhlášce.")
            return JsonResponse(
                {"status": "error", "message": "Denní odhlášku se nepodařilo zpracovat."}
            )

    if not (request.user.is_staff or request.user.is_superuser):
        return JsonResponse(
            {
                "status": "error",
                "message": "Jednotlivé položky nelze rušit. Použijte odhlášku celého dne.",
            },
            status=403,
        )

    # ✅ KONTROLA ČASU PRO ZRUŠENÍ – cancel_days + cancel_until_time
    can_cancel_time, time_msg = can_cancel_order_for_menuitem_date(request.user, menu_item, target_date)
    if not can_cancel_time:
        return JsonResponse({'status': 'error', 'message': time_msg})

    try:
        with transaction.atomic():
            order = (
                Order.objects
                .select_for_update()
                .filter(user=request.user, datum_vydeje=target_date)
                .first()
            )

            if not order:
                return JsonResponse({'status': 'error', 'message': 'Objednávka nenalezena'})

            order_item = (
                OrderItem.objects
                .select_for_update()
                .filter(order=order, menu_item=menu_item)
                .first()
            )

            if not order_item:
                return JsonResponse({'status': 'error', 'message': 'Položka nebyla objednána'})

            # ✅ KONTROLA STATUSU OBJEDNÁVKY
            if order_item.order.status not in ['zalozena-obsluhou', 'objednano']:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Tuto objednávku nelze zrušit (již byla vydána nebo zrušena)'
                })

            # cena vracená uživateli
            qty_to_remove = min(quantity_to_remove, order_item.quantity)
            return_price = order_item.cena * qty_to_remove

            if order_item.quantity <= qty_to_remove:
                order_item.delete()
            else:
                order_item.quantity -= qty_to_remove
                order_item.save()

            # pokud v objednávce nic nezbylo, smaž i Order
            if not order.items.exists():
                order.delete()

        # zůstatek je počítaný, není nutný zápis do user modelu

        # ✅ Validuj pro hide_quantity
        validate_item_for_display(request.user, menu_item, target_date)

        order_item_final = OrderItem.objects.filter(
            order__user=request.user,
            order__datum_vydeje=target_date,
            menu_item=menu_item
        ).first()

        if order_item_final:
            order_status = "ordered"
            current_qty = order_item_final.quantity
        else:
            order_status = ""
            current_qty = 0

        can_order_flag, _ = can_order_for_menuitem_date(request.user, menu_item, target_date)
        can_cancel_flag, _ = can_cancel_order_for_menuitem_date(request.user, menu_item, target_date)

        context = {
            'item': menu_item,
            'date': target_date,
            'current_order_item_id': order_item_final.id if order_item_final else None,
            'current_quantity': current_qty,
            'order_status': order_status,
            'can_order': can_order_flag,
            'can_cancel': can_cancel_flag,
            'common_allergens': menu_item.jidlo.spolecne_alergeny(request.user),
        }

        item_html = render_to_string('jidelnicek_item.html', context, request=request)
        my_orders_html = render_to_string('includes/_my_orders.html', {
            'my_day_orders': get_user_day_orders(request.user)
        }, request=request)
        # ✅ AJAX response s kompletními daty
        final_balance = get_user_balance(request.user)
        balance_class = '' if final_balance >= 0 else ''

        return JsonResponse({
            'status': 'success',
            'message': '🗑️ Objednávka zrušena!',
            'item_html': item_html,
            'my_orders_html': my_orders_html,
            'navbar_balance': f"{final_balance:.0f} Kč",
            'navbar_balance_class': balance_class,
            'menu_item_id': menu_item.id,
            'balance': float(final_balance),
        })

    except Exception:
        logger.exception("Chyba při rušení objednávky z jídelníčku.")
        return JsonResponse({'status': 'error', 'message': 'Chyba rušení'})


@login_required
def account_status_api(request):
    """AJAX: Kompletní stav konta + debetní limit"""
    balance_settings = get_user_balance_settings(request.user)
    current_balance = get_user_balance(request.user)
    
    context = {
        'user': request.user,
        'balance': current_balance,
        'balance_settings': balance_settings,
    }
    
    account_html = render_to_string('includes/_account_status.html', context, request=request)
    navbar_html = render_to_string('includes/_navbar_balance.html', context, request=request)
    
    balance_class = '' if current_balance >= 0 else ''
    
    return JsonResponse({
        'account_html': account_html,
        'navbar_html': navbar_html,
        'navbar_balance': f"{current_balance:.0f} Kč",
        'navbar_balance_class': balance_class,
        'status': 'ok'
    })

# ... (zbytek views.py zůstává stejný)



@login_required
def user_balance_api(request):
    """AJAX: Aktuální zůstatek"""
    balance = get_user_balance(request.user)
    return JsonResponse({
        'balance': float(balance),
        'formatted': f"{balance:.0f} Kč",
        'status': 'ok'
    })
