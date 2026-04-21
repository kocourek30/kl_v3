from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from collections import defaultdict

from .models import Order, OrderItem, PriceRecalculationLog, PriceRecalculationDetail, OrderValidator
from dotace.services import vypocet_dotovane_ceny


MAX_ORDER_QUANTITY = 10
EDITABLE_BULK_ORDER_STATUSES = {"zalozena-obsluhou", "objednano"}


def validate_order_quantity(value, *, max_quantity=MAX_ORDER_QUANTITY):
    try:
        quantity = int(value or 1)
    except (TypeError, ValueError):
        raise ValidationError("Množství musí být celé číslo.")

    if quantity < 1:
        raise ValidationError("Množství musí být alespoň 1.")
    if quantity > max_quantity:
        raise ValidationError(f"Najednou lze objednat maximálně {max_quantity} kusů.")
    return quantity


def _get_recalculated_price(user, menu_item, target_date, quantity, exclude_order_item_id=None):
    return vypocet_dotovane_ceny(
        user,
        menu_item,
        target_date=target_date,
        quantity=quantity,
        exclude_order_item_id=exclude_order_item_id,
    )


def recalculate_order_prices(date_from, date_to, user, dry_run=False):
    """
    Přepočítá ceny všech objednávek v daném období podle aktuálních cen a dotací.
    
    Args:
        date_from: První datum období
        date_to: Poslední datum období
        user: Uživatel, který přepočet provádí (pro audit log)
        dry_run: Pokud True, pouze simuluje a vrací preview změn
    
    Returns:
        dict: Statistiky přepočtu + případně preview změn
    """
    
    # Najdi všechny položky objednávek v daném období
    order_items = (
        OrderItem.objects
        .filter(
            order__datum_vydeje__gte=date_from,
            order__datum_vydeje__lte=date_to,
        )
        .select_related(
            'order__user',
            'menu_item__jidlo',
            'menu_item__druh_jidla',
        )
        .prefetch_related('order__user__groups__dotacni_politika')
        .order_by('order__datum_vydeje', 'order__user__username')
    )
    
    total_items = order_items.count()
    
    if total_items == 0:
        return {
            'success': False,
            'message': 'Žádné objednávky v daném období',
            'items_affected': 0,
            'orders_affected': 0,
            'total_price_diff': Decimal('0')
        }
    
    # Připrav statistiky
    changes = []
    affected_orders = set()
    total_price_diff = Decimal('0')
    items_changed = 0
    items_unchanged = 0
    # Projdi všechny položky
    for item in order_items:
        old_price = item.cena
        new_price = _get_recalculated_price(
            item.order.user,
            item.menu_item,
            item.order.datum_vydeje,
            item.quantity,
            exclude_order_item_id=item.pk,
        )
        price_diff = new_price - old_price
        
        if abs(price_diff) >= Decimal('0.01'):  # Změna alespoň 1 haléř
            items_changed += 1
            affected_orders.add(item.order.id)
            total_price_diff += price_diff * item.quantity
            
            changes.append({
                'order_item': item,
                'old_price': old_price,
                'new_price': new_price,
                'price_diff': price_diff,
                'quantity': item.quantity,
                'total_diff': price_diff * item.quantity,
                'user': item.order.user,
                'date': item.order.datum_vydeje,
                'menu_item_name': item.menu_item.jidlo.nazev,
            })
        else:
            items_unchanged += 1
    
    # Pokud je dry_run, vrať pouze preview
    if dry_run:
        return {
            'success': True,
            'dry_run': True,
            'items_total': total_items,
            'items_changed': items_changed,
            'items_unchanged': items_unchanged,
            'orders_affected': len(affected_orders),
            'total_price_diff': total_price_diff,
            'changes': changes,  # Seznam všech změn pro preview
        }
    
    # Proveď skutečný přepočet v transakci
    with transaction.atomic():
        # Vytvoř audit log
        log = PriceRecalculationLog.objects.create(
            created_by=user,
            date_from=date_from,
            date_to=date_to,
            orders_affected=len(affected_orders),
            items_affected=items_changed,
            total_price_diff=total_price_diff,
            note=f"Přepočet cen pro období {date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}"
        )
        
        details_to_create = []
        items_to_update = []
        for change in changes:
            item = change['order_item']
            details_to_create.append(
                PriceRecalculationDetail(
                    log=log,
                    order_item=item,
                    old_price=change['old_price'],
                    new_price=change['new_price'],
                    price_diff=change['price_diff'],
                )
            )

            item.cena = change['new_price']
            items_to_update.append(item)


        PriceRecalculationDetail.objects.bulk_create(details_to_create, batch_size=500)
        OrderItem.objects.bulk_update(items_to_update, ['cena'], batch_size=500)
    
    return {
        'success': True,
        'dry_run': False,
        'log_id': log.id,
        'items_total': total_items,
        'items_changed': items_changed,
        'items_unchanged': items_unchanged,
        'orders_affected': len(affected_orders),
        'total_price_diff': total_price_diff,
        'message': f'Úspěšně přepočteno {items_changed} položek v {len(affected_orders)} objednávkách'
    }


def get_recalculation_summary_by_user(changes):
    """Seskupí změny podle uživatelů pro přehlednější zobrazení"""
    by_user = defaultdict(lambda: {
        'items': [],
        'total_diff': Decimal('0'),
        'items_count': 0
    })
    
    for change in changes:
        user = change['user']
        by_user[user]['items'].append(change)
        by_user[user]['total_diff'] += change['total_diff']
        by_user[user]['items_count'] += 1
    
    return dict(by_user)


def build_bulk_order_plan(datum_vydeje, menu_items, users):
    """
    Připraví náhled hromadného založení objednávek bez zápisu do DB.

    Admin nástroj záměrně neřeší uzávěrku objednávek, ale nesmí tiše přepsat
    už vydané/uzavřené objednávky a má respektovat konto uživatele.
    """
    menu_items = list(menu_items)
    users = list(users)
    existing_orders = {
        order.user_id: order
        for order in (
            Order.objects
            .filter(user__in=users, datum_vydeje=datum_vydeje)
            .prefetch_related("items")
        )
    }

    plan = []
    summary = {
        "create": 0,
        "replace": 0,
        "skip_status": 0,
        "skip_balance": 0,
        "skipped": 0,
    }

    for user in users:
        existing_order = existing_orders.get(user.pk)
        existing_items = existing_order.items.count() if existing_order else 0
        total_price = sum(
            (
                OrderValidator.get_price_for_user(
                    user,
                    menu_item,
                    target_date=datum_vydeje,
                    quantity=1,
                ) or Decimal("0")
            )
            for menu_item in menu_items
        )

        entry = {
            "user": user,
            "existing_order": existing_order,
            "existing_items": existing_items,
            "menu_items_count": len(menu_items),
            "total_price": total_price,
            "action": "create",
            "reason": "",
        }

        if existing_order:
            if existing_order.status not in EDITABLE_BULK_ORDER_STATUSES:
                entry["action"] = "skip_status"
                entry["reason"] = f"Existující objednávka má stav „{existing_order.get_status_display()}“."
            else:
                entry["action"] = "replace"
                entry["reason"] = "Existující rozpracovaná objednávka bude nahrazena novým obsahem."

        if entry["action"] in {"create", "replace"}:
            ok_balance, balance_reason = OrderValidator.check_user_balance(user, total_price)
            if not ok_balance:
                entry["action"] = "skip_balance"
                if balance_reason == "insufficient_balance":
                    entry["reason"] = "Nedostatečný zůstatek."
                elif balance_reason == "debit_limit_exceeded":
                    entry["reason"] = "Překročen debetní limit."
                else:
                    entry["reason"] = "Objednávku nelze vytvořit kvůli nastavení konta."

        if entry["action"].startswith("skip"):
            summary["skipped"] += 1
        summary[entry["action"]] += 1
        plan.append(entry)

    return {"entries": plan, "summary": summary}


def apply_bulk_order_plan(datum_vydeje, menu_items, plan_entries):
    """
    Provede pouze položky, které v náhledu vyšly jako create/replace.
    """
    menu_items = list(menu_items)
    created = 0
    replaced = 0

    with transaction.atomic():
        for entry in plan_entries:
            if entry["action"] not in {"create", "replace"}:
                continue

            user = entry["user"]
            order, order_created = Order.objects.select_for_update().get_or_create(
                user=user,
                datum_vydeje=datum_vydeje,
                defaults={"status": "zalozena-obsluhou"},
            )

            if not order_created and order.status not in EDITABLE_BULK_ORDER_STATUSES:
                # Ochrana proti změně stavu mezi preview a potvrzením.
                continue

            order.status = "zalozena-obsluhou"
            order.save(update_fields=["status"])
            order.items.all().delete()

            OrderItem.objects.bulk_create(
                [
                    OrderItem(
                        order=order,
                        menu_item=menu_item,
                        quantity=1,
                        cena=OrderValidator.get_price_for_user(
                            user,
                            menu_item,
                            target_date=datum_vydeje,
                            quantity=1,
                        ),
                    )
                    for menu_item in menu_items
                ]
            )

            if order_created:
                created += 1
            else:
                replaced += 1

    return {"created": created, "replaced": replaced}
