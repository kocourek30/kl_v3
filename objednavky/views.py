from datetime import datetime, timedelta
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum
from django.http import JsonResponse
from requests import request
from jidelnicek.services import build_dashboard_redirect_from_post  # ✅ PŘIDÁNO

from .models import Order, OrderItem, OrderValidator
from jidelnicek.models import PolozkaJidelnicku
from canteen_settings.models import OrderClosingTime, GroupOrderLimit

from django.db.models import Sum, F
from decimal import Decimal
from jidelnicek.models import PolozkaJidelnicku, DruhJidla
from canteen_settings.models import OrderClosingTime, GroupOrderLimit
from datetime import datetime, timedelta
from django.utils import timezone

def get_closing_setting_for_mealtype(druh_jidla: DruhJidla):
    """
    Vrátí aktivní nastavení pro konkrétní druh jídla,
    nebo globální nastavení (druh_jidla is null), pokud konkrétní není.
    """
    setting = OrderClosingTime.objects.filter(
        je_aktivni=True,
        druh_jidla=druh_jidla,
    ).first()
    if setting:
        return setting

    return OrderClosingTime.objects.filter(
        je_aktivni=True,
        druh_jidla__isnull=True,
    ).first()

def can_order_for_menuitem_date(user, menu_item: PolozkaJidelnicku, target_date):
    """
    Kontroluje, zda lze objednávat konkrétní položku (druh jídla) na dané datum.
    """
    if user.is_staff:
        return True, ""

    try:
        setting = get_closing_setting_for_mealtype(menu_item.druh_jidla)
        if not setting:
            return True, ""  # žádné nastavení = neomezovat

        closing_date = target_date - timedelta(days=setting.advance_days)
        tz = timezone.get_current_timezone()
        closing_datetime = timezone.make_aware(
            timezone.datetime.combine(closing_date, setting.closing_time),
            tz
        )

        if timezone.now() <= closing_datetime:
            return True, ""
        else:
            msg = (
                f"Objednávky na {target_date.strftime('%d.%m.%Y')} "
                f"pro {menu_item.druh_jidla.nazev} již nelze vytvářet ani měnit."
            )
            return False, msg
    except Exception:
        return True, ""

def can_cancel_order_for_menuitem_date(user, menu_item: PolozkaJidelnicku, target_date):
    """
    Kontroluje, zda lze zrušit objednávku pro konkrétní druh jídla na dané datum.
    Používá cancel_days + cancel_until_time (nezávisle na advance_days).
    """
    print("DEBUG: can_cancel_order_for_menuitem_date CALLED")

    if user.is_staff:
        print("DEBUG: user is staff -> True")
        return True, ""

    try:
        setting = get_closing_setting_for_mealtype(menu_item.druh_jidla)
        print("DEBUG: setting =", setting)

        if not setting:
            print("DEBUG: no setting found -> True")
            return True, ""

        tz = timezone.get_current_timezone()

        cancel_deadline_date = target_date - timedelta(days=setting.cancel_days)
        cancel_deadline_dt = timezone.make_aware(
            timezone.datetime.combine(cancel_deadline_date, setting.cancel_until_time),
            tz
        )

        now = timezone.now()

        print(
            "DEBUG CANCEL:",
            "user=", user.id,
            "menu_item=", menu_item.id,
            "druh_jidla=", menu_item.druh_jidla.nazev,
            "target_date=", target_date,
            "cancel_days=", setting.cancel_days,
            "cancel_until_time=", setting.cancel_until_time,
            "deadline=", cancel_deadline_dt,
            "now=", now,
        )

        if now <= cancel_deadline_dt:
            print("DEBUG RESULT: can_cancel = True")
            return True, ""
        else:
            msg = (
                f"Objednávku na {target_date.strftime('%d.%m.%Y')} "
                f"pro {menu_item.druh_jidla.nazev} již nelze zrušit."
            )
            print("DEBUG RESULT: can_cancel = False ->", msg)
            return False, msg
    except Exception as e:
        print("DEBUG ERROR in can_cancel_order_for_menuitem_date:", e)
        return True, ""

    


def get_user_balance(user):
    """✅ AKTUÁLNÍ ZŮSTATEK: zakladni_zustatek - VŠE OBJEDNÁVKY"""
    try:
        # 1. ZAKLADNÍ ZŮSTATEK z CustomUser
        zakladni = getattr(user, 'zakladni_zustatek', Decimal('0'))
        zakladni_zustatek = Decimal(str(zakladni or 0))
        
        # 2. CELKEM OBJEDNÁVEK (TVŮJ MODEL!)
        celkem_objednavek = OrderItem.objects.filter(
            order__user=user  # ← SPRÁVNÉ POLE!
        ).aggregate(
            total=Sum(F('quantity') * F('cena'))
        )['total'] or Decimal('0')
        
        zustatek = zakladni_zustatek - celkem_objednavek
        print(f"💰 DEBUG BALANCE: zakladní={zakladni_zustatek}, objednávky={celkem_objednavek}, ZŮSTATEK={zustatek}")
        
        return zustatek
        
    except Exception as e:
        print(f"⚠️ get_user_balance CHYBA: {e}")
        return Decimal('0')


def can_order_for_date(user, target_date):  # ✅ PARAMETRY OPRACENY!
    """Kontroluje, zda lze objednávat na dané datum podle nastavení uzavírací doby"""
    # Admin může vždy
    if user.is_staff:
        return True, ""
    try:
        settings = OrderClosingTime.objects.first()
        if not settings:
            return True, ""
        closing_date = target_date - timedelta(days=settings.advance_days)
        closing_datetime = timezone.datetime.combine(closing_date, settings.closing_time)
        closing_datetime = timezone.make_aware(closing_datetime, timezone.get_current_timezone())
        return timezone.now() < closing_datetime, ""
    except Exception:
        return True, ""


def check_group_limit(user, menu_item, target_date, quantity):
    """Kontroluje limit objednávek podle skupiny a druhu jídla"""
    # Admin nemá limity
    if user.is_staff:
        return True, ""

    # Najdi uživatelovu skupinu (první skupina)
    user_group = user.groups.first()
    if not user_group:
        return True, ""  # bez skupiny = bez limitu

    # Najdi nastavení limitu pro tuto skupinu + druh jídla
    limit_setting = GroupOrderLimit.objects.filter(
        group=user_group,
        druh_jidla=menu_item.druh_jidla
    ).first()

    if not limit_setting or limit_setting.max_orders_per_day == 0:
        return True, ""  # žádný limit nebo neomezeno

    # Spočítej aktuální objednávky uživatele pro tento druh jídla tento den
    current_orders = OrderItem.objects.filter(
        order__user=user,
        order__datum_vydeje=target_date,
        menu_item__druh_jidla=menu_item.druh_jidla
    ).aggregate(total=Sum('quantity'))['total'] or 0

    # Kontrola limitu
    if current_orders + quantity > limit_setting.max_orders_per_day:
        return False, f"Limit {limit_setting.max_orders_per_day} ks {menu_item.druh_jidla.nazev} za den pro skupinu {user_group.name}!"

    return True, ""


@login_required
def order_create_view(request):  # ✅ BEZ @require_POST
    """Vytvoří/zvýší objednávku"""
    if request.method != 'POST':  # ✅ MANUÁLNÍ KONTROLA
        return JsonResponse({'error': 'POST only'}, status=405)
    
    menu_item_id = request.POST.get('menu_item_id')
    quantity = int(request.POST.get('quantity', 1))
    menu_date_str = request.POST.get('menu_date')

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    def ajax_response(status, message):
        return JsonResponse({'status': status, 'message': message})

    if not menu_date_str:
        msg = "Chybí datum objednávky."
        messages.error(request, msg)
        if is_ajax:
            return ajax_response('error', msg)
        return redirect('jidelnicek:dashboard')  # ✅ NAMESPACE!

    try:
        menu_item = PolozkaJidelnicku.objects.get(id=menu_item_id)
        target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()

        # ✅ KOMPLETNÍ VALIDACE (používá tvé lokální funkce)
        ok, msg = can_order_for_menuitem_date(request.user, menu_item, target_date)
        if not ok:
            messages.error(request, msg)
            if is_ajax:
                return ajax_response('error', msg)
            return redirect('jidelnicek:dashboard')

        ok, msg = check_group_limit(request.user, menu_item, target_date, quantity)
        if not ok:
            messages.error(request, msg)
            if is_ajax:
                return ajax_response('error', msg)
            return redirect('jidelnicek:dashboard')

        cena = OrderValidator.get_price_for_user(request.user, menu_item)
        total_price = cena * quantity

        ok, msg = OrderValidator.check_user_balance(request.user, total_price)
        if not ok:
            messages.error(request, msg)
            if is_ajax:
                return ajax_response('error', msg)
            return redirect('jidelnicek:dashboard')

        # VŠE OK - vytvoř objednávku
        order, created = Order.objects.get_or_create(
            user=request.user, datum_vydeje=target_date, defaults={'status': 'objednano'}
        )

        order_item, item_created = OrderItem.objects.get_or_create(
            order=order, menu_item=menu_item,
            defaults={'quantity': quantity, 'cena': cena}
        )
        if not item_created:
            order_item.quantity += quantity
            order_item.cena = cena
            order_item.save()

        msg_ok = f"Přidáno {order_item.quantity}x {menu_item.jidlo.nazev}"
        messages.success(request, msg_ok)

        if is_ajax:
            return ajax_response('success', msg_ok)

    except PolozkaJidelnicku.DoesNotExist:
        msg = "Položka neexistuje."
        messages.error(request, msg)
        if is_ajax:
            return ajax_response('error', msg)
    except ValueError:
        msg = "Neplatné datum."
        messages.error(request, msg)
        if is_ajax:
            return ajax_response('error', msg)
    except Exception as e:
        msg = f"Chyba: {str(e)}"
        messages.error(request, msg)
        if is_ajax:
            return ajax_response('error', msg)

    if is_ajax:
        return ajax_response('error', "Požadavek nebyl úspěšně dokončen.")
    return redirect('jidelnicek:dashboard')  # ✅ NAMESPACE!


@login_required
def order_delete_view(request):  # ✅ BEZ @require_POST
    """Zruší objednávku"""

    if request.method != 'POST':  # ✅ MANUÁLNÍ KONTROLA
        return JsonResponse({'error': 'POST only'}, status=405)

    menu_item_id = request.POST.get('menu_item_id')
    menu_date_str = request.POST.get('menu_date')

    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

    # DEBUG vstupů z requestu
    print("DEBUG DELETE VIEW: menu_item_id =", menu_item_id,
          "menu_date_str =", menu_date_str,
          "user_id =", request.user.id)

    def ajax_response(status, message, extra=None):
        data = {'status': status, 'message': message}
        if extra:
            data.update(extra)
        return JsonResponse(data)

    if not menu_date_str:
        msg = "Chybí datum objednávky."
        messages.error(request, msg)
        if is_ajax:
            return ajax_response('error', msg)
        return redirect('jidelnicek:dashboard')  # ✅ NAMESPACE!

    try:
        menu_item = PolozkaJidelnicku.objects.get(id=menu_item_id)
        target_date = datetime.strptime(menu_date_str, '%Y-%m-%d').date()

        print(
            "DEBUG DELETE VIEW:",
            "target_date =", target_date,
            "menu_item_id =", menu_item.id,
            "druh_jidla =", menu_item.druh_jidla.nazev,
        )

        # Kontrola času (pro zrušení)
        ok, msg = can_cancel_order_for_menuitem_date(request.user, menu_item, target_date)
        print("DEBUG DELETE VIEW: can_cancel =", ok, "msg =", msg)

        if not ok:
            messages.error(request, msg)
            if is_ajax:
                return ajax_response('error', msg)
            return redirect('jidelnicek:dashboard')

        order = Order.objects.filter(user=request.user, datum_vydeje=target_date).first()
        print("DEBUG DELETE VIEW: order_found =", bool(order))

        if order:
            order_item = OrderItem.objects.filter(order=order, menu_item=menu_item).first()
            print("DEBUG DELETE VIEW: order_item_found =", bool(order_item))

            if order_item:
                deleted_id = order_item.id
                order_item.delete()
                msg = f"Zrušeno {menu_item.jidlo.nazev} na {target_date.strftime('%d.%m.%Y')}"
                messages.success(request, msg)
                print("DEBUG DELETE VIEW: deleted_order_item_id =", deleted_id)

                if not order.items.exists():
                    print("DEBUG DELETE VIEW: no more items in order -> deleting order")
                    order.delete()

                if is_ajax:
                    return ajax_response('success', msg, {'deleted_order_item_id': deleted_id})
            else:
                msg = "Tato položka nebyla objednána."
                messages.warning(request, msg)
                if is_ajax:
                    return ajax_response('warning', msg)
        else:
            msg = "Objednávka neexistuje."
            messages.warning(request, msg)
            if is_ajax:
                return ajax_response('warning', msg)

    except PolozkaJidelnicku.DoesNotExist:
        msg = "Položka neexistuje."
        messages.error(request, msg)
        print("DEBUG DELETE VIEW ERROR: PolozkaJidelnicku.DoesNotExist")
        if is_ajax:
            return ajax_response('error', msg)
    except ValueError:
        msg = "Neplatné datum."
        messages.error(request, msg)
        print("DEBUG DELETE VIEW ERROR: ValueError (bad date)")
        if is_ajax:
            return ajax_response('error', msg)
    except Exception as e:
        msg = "Chyba při rušení objednávky."
        messages.error(request, msg)
        print("DEBUG DELETE VIEW ERROR:", e)
        if is_ajax:
            return ajax_response('error', msg)

    if is_ajax:
        return ajax_response('info', "Požadavek byl zpracován.")
    return redirect('jidelnicek:dashboard')  # ✅ NAMESPACE!
