from django.urls import path
from django.contrib import admin
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction, models
from django.db.models import Max, Prefetch, Sum
from django.http import JsonResponse
from decimal import Decimal
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.utils.html import format_html
from django.utils import timezone
from datetime import date, timedelta

from .models import (
    Order, OrderItem, UserRFID, 
    PriceRecalculationLog, PriceRecalculationDetail
)
from .services import (
    apply_bulk_order_plan,
    build_bulk_order_plan,
    recalculate_order_prices,
    get_recalculation_summary_by_user,
)
from jidelnicek.models import Jidelnicek, PolozkaJidelnicku
from dotace.models import DotaceProJidelniskouSkupinu
from users.group_utils import get_first_group_setting, get_primary_effective_group

User = get_user_model()


def get_cena_for_user_and_item(user, menu_item):
    skupina = get_primary_effective_group(user)
    if not skupina:
        return menu_item.jidlo.cena
    politika = getattr(skupina, 'dotacni_politika', None)
    if not politika:
        return menu_item.jidlo.cena
    prepis = DotaceProJidelniskouSkupinu.objects.filter(
        dotacni_politika=politika,
        jidelniskova_skupina=menu_item.druh_jidla
    ).first()
    procento = prepis.procento if prepis and prepis.procento is not None else politika.procento
    castka = prepis.castka if prepis and prepis.castka is not None else politika.castka
    base_price = menu_item.jidlo.cena
    snizena_cena = base_price
    if procento and procento != Decimal('0'):
        snizena_cena = base_price * (Decimal('1') - Decimal(procento) / Decimal('100'))
    if castka and castka != Decimal('0'):
        snizena_cena = max(Decimal('0'), snizena_cena - Decimal(castka))
    return snizena_cena.quantize(Decimal('0.01'))


class BulkOrderForm(forms.Form):
    datum_vydeje = forms.DateField(
        label="Datum výdeje",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d']
    )
    menu_items = forms.ModelMultipleChoiceField(
        queryset=PolozkaJidelnicku.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Položky jídelníčku"
    )
    skupina = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        required=False,
        label="Skupina zákazníků",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    uzivatele = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        label="Uživatelé"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        datum = self.data.get('datum_vydeje') or self.initial.get('datum_vydeje')
        if datum:
            from datetime import datetime
            try:
                datum_obj = datetime.strptime(datum, '%Y-%m-%d').date()
                self.fields['menu_items'].queryset = PolozkaJidelnicku.objects.filter(
                    jidelnicek__platnost_od__lte=datum_obj,
                    jidelnicek__platnost_do__gte=datum_obj
                )
            except Exception:
                self.fields['menu_items'].queryset = PolozkaJidelnicku.objects.none()
        else:
            self.fields['menu_items'].queryset = PolozkaJidelnicku.objects.none()

        group_id = self.data.get('skupina') or self.initial.get('skupina')
        if group_id:
            try:
                group_id = int(group_id)
                self.fields['uzivatele'].queryset = User.objects.filter(
                    groups__id=group_id,
                    is_active=True
                )
            except Exception:
                self.fields['uzivatele'].queryset = User.objects.filter(is_active=True)
        else:
            self.fields['uzivatele'].queryset = User.objects.filter(is_active=True)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    change_list_template = 'admin/objednavky/order_change_list.html'
    
    list_display = (
        'created_at_formatted',
        'osobni_cislo',
        'jmeno',
        'prijmeni',
        'formatted_datum',
        'status',
        'total_items',
        'show_items',
    )
    list_display_links = None

    def has_add_permission(self, request):
        return False    

    list_filter = ('status', 'datum_vydeje', 'user', 'created_at')
    search_fields = [
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__osobni_cislo',
    ]
    list_select_related = ('user',)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('user')
            .prefetch_related(
                Prefetch(
                    'items',
                    queryset=OrderItem.objects.select_related('menu_item__jidlo'),
                )
            )
            .annotate(total_quantity_sum=Sum('items__quantity'))
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('bulk_create/', self.admin_site.admin_view(self.bulk_create_view), name='bulk_create_orders'),
            path('api/menu_items/', self.admin_site.admin_view(self.menu_items_api), name='menu_items_api'),
            path('api/jidelnicek_days/', self.admin_site.admin_view(self.jidelnicek_days_api), name='jidelnicek_days_api'),
            path('api/zákazníci/', self.admin_site.admin_view(self.users_api), name='users_api'),
            path(
                'price-recalculation/',
                self.admin_site.admin_view(self.price_recalculation_view),
                name='objednavky_order_price_recalculation',
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Datum vytvoření", ordering='created_at')
    def created_at_formatted(self, obj):
        if getattr(obj, 'created_at', None):
            return obj.created_at.strftime('%d.%m.%Y %H:%M')
        return '-'

    @admin.display(description="Osobní číslo", ordering='user__osobni_cislo')
    def osobni_cislo(self, obj):
        return getattr(obj.user, 'osobni_cislo', '') or '-'

    @admin.display(description="Jméno", ordering='user__first_name')
    def jmeno(self, obj):
        return obj.user.first_name or '-'

    @admin.display(description="Příjmení", ordering='user__last_name')
    def prijmeni(self, obj):
        return obj.user.last_name or '-'

    @admin.display(description="Datum výdeje", ordering='datum_vydeje')
    def formatted_datum(self, obj):
        return obj.datum_vydeje.strftime('%d.%m.%Y')

    @admin.display(description="Počet položek", ordering='total_quantity_sum')
    def total_items(self, obj):
        if getattr(obj, 'total_quantity_sum', None) is not None:
            return obj.total_quantity_sum
        return obj.total_quantity()

    @admin.display(description="Položky objednávky a cena")
    def show_items(self, obj):
        items = list(obj.items.all())[:3]
        return ", ".join(
            f"{item.menu_item.jidlo.nazev} x{item.quantity} ({item.cena} Kč)"
            for item in items
        )

    @admin.display(description="Zákazník")
    def user_info(self, obj):
        user = obj.user
        return f"{getattr(user, 'osobni_cislo', '') or ''} - {user.first_name} {user.last_name}"

    def bulk_create_view(self, request):
        form = BulkOrderForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            action = request.POST.get("action", "preview")
            datum_vydeje = form.cleaned_data['datum_vydeje']
            menu_items = form.cleaned_data['menu_items']
            uzivatele = form.cleaned_data['uzivatele']
            plan = build_bulk_order_plan(datum_vydeje, menu_items, uzivatele)

            if action != "confirm":
                context = {
                    **self.admin_site.each_context(request),
                    "title": "Náhled hromadného založení objednávek",
                    "form": form,
                    "datum_vydeje": datum_vydeje,
                    "menu_items": menu_items,
                    "plan": plan,
                }
                return render(request, "admin/objednavky/bulk_create_preview.html", context)

            result = apply_bulk_order_plan(datum_vydeje, menu_items, plan["entries"])
            skipped = plan["summary"]["skipped"]
            if skipped:
                self.message_user(
                    request,
                    (
                        f"Vytvořeno {result['created']} nových objednávek, "
                        f"nahrazeno {result['replaced']} rozpracovaných a "
                        f"přeskočeno {skipped} uživatelů."
                    ),
                    level='warning'
                )
            else:
                self.message_user(
                    request,
                    (
                        f"Vytvořeno {result['created']} nových objednávek a "
                        f"nahrazeno {result['replaced']} rozpracovaných na datum {datum_vydeje}."
                    )
                )
            return redirect('..')

        context = dict(
            self.admin_site.each_context(request),
            form=form,
            today_iso=timezone.localdate().isoformat(),
        )
        return render(request, "admin/bulk_create_orders.html", context)

    def users_api(self, request):
        query = request.GET.get('q', '').strip()
        skupina = request.GET.get('skupina', '').strip()
        datum = request.GET.get('datum', '').strip()
        menu_item_ids = request.GET.getlist('menu_items')
        users = User.objects.filter(is_active=True)
        if skupina:
            try:
                group_id = int(skupina)
                users = users.filter(groups__id=group_id)
            except ValueError:
                pass
        if query and len(query) > 0:
            users = users.filter(
                models.Q(username__icontains=query) |
                models.Q(first_name__icontains=query) |
                models.Q(last_name__icontains=query) |
                models.Q(osobni_cislo__icontains=query)
            )
        users = users.order_by('last_name', 'first_name')

        plan_by_user_id = {}
        if datum and menu_item_ids:
            try:
                datum_vydeje = date.fromisoformat(datum)
                menu_items = list(PolozkaJidelnicku.objects.filter(pk__in=menu_item_ids))
                if menu_items:
                    plan = build_bulk_order_plan(datum_vydeje, menu_items, list(users))
                    plan_by_user_id = {
                        entry["user"].pk: entry
                        for entry in plan["entries"]
                    }
            except (ValueError, TypeError):
                plan_by_user_id = {}

        data = [
            {
                "id": u.id,
                "osobni_cislo": getattr(u, 'osobni_cislo', ''),
                "name": f"{u.first_name} {u.last_name}",
                "username": u.username,
                "action": plan_by_user_id.get(u.id, {}).get("action", ""),
                "reason": plan_by_user_id.get(u.id, {}).get("reason", ""),
                "total_price": float(plan_by_user_id.get(u.id, {}).get("total_price", Decimal("0"))),
                "existing_items": plan_by_user_id.get(u.id, {}).get("existing_items", 0),
            }
            for u in users
        ]
        return JsonResponse(data, safe=False)

    def menu_items_api(self, request):
        datum = request.GET.get('datum')
        if not datum:
            return JsonResponse([], safe=False)
        from datetime import datetime
        try:
            datum_obj = datetime.strptime(datum, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse([], safe=False)
        items = PolozkaJidelnicku.objects.filter(
            jidelnicek__platnost_od__lte=datum_obj,
            jidelnicek__platnost_do__gte=datum_obj
        ).values('id', 'jidlo__nazev', 'druh_jidla__nazev')
        data = [
            {
                'id': item['id'],
                'nazev': item['jidlo__nazev'],
                'druh_jidla': item['druh_jidla__nazev'],
            }
            for item in items
        ]
        return JsonResponse(data, safe=False)

    def jidelnicek_days_api(self, request):
        from datetime import timedelta
        today = timezone.localdate()
        days_set = {today.strftime('%Y-%m-%d')}
        jidelnicky = Jidelnicek.objects.filter(platnost_do__gte=today)
        for j in jidelnicky:
            current = max(j.platnost_od, today)
            while current <= j.platnost_do:
                days_set.add(current.strftime('%Y-%m-%d'))
                current += timedelta(days=1)
        days_list = sorted(days_set)
        return JsonResponse(days_list, safe=False)

    def price_recalculation_view(self, request):
        """View pro přepočet cen s náhledem a potvrzením"""
        
        if request.method == 'POST':
            action = request.POST.get('action')
            
            # Načti data z formuláře
            date_from_str = request.POST.get('date_from')
            date_to_str = request.POST.get('date_to')
            
            try:
                date_from = date.fromisoformat(date_from_str)
                date_to = date.fromisoformat(date_to_str)
            except (ValueError, TypeError):
                messages.error(request, '❌ Neplatné datum')
                return redirect('admin:objednavky_order_changelist')
            
            # Validace období
            if date_from > date_to:
                messages.error(request, '❌ Datum OD musí být menší nebo rovno datu DO')
                return redirect('admin:objednavky_order_price_recalculation')
            
            if action == 'preview':
                # Dry run - pouze náhled změn
                result = recalculate_order_prices(
                    date_from, date_to, 
                    request.user, 
                    dry_run=True
                )
                
                if not result['success']:
                    messages.warning(request, result['message'])
                    return redirect('admin:objednavky_order_price_recalculation')
                
                # Seskup změny podle uživatelů
                by_user = get_recalculation_summary_by_user(result['changes'])
                
                context = {
                    **self.admin_site.each_context(request),
                    'title': 'Náhled přepočtu cen',
                    'result': result,
                    'by_user': by_user,
                    'date_from': date_from,
                    'date_to': date_to,
                }
                return render(request, 'admin/objednavky/price_recalculation_preview.html', context)
            
            elif action == 'confirm':
                # Skutečný přepočet
                result = recalculate_order_prices(
                    date_from, date_to, 
                    request.user, 
                    dry_run=False
                )
                
                if result['success']:
                    messages.success(
                        request, 
                        f"✅ {result['message']} | "
                        f"Cenový rozdíl: {result['total_price_diff']:+.2f} Kč"
                    )
                else:
                    messages.error(request, f"❌ {result['message']}")
                
                return redirect('admin:objednavky_pricerecalculationlog_changelist')
        
        # GET request - zobraz formulář
        context = {
            **self.admin_site.each_context(request),
            'title': 'Přepočet cen objednávek',
            'today': date.today(),
            'week_later': date.today() + timedelta(days=7),
        }
        return render(request, 'admin/objednavky/price_recalculation_form.html', context)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """
    Detailní list položek objednávek
    """
    list_display = [
        'datum_vydeje',
        'osobni_cislo',
        'jmeno',
        'prijmeni',
        'druh_jidla',
        'jidlo_nazev',
        'quantity',
        'cena',
        'total_price',
    ]
    list_filter = ['order__datum_vydeje', 'order__user', 'order__status', 'menu_item__druh_jidla']
    search_fields = [
        'order__user__username',
        'order__user__first_name',
        'order__user__last_name',
        'order__user__osobni_cislo',
        'menu_item__jidlo__nazev',
    ]
    readonly_fields = ['total_price']
    list_select_related = ('order__user', 'menu_item__jidlo', 'menu_item__druh_jidla')

    @admin.display(description="Datum výdeje", ordering='order__datum_vydeje')
    def datum_vydeje(self, obj):
        return obj.order.datum_vydeje.strftime('%d.%m.%Y')

    @admin.display(description="Osobní číslo", ordering='order__user__osobni_cislo')
    def osobni_cislo(self, obj):
        return getattr(obj.order.user, 'osobni_cislo', '') or '-'

    @admin.display(description="Jméno", ordering='order__user__first_name')
    def jmeno(self, obj):
        return obj.order.user.first_name or '-'

    @admin.display(description="Příjmení", ordering='order__user__last_name')
    def prijmeni(self, obj):
        return obj.order.user.last_name or '-'

    @admin.display(description="Druh jídla", ordering='menu_item__druh_jidla__poradi')
    def druh_jidla(self, obj):
        return getattr(obj.menu_item.druh_jidla, 'nazev', str(obj.menu_item.druh_jidla))

    @admin.display(description="Jídlo", ordering='menu_item__jidlo__nazev')
    def jidlo_nazev(self, obj):
        return obj.menu_item.jidlo.nazev

    def total_price(self, obj):
        return f"{obj.quantity * obj.cena} Kč"


@admin.register(PriceRecalculationLog)
class PriceRecalculationLogAdmin(admin.ModelAdmin):
    verbose_name = "Přepočet cen log"
    verbose_name_plural = "Přepočty cen logy"

    list_display = [
        'created_at', 'created_by', 'date_range', 
        'orders_affected', 'items_affected', 
        'colored_price_diff'
    ]
    list_filter = ['created_at', 'created_by']
    search_fields = ['created_by__username', 'note']
    readonly_fields = [
        'created_at', 'created_by', 'date_from', 'date_to',
        'orders_affected', 'items_affected', 'total_price_diff', 'note'
    ]
    
    def date_range(self, obj):
        return f"{obj.date_from.strftime('%d.%m.%Y')} - {obj.date_to.strftime('%d.%m.%Y')}"
    date_range.short_description = "Období"
    
    def colored_price_diff(self, obj):
        diff = obj.total_price_diff
        if diff > 0:
            color = 'red'
            symbol = '+'
        elif diff < 0:
            color = 'green'
            symbol = ''
        else:
            color = 'gray'
            symbol = ''

        formatted_diff = f"{symbol}{diff:.2f} Kč"  # ✅ Naformátuj string PŘEDEM
        return format_html(
            '<span style="color: {};">{}</span>',  # ✅ Pak jen vložíš string
            color, formatted_diff
        )
    
    colored_price_diff.short_description = "Cenový rozdíl"
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PriceRecalculationDetail)
class PriceRecalculationDetailAdmin(admin.ModelAdmin):

    verbose_name = "Přepočet cen detail"
    verbose_name_plural = "Přepočty cen detaily"

    list_display = [
        'log', 
        'datum_vydeje',      # ✅ NOVÉ
        'osobni_cislo',      # ✅ NOVÉ
        'jmeno_prijmeni',    # ✅ NOVÉ
        'jidlo_nazev',       # ✅ NOVÉ
        'old_price', 
        'new_price', 
        'colored_diff'
    ]
    list_filter = ['log__created_at', 'order_item__order__datum_vydeje']  # ✅ Přidán filtr
    search_fields = [  # ✅ Přidáno vyhledávání
        'order_item__order__user__first_name',
        'order_item__order__user__last_name',
        'order_item__order__user__osobni_cislo',
        'order_item__menu_item__jidlo__nazev',
    ]
    readonly_fields = [
        'log', 'order_item', 'old_price', 'new_price', 'price_diff'
    ]
    list_select_related = (  # ✅ Optimalizace dotazů
        'order_item__order__user', 
        'order_item__menu_item__jidlo',
        'log'
    )
    
    # ✅ NOVÉ METODY pro zobrazení
    @admin.display(description="Datum výdeje", ordering='order_item__order__datum_vydeje')
    def datum_vydeje(self, obj):
        return obj.order_item.order.datum_vydeje.strftime('%d.%m.%Y')
    
    @admin.display(description="Osobní číslo", ordering='order_item__order__user__osobni_cislo')
    def osobni_cislo(self, obj):
        return getattr(obj.order_item.order.user, 'osobni_cislo', '') or '-'
    
    @admin.display(description="Zákazník", ordering='order_item__order__user__last_name')
    def jmeno_prijmeni(self, obj):
        user = obj.order_item.order.user
        return f"{user.first_name} {user.last_name}"
    
    @admin.display(description="Jídlo", ordering='order_item__menu_item__jidlo__nazev')
    def jidlo_nazev(self, obj):
        return obj.order_item.menu_item.jidlo.nazev
    
    def colored_diff(self, obj):
        diff = obj.price_diff
        if diff > 0:
            color = 'red'
            symbol = '+'
        elif diff < 0:
            color = 'green'
            symbol = ''
        else:
            color = 'gray'
            symbol = ''
        
        formatted_diff = f"{symbol}{diff:.2f} Kč"
        
        return format_html(
            '<span style="color: {};">{}</span>',
            color, formatted_diff
        )
    colored_diff.short_description = "Rozdíl"
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False



class OrderSummaryAdmin(admin.ModelAdmin):
    """
    Souhrn: jídelníček vs. počet objednaných jídel
    """
    change_list_template = "admin/order_summary_changelist.html"
    
    CLOSED_STATUSES = ["uzavrena", "uzavrena-objednavani"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return OrderItem.objects.none()

    def changelist_view(self, request, extra_context=None):
        closed_orders = Order.objects.filter(status__in=self.CLOSED_STATUSES)
        nearest_date = closed_orders.aggregate(d=Max('datum_vydeje'))['d']

        jidelnicek_items = []
        chart_labels = []
        chart_values = []

        if nearest_date:
            jidelnicek_items_qs = PolozkaJidelnicku.objects.filter(
                jidelnicek__platnost_od__lte=nearest_date,
                jidelnicek__platnost_do__gte=nearest_date
            ).select_related('jidlo', 'druh_jidla')

            objednavky_agg = (
                OrderItem.objects
                .filter(
                    order__datum_vydeje=nearest_date,
                    order__status__in=self.CLOSED_STATUSES
                )
                .values('menu_item_id')
                .annotate(pocet_kusu=Sum('quantity'))
            )

            objednavky_map = {
                row['menu_item_id']: row['pocet_kusu'] for row in objednavky_agg
            }

            for item in jidelnicek_items_qs:
                pocet = objednavky_map.get(item.id, 0) or 0
                jidelnicek_items.append({
                    "druh_jidla": getattr(item.druh_jidla, "nazev", str(item.druh_jidla)),
                    "jidlo": item.jidlo.nazev,
                    "pocet_kusu": pocet,
                })
                chart_labels.append(item.jidlo.nazev)
                chart_values.append(pocet)

        context = {
            **self.admin_site.each_context(request),
            "summary_date": nearest_date,
            "jidelnicek_items": jidelnicek_items,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "title": "Souhrn objednávek vs. jídelníček",
        }
        return render(request, self.change_list_template, context)
