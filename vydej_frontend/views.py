import logging
import secrets
from collections import defaultdict

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import date
import json
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from objednavky.models import Order, OrderItem
from canteen_settings.models import MealPickupTime
from jidelnicek.models import PolozkaJidelnicku
from django.db.models import Sum
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.conf import settings

from .decorators import obsluha_required
from .services import aktualni_druhy_jidel_ids, vydat_objednavku, vydat_polozku

User = get_user_model()
logger = logging.getLogger(__name__)

from vydej.models import VydejSettings


ACTIVE_ORDER_STATUSES = ['objednano', 'zalozena-obsluhou', 'castecne-vydano']
LOCAL_RFID_CLIENTS = {"127.0.0.1", "::1", "localhost"}


@login_required
@obsluha_required
def get_vydej_settings(request):
    settings, created = VydejSettings.objects.get_or_create()
    return JsonResponse({
        "timeout_seconds": settings.timeout_seconds
    })


def _rfid_token_ok(request, data=None, require_configured=False):
    expected = getattr(settings, "RFID_API_TOKEN", "")
    if not expected:
        return not require_configured
    supplied = (
        request.headers.get("X-RFID-Token")
        or request.headers.get("X-API-Key")
        or (data or {}).get("token")
        or ""
    )
    return secrets.compare_digest(str(supplied), str(expected))


def _rfid_request_allowed(request, data=None):
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        return True

    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    if remote_addr not in LOCAL_RFID_CLIENTS:
        return False

    return _rfid_token_ok(request, data, require_configured=True)


def get_current_meal_type_ids():
    """Vrátí ID druhů jídel s aktuálním výdejním časem"""
    return aktualni_druhy_jidel_ids()


def prepare_order_with_items(order, current_meal_type_ids):
    """Připraví objednávku s rozdělením položek"""
    all_items = order.items.select_related('menu_item__jidlo', 'menu_item__druh_jidla').all()
    
    return {
        'order': order,
        'current_items': [item for item in all_items 
                         if not item.vydano and item.menu_item.druh_jidla_id in current_meal_type_ids],
        'issued_items': [item for item in all_items if item.vydano],
        'pending_items': [item for item in all_items if not item.vydano],
        'has_other_items': any(item for item in all_items 
                              if not item.vydano and item.menu_item.druh_jidla_id not in current_meal_type_ids)
    }


def get_current_meal_types_with_counts(today, current_meal_type_ids):
    """Vrátí aktuálně vydávané druhy jídel s počty"""
    now = timezone.localtime(timezone.now()).time()
    
    active_pickup_times = MealPickupTime.objects.filter(
        pickup_from__lte=now,
        pickup_to__gte=now
    ).select_related('druh_jidla').order_by('druh_jidla__poradi', 'druh_jidla__nazev')
    
    pickup_meal_type_ids = [p.druh_jidla_id for p in active_pickup_times]
    if not pickup_meal_type_ids:
        return active_pickup_times

    menu_items = list(
        PolozkaJidelnicku.objects.filter(
            druh_jidla_id__in=pickup_meal_type_ids,
            jidelnicek__platnost_od__lte=today,
            jidelnicek__platnost_do__gte=today,
        )
        .select_related("jidlo", "druh_jidla")
        .order_by("druh_jidla__poradi", "druh_jidla__nazev", "jidlo__nazev")
    )
    menu_item_ids = [m.id for m in menu_items]
    if not menu_item_ids:
        for pickup in active_pickup_times:
            pickup.meals_with_counts = []
        return active_pickup_times

    count_by_menu_item_id = {
        row["menu_item_id"]: row["total"]
        for row in OrderItem.objects.filter(
            order__datum_vydeje=today,
            order__status__in=ACTIVE_ORDER_STATUSES,
            menu_item_id__in=menu_item_ids,
            vydano=False,
        )
        .values("menu_item_id")
        .annotate(total=Sum("quantity"))
    }

    meals_by_type = defaultdict(list)
    for menu_item in menu_items:
        count = count_by_menu_item_id.get(menu_item.id, 0) or 0
        if count <= 0:
            continue
        meals_by_type[menu_item.druh_jidla_id].append(
            {
                "menu_item": menu_item,
                "count": count,
            }
        )

    for pickup in active_pickup_times:
        pickup.meals_with_counts = meals_by_type.get(pickup.druh_jidla_id, [])
    
    return active_pickup_times


@login_required
@obsluha_required
def dashboard(request):
    today = date.today()
    current_meal_type_ids = get_current_meal_type_ids()
    
    # Pending orders (k výdeji)
    pending_orders_qs = Order.objects.filter(
        datum_vydeje=today,
        status__in=ACTIVE_ORDER_STATUSES
    ).select_related('user').prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    ).order_by('user__last_name', 'user__first_name')
    
    pending_orders = []
    for order in pending_orders_qs:
        prepared = prepare_order_with_items(order, current_meal_type_ids)
        if prepared['current_items']:
            pending_orders.append(prepared)
    
    # Completed orders (vydané dnes)
    completed_orders_qs = Order.objects.filter(
        datum_vydeje=today,
        status__in=['vydano', 'castecne-vydano']
    ).exclude(
        datum_vydani__isnull=True
    ).select_related('user').prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    ).order_by('-datum_vydani')[:20]
    
    completed_orders = []
    for order in completed_orders_qs:
        prepared = prepare_order_with_items(order, current_meal_type_ids)
        if prepared['issued_items']:
            completed_orders.append(prepared)
    
    # Aktuální výdejní časy
    meal_types = get_current_meal_types_with_counts(today, current_meal_type_ids)
    
    # Statistiky položek k výdeji POUZE pro aktuální výdejní časy
    pending_items = OrderItem.objects.filter(
        order__datum_vydeje=today,
        order__status__in=ACTIVE_ORDER_STATUSES,
        vydano=False,
        menu_item__druh_jidla_id__in=current_meal_type_ids
    ).select_related(
        'menu_item__jidlo', 'menu_item__druh_jidla'
    ).values(
        'menu_item__jidlo__nazev',
        'menu_item__druh_jidla__nazev'
    ).annotate(
        total_quantity=Sum('quantity')
    ).order_by(
        'menu_item__druh_jidla__poradi',
        'menu_item__druh_jidla__nazev',
        'menu_item__jidlo__nazev',
    )
    
    context = {
        'today': today,
        'pending_orders': pending_orders,
        'completed_orders': completed_orders,
        'meal_types': meal_types,
        'current_meal_type_ids': current_meal_type_ids,
        'pending_items': pending_items,
        'pending_count': len(pending_orders),
        'completed_count': len(completed_orders),
    }
    
    return render(request, 'vydej_frontend/dashboard.html', context)


@login_required
@obsluha_required
@csrf_protect
@require_POST
def issue_order(request, order_id):
    """
    AJAX endpoint pro vydání AKTUÁLNÍCH položek objednávky.
    Vždy vrací JSON payload (success / error).
    """
    try:
        result = vydat_objednavku(order_id, request.user)
        order = result["order"]

        return JsonResponse({
            'success': True,
            'message': f'Vydáno pro {order.user.get_full_name()}: {", ".join(result["vydane_polozky"])}',
            'uctenka_id': result["uctenka"].id,
            'partial': result["partial"],
        })

    except Order.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Objednávka nenalezena',
        }, status=404)

    except ValidationError as exc:
        return JsonResponse({
            'success': False,
            'error': exc.messages[0],
        }, status=400)

    except Exception:
        logger.exception('Chyba při vydávání objednávky %s.', order_id)

        return JsonResponse({
            'success': False,
            'error': 'Chyba při vytváření účtenky.',
        }, status=500)


@login_required
@obsluha_required
def refresh_data(request):
    """AJAX endpoint pro refresh dashboard dat"""
    today = date.today()
    current_meal_type_ids = get_current_meal_type_ids()
    
    pending_orders_qs = Order.objects.filter(
        datum_vydeje=today,
        status__in=ACTIVE_ORDER_STATUSES
    ).select_related('user').prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    ).order_by('user__last_name', 'user__first_name')
    
    pending_orders = []
    for order in pending_orders_qs:
        prepared = prepare_order_with_items(order, current_meal_type_ids)
        if prepared['current_items']:
            pending_orders.append(prepared)
    
    completed_orders_qs = Order.objects.filter(
        datum_vydeje=today,
        status__in=['vydano', 'castecne-vydano']
    ).exclude(
        datum_vydani__isnull=True
    ).select_related('user').prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    ).order_by('-datum_vydani')[:20]
    
    completed_orders = []
    for order in completed_orders_qs:
        prepared = prepare_order_with_items(order, current_meal_type_ids)
        if prepared['issued_items']:
            completed_orders.append(prepared)
    
    pending_items = OrderItem.objects.filter(
        order__datum_vydeje=today,
        order__status__in=ACTIVE_ORDER_STATUSES,
        vydano=False,
        menu_item__druh_jidla_id__in=current_meal_type_ids
    ).select_related(
        'menu_item__jidlo', 'menu_item__druh_jidla'
    ).values(
        'menu_item__jidlo__nazev',
        'menu_item__druh_jidla__nazev'
    ).annotate(
        total_quantity=Sum('quantity')
    ).order_by(
        'menu_item__druh_jidla__poradi',
        'menu_item__druh_jidla__nazev',
        'menu_item__jidlo__nazev',
    )
    
    pending_html = render_to_string('vydej_frontend/partials/pending_orders.html', {
        'pending_orders': pending_orders
    })
    
    completed_html = render_to_string('vydej_frontend/partials/completed_orders.html', {
        'completed_orders': completed_orders
    })
    
    recent_html = render_to_string('vydej_frontend/partials/recent_orders.html', {
        'completed_orders': completed_orders
    })
    
    summary_html = render_to_string('vydej_frontend/partials/summary_footer.html', {
        'pending_items': pending_items
    })
    
    return JsonResponse({
        'success': True,
        'rfid_ready': True,
        'pending_count': len(pending_orders),
        'completed_count': len(completed_orders),
        'pending_orders_html': pending_html,
        'completed_orders_html': completed_html,
        'recent_orders_html': recent_html,
        'summary_html': summary_html
    })


@csrf_exempt
@require_POST
def rfid_scan(request):
    """Najde objednávku podle RFID - zobrazí i už vydané"""
    try:
        data = json.loads(request.body)
        if not _rfid_request_allowed(request, data):
            logger.warning(
                "Odmítnutý RFID scan (remote=%s, user=%s).",
                request.META.get("REMOTE_ADDR"),
                getattr(getattr(request, "user", None), "username", None),
            )
            return JsonResponse({'success': False, 'error': 'Neplatné oprávnění RFID terminálu.'}, status=403)

        rfid_tag = data.get('rfid_tag', '').strip()
        
        if not rfid_tag:
            return JsonResponse({'success': False, 'error': 'Žádný RFID tag'})
        
        # Najdi uživatele podle ISIC karty nebo ISIC mobilu
        try:
            user = User.objects.get(
                Q(identifikacni_medium__iexact=rfid_tag) | Q(identifikacni_medium_mobil__iexact=rfid_tag)
            )
        except User.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Uživatel s kartou {rfid_tag} nenalezen v systému'
            })
        except User.MultipleObjectsReturned:
            return JsonResponse({
                'success': False,
                'error': 'RFID je přiřazeno více uživatelům. Zkontrolujte ISIC kartu/mobil v administraci.'
            })
        
        # Najdi dnešní objednávku
        today = date.today()
        
        # 🔥 NEJDŘÍV ZKUS NAJÍT NEVYDANOU
        order = Order.objects.filter(
            user=user,
            datum_vydeje=today,
            status__in=ACTIVE_ORDER_STATUSES
        ).select_related('user').prefetch_related(
            'items__menu_item__jidlo',
            'items__menu_item__druh_jidla'
        ).order_by('-created_at').first()
        
        # Získej aktuální výdejní časy
        now = timezone.localtime(timezone.now()).time()
        current_meal_type_ids = list(MealPickupTime.objects.filter(
            pickup_from__lte=now,
            pickup_to__gte=now
        ).values_list('druh_jidla_id', flat=True))
        
        if order:
            # Má nevydanou objednávku
            if not current_meal_type_ids:
                return JsonResponse({
                    'success': False,
                    'error': f'Nyní není žádný výdejní čas pro {user.get_full_name()}. Zkuste později.'
                })
            
            # Zkontroluj, zda jsou položky k vydání
            pending_items = order.items.filter(
                vydano=False,
                menu_item__druh_jidla_id__in=current_meal_type_ids
            )
            
            if not pending_items.exists():
                return JsonResponse({
                    'success': False,
                    'error': f'Žádné položky k vydání v aktuálním čase pro {user.get_full_name()}'
                })
            
            # ✅ ÚSPĚCH - Vrať informaci o objednávce
            return JsonResponse({
                'success': True,
                'order_id': order.id,
                'user_name': user.get_full_name(),
                'already_issued': False
            })
        
        # 🔥 POKUD NENÍ NEVYDANÁ, NAJDI UŽ VYDANOU
        issued_order = Order.objects.filter(
            user=user,
            datum_vydeje=today,
            status='vydano'
        ).select_related('user').prefetch_related(
            'items__menu_item__jidlo',
            'items__menu_item__druh_jidla'
        ).order_by('-datum_vydani').first()
        
        if issued_order:
            # ✅ Našli jsme už vydanou objednávku
            return JsonResponse({
                'success': True,
                'order_id': issued_order.id,
                'user_name': user.get_full_name(),
                'already_issued': True  # 🔥 KLÍČOVÝ FLAG
            })
        
        # Žádná objednávka nenalezena
        return JsonResponse({
            'success': False,
            'error': f'Žádná objednávka pro {user.get_full_name()} na dnes ({today.strftime("%d.%m.%Y")})'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Neplatný JSON formát'})
    except Exception:
        logger.exception('Chyba při RFID scan.')
        return JsonResponse({'success': False, 'error': 'Systémová chyba.'}, status=500)


@login_required
@obsluha_required
@require_POST
def rfid_debug(request):
    """Debug endpoint pro testování RFID"""
    data = json.loads(request.body)
    if not _rfid_token_ok(request, data):
        logger.warning("Odmítnutý RFID debug kvůli neplatnému tokenu.")
        return JsonResponse({'success': False, 'error': 'Neplatné oprávnění RFID terminálu.'}, status=403)

    rfid = data.get('rfid_tag', '').strip()

    user = User.objects.filter(
        Q(identifikacni_medium__iexact=rfid) | Q(identifikacni_medium_mobil__iexact=rfid)
    ).first()

    return JsonResponse({
        'input_rfid': rfid,
        'found': bool(user),
        'user': f"{user.first_name} {user.last_name}" if user else None,
    })

@login_required
@obsluha_required
def get_order_detail(request, order_id):
    """Vrátí detail objednávky pro RFID tab"""
    try:
        order = Order.objects.select_related('user').prefetch_related(
            'items__menu_item__jidlo',
            'items__menu_item__druh_jidla'
        ).get(id=order_id)
        
        # Získej aktuální výdejní časy
        now = timezone.localtime(timezone.now()).time()
        current_meal_type_ids = list(MealPickupTime.objects.filter(
            pickup_from__lte=now,
            pickup_to__gte=now
        ).values_list('druh_jidla_id', flat=True))
        
        # 🔥 KONTROLA - JE UŽ OBJEDNÁVKA VYDANÁ?
        already_issued = order.status == 'vydano'
        
        # 🔥 SESKUPENÍ POLOŽEK PODLE NÁZVU JÍDLA
        from collections import defaultdict
        
        grouped_items = defaultdict(lambda: {
            'quantity': 0,
            'type': '',
            'issued': False,
            'issued_times': [],
            'item_ids': []
        })
        
        if already_issued:
            # Seskup UŽ VYDANÉ položky
            for item in order.items.filter(vydano=True):
                key = item.menu_item.jidlo.nazev
                grouped_items[key]['quantity'] += item.quantity
                grouped_items[key]['type'] = item.menu_item.druh_jidla.nazev
                grouped_items[key]['issued'] = True
                grouped_items[key]['item_ids'].append(item.id)
                if item.datum_vydani:
                    grouped_items[key]['issued_times'].append(item.datum_vydani.strftime('%H:%M:%S'))
        else:
            # Seskup položky K VYDÁNÍ
            for item in order.items.filter(vydano=False, menu_item__druh_jidla_id__in=current_meal_type_ids):
                key = item.menu_item.jidlo.nazev
                grouped_items[key]['quantity'] += item.quantity
                grouped_items[key]['type'] = item.menu_item.druh_jidla.nazev
                grouped_items[key]['issued'] = False
                grouped_items[key]['item_ids'].append(item.id)
        
        # Převeď na seznam
        items = []
        for name, data in grouped_items.items():
            items.append({
                'name': name,
                'quantity': data['quantity'],
                'type': data['type'],
                'issued': data['issued'],
                'issued_time': data['issued_times'][0] if data['issued_times'] else None,
                'item_ids': data['item_ids']
            })
        
        # Seřaď podle typu jídla
        items.sort(key=lambda x: x['type'])
        
        return JsonResponse({
            'success': True,
            'order_id': order.id,
            'user_name': order.user.get_full_name(),
            'order_date': order.datum_vydeje.strftime('%d.%m.%Y'),
            'issued_time': order.datum_vydani.strftime('%H:%M:%S') if order.datum_vydani else None,
            'items': items,
            'already_issued': already_issued
        })
        
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Objednávka nenalezena'}, status=404)

@login_required
@obsluha_required
@require_POST
def issue_single_item(request, item_id):
    """AJAX endpoint pro vydání JEDNÉ položky objednávky"""
    try:
        result = vydat_polozku(item_id, request.user)
        
        return JsonResponse({
            'success': True,
            'message': f'Vydáno: {", ".join(result["vydane_polozky"])}',
            'uctenka_id': result["uctenka"].id,
            'order_complete': result["order_complete"],
        })
        
    except OrderItem.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Položka nenalezena'
        }, status=404)
    except ValidationError as exc:
        return JsonResponse({
            'success': False,
            'error': exc.messages[0],
        }, status=400)
    except Exception:
        logger.exception('Chyba při vydávání položky %s.', item_id)
        return JsonResponse({
            'success': False,
            'error': 'Chyba při vytváření účtenky.'
        }, status=500)


from django.shortcuts import redirect
from django.contrib.auth import login
from django.views.decorators.http import require_http_methods

@require_http_methods(["GET"])
def auto_login_kiosk(request):
    """Automatické přihlášení pro výdejní terminál"""
    if not getattr(settings, "DEBUG", False) and not getattr(settings, "KIOSK_AUTO_LOGIN_ENABLED", False):
        return redirect('admin:login')

    if request.user.is_authenticated:
        return redirect('vydej_frontend:dashboard')
    
    # Najdi uživatele "obsluha" nebo prvního staff uživatele
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Zkus najít uživatele s username "obsluha" nebo "vydej"
        kiosk_user = User.objects.filter(
            username__in=['obsluha', 'vydej', 'kuchyne'],
            is_staff=True
        ).first()
        
        if not kiosk_user:
            # Fallback - první staff user
            kiosk_user = User.objects.filter(is_staff=True).first()
        
        if kiosk_user:
            # Automaticky přihlas
            login(request, kiosk_user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('vydej_frontend:dashboard')
        else:
            # Žádný vhodný uživatel
            return redirect('admin:login')
            
    except Exception:
        logger.exception("Chyba při automatickém přihlášení výdejního terminálu.")
        return redirect('admin:login')
