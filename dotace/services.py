from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import F, Sum

from .models import DotaceProJidelniskouSkupinu, DotacniPolitika


AKTIVNI_STAVY_OBJEDNAVEK = [
    "zalozena-obsluhou",
    "objednano",
    "castecne-vydano",
    "vydano",
    "nevyzvednuto",
]


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _first_group_policy(user):
    for group in user.groups.all():
        try:
            return group.dotacni_politika
        except DotacniPolitika.DoesNotExist:
            continue
    return None


def _override_for_menu_item(policy, menu_item):
    return (
        DotaceProJidelniskouSkupinu.objects
        .filter(
            dotacni_politika=policy,
            jidelniskova_skupina=menu_item.druh_jidla,
        )
        .first()
    )


def _discount_setup(policy, menu_item):
    override = _override_for_menu_item(policy, menu_item)
    procento = override.procento if override and override.procento is not None else policy.procento
    castka = override.castka if override and override.castka is not None else policy.castka
    return Decimal(str(procento or 0)), Decimal(str(castka or 0))


def _count_limits(policy, menu_item):
    override = _override_for_menu_item(policy, menu_item)
    denni_limit = override.denni_limit if override and override.denni_limit is not None else policy.denni_limit
    mesicni_limit = override.mesicni_limit if override and override.mesicni_limit is not None else policy.mesicni_limit
    return denni_limit, mesicni_limit


def _raw_discount_per_portion(base_price, policy, menu_item):
    procento, castka = _discount_setup(policy, menu_item)
    discount = Decimal("0")
    if procento > 0:
        discount += base_price * procento / Decimal("100")
    if castka > 0:
        discount += castka
    return min(base_price, _money(discount))


def _month_bounds(target_date):
    last_day = monthrange(target_date.year, target_date.month)[1]
    return target_date.replace(day=1), target_date.replace(day=last_day)


def _used_subsidy(user, date_from, date_to=None, exclude_order_item_id=None, druh_jidla_id=None):
    from objednavky.models import OrderItem

    filters = {
        "order__user": user,
        "order__status__in": AKTIVNI_STAVY_OBJEDNAVEK,
        "cena__lt": F("menu_item__jidlo__cena"),
    }
    if druh_jidla_id:
        filters["menu_item__druh_jidla_id"] = druh_jidla_id
    if date_to is None:
        filters["order__datum_vydeje"] = date_from
    else:
        filters["order__datum_vydeje__gte"] = date_from
        filters["order__datum_vydeje__lte"] = date_to

    qs = OrderItem.objects.filter(**filters)
    if exclude_order_item_id:
        qs = qs.exclude(pk=exclude_order_item_id)

    rows = qs.aggregate(
        porce=Sum("quantity"),
        castka=Sum((F("menu_item__jidlo__cena") - F("cena")) * F("quantity")),
    )
    return {
        "porce": int(rows["porce"] or 0),
        "castka": _money(rows["castka"] or 0),
    }


def _fits_count_limit(limit, used, requested):
    if limit in (None, 0):
        return True
    return int(used) + int(requested) <= int(limit)


def _fits_amount_limit(limit, used, requested_amount):
    limit = _money(limit)
    if limit <= 0:
        return True
    return used + requested_amount <= limit


def vypocet_dotovane_ceny(user, menu_item, target_date=None, quantity=1, exclude_order_item_id=None):
    """
    Vrátí jednotkovou cenu po dotaci.

    Dotace se nerozpočítává na zbytek položky. Buď se dotuje celá objednaná
    položka, nebo se při vyčerpaném limitu účtuje za plnou cenu.
    """
    quantity = max(1, int(quantity or 1))
    base_price = _money(getattr(menu_item.jidlo, "cena", 0))
    if base_price <= 0:
        return Decimal("0.00")

    policy = _first_group_policy(user)
    if not policy:
        return base_price

    discount_per_portion = _raw_discount_per_portion(base_price, policy, menu_item)
    if discount_per_portion <= 0:
        return base_price

    requested_discount = discount_per_portion * quantity

    if target_date:
        if not isinstance(target_date, date):
            raise TypeError("target_date musí být typu datetime.date.")

        denni_limit, mesicni_limit = _count_limits(policy, menu_item)
        day_used_all = _used_subsidy(user, target_date, exclude_order_item_id=exclude_order_item_id)
        day_used_type = _used_subsidy(
            user,
            target_date,
            exclude_order_item_id=exclude_order_item_id,
            druh_jidla_id=menu_item.druh_jidla_id,
        )
        month_start, month_end = _month_bounds(target_date)
        month_used_all = _used_subsidy(
            user,
            month_start,
            month_end,
            exclude_order_item_id=exclude_order_item_id,
        )
        month_used_type = _used_subsidy(
            user,
            month_start,
            month_end,
            exclude_order_item_id=exclude_order_item_id,
            druh_jidla_id=menu_item.druh_jidla_id,
        )

        if not _fits_count_limit(policy.denni_limit, day_used_all["porce"], quantity):
            return base_price
        if not _fits_count_limit(policy.mesicni_limit, month_used_all["porce"], quantity):
            return base_price
        if not _fits_count_limit(denni_limit, day_used_type["porce"], quantity):
            return base_price
        if not _fits_count_limit(mesicni_limit, month_used_type["porce"], quantity):
            return base_price
        if not _fits_amount_limit(policy.denni_limit_castka, day_used_all["castka"], requested_discount):
            return base_price
        if not _fits_amount_limit(policy.mesicni_limit_castka, month_used_all["castka"], requested_discount):
            return base_price

    return _money(base_price - discount_per_portion)


def ma_pocetni_limit_dotace(user, menu_item):
    policy = _first_group_policy(user)
    if not policy:
        return False
    denni_limit, mesicni_limit = _count_limits(policy, menu_item)
    return any(limit not in (None, 0) for limit in (policy.denni_limit, policy.mesicni_limit, denni_limit, mesicni_limit))
