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
from django.db.models import Sum, F
from django.db import transaction
from decimal import Decimal
from collections import defaultdict

from .services import (
    get_user_price_for_item,
    get_effective_closing_time,
    get_group_order_limit,
    check_user_balance_for_item,
    get_user_order_items,
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


from objednavky.models import Order, OrderItem
from objednavky.services import validate_order_quantity
from jidelnicek.models import PolozkaJidelnicku, Jidelnicek
from dotace.models import SkupinoveNastaveni
from ankety.services import anketni_prehled_uzivatele
from users.group_utils import get_first_group_setting


logger = logging.getLogger(__name__)


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
    """✅ SPRÁVNĚ načte aktuální zůstatek z DB"""
    try:
        # Předpokládám User model s custom polem aktualni_zustatek
        return Decimal(str(user.aktualni_zustatek or 0))
    except:
        # Fallback: spočítat z objednávek
        total_orders = OrderItem.objects.filter(
            order__user=user,
            order__datum_vydeje__gte=date.today().replace(day=1)
        ).aggregate(total=Sum(F('quantity') * F('cena')))['total'] or 0
        return Decimal('0') - Decimal(str(total_orders or 0))

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
    my_order_items = get_user_order_items(request.user)
    html = render_to_string('includes/_my_orders.html', {
        'my_order_items': my_order_items,
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
    if month and year:
        selected_date = date(int(year), int(month), 1)
    elif filter_type == 'date' and reference_date:
        selected_date = reference_date
    else:
        selected_date = today

    # ✅ Kalendář vždy podle selected_date (bez změn)
    first_day_month = selected_date.replace(day=1)
    if first_day_month.month == 12:
        last_day_month = date(first_day_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day_month = first_day_month.replace(month=first_day_month.month + 1, day=1) - timedelta(days=1)

    calendar_ctx = build_calendar_context(selected_date)

    # ✅ DEN - vždy selected_date
    day_ctx = build_day_menu_context(request.user, selected_date)

    # ✅ TÝDEN - POUŽIJ reference_date (pokud existuje), jinak selected_date
    week_reference = reference_date or selected_date
    week_ctx = build_week_menu_context(request.user, week_reference)

    # Filtruj dny >= today, ale zachovej reference
    week_ctx['menu_items_by_day'] = {
        d: items for d, items in week_ctx.get('menu_items_by_day', {}).items() if d >= today
    }
    week_ctx['menu_items_by_day_grouped'] = {
        d: items for d, items in week_ctx.get('menu_items_by_day_grouped', {}).items() if d >= today
    }

    # ✅ MĚSÍC - měsíc z selected_date, ale data od today
    month_first = max(first_day_month, today)
    month_ctx = build_month_menu_context(request.user, month_first, last_day_month)

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

    # Seřaď TÝDEN
    if week_ctx.get('menu_items_by_day_grouped'):
        for day in week_ctx['menu_items_by_day_grouped']:
            week_ctx['menu_items_by_day_grouped'][day] = sort_druhy_by_priority(
                week_ctx['menu_items_by_day_grouped'][day]
            )

    # Seřaď MĚSÍC
    if month_ctx.get('menu_items_by_day_grouped'):
        for day in month_ctx['menu_items_by_day_grouped']:
            month_ctx['menu_items_by_day_grouped'][day] = sort_druhy_by_priority(
                month_ctx['menu_items_by_day_grouped'][day]
            )

    # Seřaď DEN
    if day_ctx.get('menu_items_grouped'):
        day_ctx['menu_items_grouped'] = sort_druhy_by_priority(day_ctx['menu_items_grouped'])

    # Výběr dat podle filtru
    if filter_type == 'week':
        menu_items_by_day_grouped = week_ctx.get('menu_items_by_day_grouped', {})
    elif filter_type == 'month':
        menu_items_by_day_grouped = month_ctx.get('menu_items_by_day_grouped', {})
    else:
        menu_items_by_day_grouped = {}

    week_items_count = sum(len(items) for items in week_ctx.get('menu_items_by_day', {}).values())
    month_items_count = sum(len(items) for items in month_ctx.get('menu_items_by_day', {}).values())

    my_order_items = get_user_order_items(request.user)
    ankety_prehled = anketni_prehled_uzivatele(request.user, today)
    my_orders = Order.objects.filter(
        user=request.user,
        datum_vydeje__month=selected_date.month,
        datum_vydeje__year=selected_date.year
    ).prefetch_related('items').order_by('-created_at')[:5]

    context = {
        **calendar_ctx,
        'menu_items': day_ctx.get('menu_items', []),
        'menu_items_grouped': day_ctx.get('menu_items_grouped', {}),
        'menu_items_by_day_grouped': menu_items_by_day_grouped,
        'week_items_count': week_items_count,
        'month_items_count': month_items_count,
        'week_start': week_ctx.get('week_start'),
        'week_end': week_ctx.get('week_end'),
        'my_orders': my_orders,
        'filter': filter_type,
        'selected_date': selected_date,
        'date_str': selected_date.strftime('%Y-%m-%d'),
        'my_order_items': my_order_items,
        'ankety_hodnotit': ankety_prehled["hodnotit"],
        'ankety_hotovo': ankety_prehled["hotovo"],
        'ankety_otazky_count': ankety_prehled["otazky_count"],
        'today': today,
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


    return render(request, 'dashboard.html', context)



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

        # 4. Kontrola zůstatku / debetu
        balance_settings = get_user_balance_settings(request.user)
        current_balance = get_user_balance(request.user)
        new_balance = current_balance - total_price

        if balance_settings['nutnost_dobit'] and new_balance < 0:
            return JsonResponse({'status': 'error', 'message': 'Nedostatek zůstatku'})

        if balance_settings['cerpani_debit']:
            debit_limit = Decimal(str(balance_settings['debit_limit']))
            if new_balance < -abs(debit_limit):
                return JsonResponse({'status': 'error', 'message': 'Překročen debet'})

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

            update_user_balance(request.user, -total_price)

        # 6. Refresh
        request.user.refresh_from_db()
        menu_item.refresh_from_db()

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
            'my_order_items': get_user_order_items(request.user)
        }, request=request)
        account_html = render_to_string('includes/_account_status.html', {
            'user': request.user
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

            update_user_balance(request.user, return_price)

        request.user.refresh_from_db()
        menu_item.refresh_from_db()

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
            'my_order_items': get_user_order_items(request.user)
        }, request=request)
        account_html = render_to_string('includes/_account_status.html', {
            'user': request.user
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
