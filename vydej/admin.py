from django.contrib import admin
from django.http import HttpResponse
from django.db.models import Sum, Q
from django.utils.html import format_html
from django.urls import reverse
from django.shortcuts import redirect
from django.contrib import messages
from django.template.response import TemplateResponse
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal

from objednavky.models import Order, OrderItem
from dotace.models import DotacniPolitika, DotaceProJidelniskouSkupinu
from .models import VydejOrder, VydejniUctenka, PolozkaUctenky, PrehledProKuchyni
from .utils import generuj_pdf_uctenka
from .models import StornovaneObjednavky

# ==================== CUSTOM FILTRY ====================

class UserFilter(admin.SimpleListFilter):
    """Custom filtr pro uživatele"""
    title = 'Uživatel'
    parameter_name = 'user'
    
    def lookups(self, request, model_admin):
        """Vrátí seznam uživatelů, kteří mají objednávky k výdeji"""
        users = Order.objects.filter(
            Q(status='objednano') | Q(status='zalozena-obsluhou')
        ).select_related('user').values_list(
            'user__id', 'user__first_name', 'user__last_name', 'user__username'
        ).distinct()
        
        return [
            (user_id, f"{first_name} {last_name}" if first_name else username)
            for user_id, first_name, last_name, username in users
        ]
    
    def queryset(self, request, queryset):
        """Filtruj podle vybraného uživatele"""
        if self.value():
            return queryset.filter(user__id=self.value())
        return queryset


class DatumVydejeFilter(admin.SimpleListFilter):
    """Custom filtr pro datum výdeje s předdefinovanými obdobími"""
    title = 'Datum výdeje'
    parameter_name = 'datum_vydeje_filter'
    
    def lookups(self, request, model_admin):
        """Vrátí seznam předdefinovaných období"""
        return (
            ('dnes', 'Dnes'),
            ('zitra', 'Zítra'),
            ('aktualni_tyden', 'Tento týden'),
            ('pristi_tyden', 'Příští týden'),
            ('aktualni_mesic', 'Tento měsíc'),
            ('pristi_mesic', 'Příští měsíc'),
        )
    
    def queryset(self, request, queryset):
        """Filtruj podle vybraného období"""
        today = date.today()
        
        if self.value() == 'dnes':
            return queryset.filter(datum_vydeje=today)
        
        elif self.value() == 'zitra':
            tomorrow = today + timedelta(days=1)
            return queryset.filter(datum_vydeje=tomorrow)
        
        elif self.value() == 'aktualni_tyden':
            # Pondělí až neděle aktuálního týdne
            start_week = today - timedelta(days=today.weekday())
            end_week = start_week + timedelta(days=6)
            return queryset.filter(datum_vydeje__gte=start_week, datum_vydeje__lte=end_week)
        
        elif self.value() == 'pristi_tyden':
            # Pondělí až neděle příštího týdne
            start_next_week = today - timedelta(days=today.weekday()) + timedelta(days=7)
            end_next_week = start_next_week + timedelta(days=6)
            return queryset.filter(datum_vydeje__gte=start_next_week, datum_vydeje__lte=end_next_week)
        
        elif self.value() == 'aktualni_mesic':
            # První a poslední den aktuálního měsíce
            start_month = today.replace(day=1)
            if today.month == 12:
                end_month = today.replace(day=31)
            else:
                end_month = (today.replace(month=today.month + 1, day=1) - timedelta(days=1))
            return queryset.filter(datum_vydeje__gte=start_month, datum_vydeje__lte=end_month)
        
        elif self.value() == 'pristi_mesic':
            # První a poslední den příštího měsíce
            if today.month == 12:
                start_next_month = date(today.year + 1, 1, 1)
                end_next_month = date(today.year + 1, 1, 31)
            else:
                start_next_month = today.replace(month=today.month + 1, day=1)
                if today.month == 11:
                    end_next_month = date(today.year, 12, 31)
                else:
                    end_next_month = (today.replace(month=today.month + 2, day=1) - timedelta(days=1))
            return queryset.filter(datum_vydeje__gte=start_next_month, datum_vydeje__lte=end_next_month)
        
        return queryset


# ==================== ADMIN TŘÍDY ====================

@admin.register(VydejOrder)
class VydejOrderAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user_full_name', 'datum_vydeje', 'get_status_display',
        'zobraz_polozky', 'total_items', 'total_price_display', 'created_at', 'akce_vydat'
    ]
    list_filter = [UserFilter, DatumVydejeFilter, 'status', 'created_at']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'id']
    date_hierarchy = 'datum_vydeje'
    ordering = ['datum_vydeje', '-created_at']
    actions = ['vydat_objednavky']
    list_per_page = 20
    
    def get_queryset(self, request):
        """Filtruj pouze objednávky ve stavu 'objednáno' a 'založena obsluhou'"""
        qs = super().get_queryset(request)
        return qs.filter(
            Q(status='objednano') | Q(status='zalozena-obsluhou')
        ).select_related('user').prefetch_related('items__menu_item__jidlo', 'items__menu_item__druh_jidla')
    
    def user_full_name(self, obj):
        """Zobraz celé jméno uživatele"""
        return obj.user.get_full_name() or obj.user.username
    user_full_name.short_description = 'Uživatel'
    user_full_name.admin_order_field = 'user__last_name'
    
    def get_status_display(self, obj):
        """Zobraz lidsky čitelný stav"""
        return obj.get_status_display()
    get_status_display.short_description = 'Stav'
    get_status_display.admin_order_field = 'status'
    
    def zobraz_polozky(self, obj):
        """Zobraz položky objednávky v přehledném formátu"""
        items = obj.items.all()
        if not items:
            return '-'
        
        html = '<ul style="margin: 0; padding-left: 15px; line-height: 1.6;">'
        for item in items:
            html += (
                f'<li><strong>{item.menu_item.jidlo.nazev}</strong> '
                f'({item.menu_item.druh_jidla.nazev}) - {item.quantity}× {item.cena} Kč</li>'
            )
        html += '</ul>'
        return format_html(html)
    zobraz_polozky.short_description = 'Položky objednávky'
    
    def total_items(self, obj):
        """Celkový počet položek v objednávce"""
        return obj.items.aggregate(total=Sum('quantity'))['total'] or 0
    total_items.short_description = 'Celkem ks'
    
    def total_price_display(self, obj):
        """Celková cena objednávky"""
        total = sum(item.quantity * item.cena for item in obj.items.all())
        return format_html('<strong>{} Kč</strong>', total)
    total_price_display.short_description = 'Celková cena'
    
    def akce_vydat(self, obj):
        """Tlačítko pro výdej objednávky"""
        return format_html(
            '<a class="button" href="{}?order_id={}" style="background: #32B8C6; color: white; '
            'padding: 5px 10px; text-decoration: none; border-radius: 3px;">Vydat</a>',
            reverse('admin:vydat_objednavku'),
            obj.id
        )
    akce_vydat.short_description = 'Akce'
    
    def vydat_objednavky(self, request, queryset):
        """Hromadná akce pro výdej více objednávek"""
        vydano = 0
        for order in queryset:
            if self._vydat_objednavku(order, request.user):
                vydano += 1
        
        self.message_user(request, f'Vydáno {vydano} objednávek.', messages.SUCCESS)
    vydat_objednavky.short_description = "Vydat vybrané objednávky"
    
    def _vydat_objednavku(self, order, vydal_user):
        """Logika pro výdej objednávky a vytvoření účtenky"""
        # ✅ ZKONTROLUJ, JESTLI UŽ ÚČTENKA NEEXISTUJE
        if VydejniUctenka.objects.filter(order=order).exists():
            return False
        
        # Zkontroluj, jestli už není vydáno
        if order.status == 'vydano':
            return False
        
        # Vypočítej ceny a dotace
        celkova_cena = Decimal('0')
        celkova_dotace = Decimal('0')
        polozky_data = []
        
        for item in order.items.all():
            cena_za_kus = item.cena
            
            # Vypočítej dotaci
            puvodni_cena = item.menu_item.jidlo.cena
            dotace_za_kus = puvodni_cena - cena_za_kus
            
            celkova_cena += cena_za_kus * item.quantity
            celkova_dotace += dotace_za_kus * item.quantity
            
            polozky_data.append({
                'nazev_jidla': item.menu_item.jidlo.nazev,
                'druh_jidla': item.menu_item.druh_jidla.nazev,
                'mnozstvi': item.quantity,
                'cena_za_kus': cena_za_kus,
                'dotace_za_kus': dotace_za_kus,
            })
        
        # Vytvoř účtenku
        uctenka = VydejniUctenka.objects.create(
            order=order,
            datum_vydeje=timezone.now(),
            vydal=vydal_user,
            celkova_cena=celkova_cena,
            celkova_dotace=celkova_dotace
        )
        
        # Vytvoř položky účtenky
        for polozka_data in polozky_data:
            PolozkaUctenky.objects.create(
                uctenka=uctenka,
                **polozka_data
            )
        
        # Změň stav objednávky
        order.status = 'vydano-obsluhou'
        order.save()
        
        return True

    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('vydat/', self.admin_site.admin_view(self.vydat_view), name='vydat_objednavku'),
        ]
        return custom_urls + urls
    
    def vydat_view(self, request):
        """View pro výdej jednotlivé objednávky"""
        order_id = request.GET.get('order_id')
        if order_id:
            try:
                order = Order.objects.get(id=order_id)
                if self._vydat_objednavku(order, request.user):
                    messages.success(request, f'Objednávka #{order_id} byla vydána.')
                    # Přesměruj na detail účtenky
                    uctenka = VydejniUctenka.objects.get(order=order)
                    return redirect('admin:vydej_vydejniuctenka_change', uctenka.id)
                else:
                    messages.error(request, 'Objednávka už byla vydána.')
            except Order.DoesNotExist:
                messages.error(request, 'Objednávka nenalezena.')
        
        return redirect('admin:vydej_vydejorder_changelist')
    
    def has_add_permission(self, request):
        """Zakáže vytváření nových objednávek z adminu"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Zakáže mazání objednávek z adminu"""
        return False


@admin.register(VydejniUctenka)
class VydejniUctenkaAdmin(admin.ModelAdmin):
    list_display = ['id', 'get_user', 'datum_vydeje', 'celkova_cena', 'celkova_dotace', 'vydal', 'stahnout_pdf', 'akce_smazat']
    list_filter = ['datum_vydeje', 'vydal']
    search_fields = ['order__user__username', 'order__user__first_name', 'order__user__last_name', 'id']
    date_hierarchy = 'datum_vydeje'
    readonly_fields = ['order', 'datum_vydeje', 'vydal', 'celkova_cena', 'celkova_dotace', 'zobraz_detail']
    
    # ✅ HMADNÉ AKCE
    actions = ['stahnout_pdf_uctenky', 'stornovat_ucetnky_se_objednavkami']
    
    fieldsets = (
        ('Základní informace', {
            'fields': ('order', 'datum_vydeje', 'vydal', 'celkova_cena', 'celkova_dotace', 'poznamka')
        }),
        ('Náhled účtenky', {
            'fields': ('zobraz_detail',)
        }),
    )
    
    def get_user(self, obj):
        return obj.order.user.get_full_name() or obj.order.user.username
    get_user.short_description = 'Zákazník'
    
    def stahnout_pdf(self, obj):
        """Tlačítko pro stažení PDF účtenky"""
        return format_html(
            '<a class="button" href="{}">📥 PDF</a>',
            reverse('admin:uctenka_pdf', args=[obj.id])
        )
    stahnout_pdf.short_description = 'Stažení'
    
    # ✅ TLAČÍTKO STORNOVAT v seznamu
    def akce_smazat(self, obj):
        """Tlačítko pro stornování účtenky + objednávky"""
        return format_html(
            '<a href="{}" '
            'onclick="return confirm(\'Opravdu chcete STORNOVAT účtenku #{} a označit objednávku jako STORNOVANOU?\');" '
            'class="button" style="background: #dc3545; color: white; padding: 5px 10px;">🗑️ Stornovat</a>',
            reverse('admin:vydej_vydejniuctenka_stornovat', args=[obj.id]),
            obj.id
        )
    akce_smazat.short_description = 'Akce'
    
    # ✅ HMADNÁ AKCE - Stornovat účtenky + označit objednávky jako STORNOVANÉ
    def _stornovat_ucetnku_se_objednavkou(self, uctenka, storno_user):
        """Interní metoda - smaže účtenku a označí objednávku jako STORNOVANÁ"""
        order = uctenka.order
        
        # ✅ ULOŽ STORNO INFO
        order.storno_user = storno_user
        order.storno_datum = timezone.now()
        
        # Změň stav
        order.status = 'stornovano'
        order.save()
        
        # Smaz položky a účtenku
        uctenka.polozky.all().delete()
        uctenka.delete()

    def stornovat_ucetnky_se_objednavkami(self, request, queryset):
        """Hromadná akce - stornuje účtenky a označí objednávky jako STORNOVANÉ"""
        stornovano = 0
        for uctenka in queryset:
            self._stornovat_ucetnku_se_objednavkou(uctenka, request.user)
            stornovano += 1
        
        self.message_user(
            request, 
            f'✅ Stornováno {stornovano} účtenek. Objednávky označeny jako STORNOVANÉ.', 
            messages.SUCCESS
        )
    stornovat_ucetnky_se_objednavkami.short_description = "🗑️ Stornovat účtenky a objednávky"
    stornovat_ucetnky_se_objednavkami.actions_selection_counter = False

    
    def stahnout_pdf_uctenky(self, request, queryset):
        """Hromadná akce pro stažení více PDF"""
        if queryset.count() == 1:
            uctenka = queryset.first()
            pdf_buffer = generuj_pdf_uctenka(uctenka)
            response = HttpResponse(pdf_buffer, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="uctenka_{uctenka.id}.pdf"'
            return response
        else:
            self.message_user(request, 'Prosím vyberte pouze jednu účtenku najednou.', messages.WARNING)
    stahnout_pdf_uctenky.short_description = "Stáhnout PDF vybrané účtenky"
    
    def zobraz_detail(self, obj):
        """Zobrazení detailu účtenky"""
        html = f"""
        <div style="font-family: monospace; background: #f5f5f5; padding: 20px; border: 1px solid #ddd;">
            <h2 style="text-align: center;">VÝDEJNÍ ÚČTENKA #{obj.id}</h2>
            <hr>
            <p><strong>Zákazník:</strong> {obj.order.user.get_full_name() or obj.order.user.username}</p>
            <p><strong>Datum objednávky:</strong> {obj.order.created_at.strftime('%d.%m.%Y %H:%M')}</p>
            <p><strong>Datum výdeje:</strong> {obj.datum_vydeje.strftime('%d.%m.%Y %H:%M')}</p>
            <p><strong>Vydal:</strong> {obj.vydal.get_full_name() if obj.vydal else 'Neznámý'}</p>
            <hr>
            <h3>Položky:</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="background: #e0e0e0;">
                    <th style="text-align: left; padding: 5px;">Položka</th>
                    <th style="text-align: center; padding: 5px;">Ks</th>
                    <th style="text-align: right; padding: 5px;">Cena/ks</th>
                    <th style="text-align: right; padding: 5px;">Dotace/ks</th>
                    <th style="text-align: right; padding: 5px;">Celkem</th>
                </tr>
        """
        
        for polozka in obj.polozky.all():
            html += f"""
                <tr>
                    <td style="padding: 5px;">{polozka.nazev_jidla} ({polozka.druh_jidla})</td>
                    <td style="text-align: center; padding: 5px;">{polozka.mnozstvi}</td>
                    <td style="text-align: right; padding: 5px;">{polozka.cena_za_kus} Kč</td>
                    <td style="text-align: right; padding: 5px;">{polozka.dotace_za_kus} Kč</td>
                    <td style="text-align: right; padding: 5px;"><strong>{polozka.celkova_cena()} Kč</strong></td>
                </tr>
            """
        
        html += f"""
            </table>
            <hr>
            <p style="text-align: right;"><strong>Celková cena:</strong> {obj.celkova_cena} Kč</p>
            <p style="text-align: right;"><strong>Celková dotace:</strong> {obj.celkova_dotace} Kč</p>
            <p style="text-align: right; font-size: 1.2em;"><strong>K úhradě:</strong> {obj.celkova_cena} Kč</p>
            <hr>
            <p style="text-align: center;">
                <a href="{reverse('admin:uctenka_pdf', args=[obj.id])}" style="padding: 10px 20px; background: #32B8C6; color: white; text-decoration: none; border-radius: 4px;">
                    📥 Stáhnout jako PDF
                </a>
            </p>
        </div>
        """
        return format_html(html)
    zobraz_detail.short_description = 'Detail účtenky'
    
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('<int:uctenka_id>/pdf/', self.admin_site.admin_view(self.uctenka_pdf_view), name='uctenka_pdf'),
            path('<int:uctenka_id>/stornovat/', self.admin_site.admin_view(self.stornovat_ucetnku_view), name='vydej_vydejniuctenka_stornovat'),
        ]
        return custom_urls + urls
    
    def uctenka_pdf_view(self, request, uctenka_id):
        """View pro stažení PDF účtenky"""
        try:
            uctenka = VydejniUctenka.objects.get(id=uctenka_id)
            pdf_buffer = generuj_pdf_uctenka(uctenka)
            response = HttpResponse(pdf_buffer, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="uctenka_{uctenka.id}.pdf"'
            return response
        except VydejniUctenka.DoesNotExist:
            messages.error(request, 'Účtenka nenalezena.')
            return redirect('admin:vydej_vydejniuctenka_changelist')
    
    # ✅ VIEW PRO STORNO JEDNOTLIVÉ ÚČTENKY
    def stornovat_ucetnku_view(self, request, uctenka_id):
        """View pro stornování jednotlivé účtenky + objednávky"""
        try:
            uctenka = VydejniUctenka.objects.get(id=uctenka_id)
            self._stornovat_ucetnku_se_objednavkou(uctenka, request.user)
            messages.success(request, f'✅ Účtenka #{uctenka_id} stornována. Objednávka označena jako STORNOVANÁ.')
        except VydejniUctenka.DoesNotExist:
            messages.error(request, 'Účtenka nenalezena.')
        
        return redirect('admin:vydej_vydejniuctenka_changelist')
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrehledProKuchyni)
class PrehledProKuchyniAdmin(admin.ModelAdmin):
    change_list_template = 'admin/vydej/prehled_kuchyne.html'
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def changelist_view(self, request, extra_context=None):
        """Custom view pro přehled jídel pro kuchyni"""
        # Získej datum z parametru nebo použij nejbližší den s objednávkami
        datum_str = request.GET.get('datum', None)
        
        if not datum_str:
            # Najdi první den s objednávkami
            prvni_objednavka = Order.objects.filter(
                datum_vydeje__gte=date.today()
            ).order_by('datum_vydeje').first()
            
            if prvni_objednavka:
                datum_vydeje = prvni_objednavka.datum_vydeje
            else:
                datum_vydeje = date.today()
        else:
            try:
                datum_vydeje = date.fromisoformat(datum_str)
            except ValueError:
                datum_vydeje = date.today()
        
        # Získej všechny objednávky pro dané datum
        orders = Order.objects.filter(
            datum_vydeje=datum_vydeje,
            status__in=['objednano', 'zalozena-obsluhou']
        ).prefetch_related('items__menu_item__jidlo', 'items__menu_item__druh_jidla')
        
        # Agreguj data podle druhu a jídla
        stats = {}
        total_objednavek = 0
        total_porci = 0
        
        for order in orders:
            total_objednavek += 1
            for item in order.items.all():
                druh_nazev = item.menu_item.druh_jidla.nazev
                jidlo_nazev = item.menu_item.jidlo.nazev
                
                if druh_nazev not in stats:
                    stats[druh_nazev] = {}
                
                if jidlo_nazev not in stats[druh_nazev]:
                    stats[druh_nazev][jidlo_nazev] = {
                        'celkem': 0,
                        'uzivatele': []
                    }
                
                stats[druh_nazev][jidlo_nazev]['celkem'] += item.quantity
                total_porci += item.quantity
                
                # Přidej uživatele
                uzivatel_str = order.user.get_full_name() or order.user.username
                stats[druh_nazev][jidlo_nazev]['uzivatele'].append({
                    'jmeno': uzivatel_str,
                    'mnozstvi': item.quantity,
                    'order_id': order.id
                })
        
        # Získej info o uzávěrce
        uzavirka_info = self.get_uzavirka_info(datum_vydeje)
        
        # Export do PDF
        if request.GET.get('export') == 'pdf':
            return self.export_pdf(request, datum_vydeje, stats, total_objednavek, total_porci, uzavirka_info)
        
        # Vytvoř kontext manuálně
        today = date.today()
        tomorrow = today + timedelta(days=1)
        
        context = {
            **self.admin_site.each_context(request),
            'datum_vydeje': datum_vydeje,
            'datum_str': datum_vydeje.strftime('%Y-%m-%d'),
            'today': today.strftime('%Y-%m-%d'),
            'tomorrow': tomorrow.strftime('%Y-%m-%d'),
            'stats': stats,
            'total_objednavek': total_objednavek,
            'total_porci': total_porci,
            'uzavirka_info': uzavirka_info,
            'title': 'Přehled pro kuchyni',
            'site_title': 'Přehled pro kuchyni',
            'has_permission': True,
            'opts': self.model._meta,
        }
        
        context.update(extra_context or {})
        
        return TemplateResponse(request, self.change_list_template, context)

    
    def get_uzavirka_info(self, datum):
        """Vrátí info o uzávěrce pro daný datum"""
        from canteen_settings.models import OrderClosingTime
        
        try:
            settings = OrderClosingTime.objects.first()
            if not settings:
                return {'uzavreno': False, 'uzavreno_text': 'Neznámá uzávěrka'}
            
            closing_date = datum - timedelta(days=settings.advance_days)
            closing_datetime = timezone.datetime.combine(closing_date, settings.closing_time)
            closing_datetime = timezone.make_aware(closing_datetime, timezone.get_current_timezone())
            
            now = timezone.now()
            
            if now >= closing_datetime:
                return {
                    'uzavreno': True,
                    'uzavreno_text': 'Uzavřeno'
                }
            else:
                delta = closing_datetime - now
                celkem_sekund = int(delta.total_seconds())
                dny = celkem_sekund // 86400
                hodiny = (celkem_sekund % 86400) // 3600
                minuty = (celkem_sekund % 3600) // 60
                
                if dny > 0:
                    odpocet_text = f"{dny}d {hodiny}h"
                elif hodiny > 0:
                    odpocet_text = f"{hodiny}h {minuty}m"
                else:
                    odpocet_text = f"{minuty}m"
                
                return {
                    'uzavreno': False,
                    'uzavreno_text': f'Uzavře za {odpocet_text}',
                }
        except:
            return {'uzavreno': False, 'uzavreno_text': 'Neznámá uzávěrka'}
    
    def export_pdf(self, request, datum_vydeje, stats, total_objednavek, total_porci, uzavirka_info):
        """Export přehledu do PDF pro kuchyni"""
        from .utils import generuj_pdf_kuchyne
        
        pdf_buffer = generuj_pdf_kuchyne(datum_vydeje, stats, total_objednavek, total_porci, uzavirka_info)
        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="prehled_kuchyne_{datum_vydeje.strftime("%Y%m%d")}.pdf"'
        return response
    

from .models import StornovaneObjednavky

@admin.register(StornovaneObjednavky)
class StornovaneObjednavkyAdmin(admin.ModelAdmin):
    
    
    list_display = ['id', 'user_full_name', 'created_at', 'storno_info', 'zobraz_polozky', 'total_items', 'total_price']
    list_filter = ['created_at', 'datum_vydeje', 'storno_user', 'storno_datum']  # ✅ Přidal filtry
    search_fields = ['user__username', 'user__first_name', 'user__last_name']
    date_hierarchy = 'created_at'
    
    def get_queryset(self, request):
        return self.model.objects.filter(status='stornovano').select_related(
            'user', 'storno_user'
        ).prefetch_related(
            'items__menu_item__jidlo', 'items__menu_item__druh_jidla'
        ).order_by('-created_at')
    
    def user_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username
    user_full_name.short_description = 'Uživatel'
    
    def storno_info(self, obj):
        """Kdo a kdy provedl storno."""
        if obj.storno_user and obj.storno_datum:
            return format_html(
                '{}<br><small style="color: #dc3545;">{}</small>',
                obj.storno_user.get_full_name() or obj.storno_user.username,
                obj.storno_datum.strftime('%d.%m. %H:%M')
            )
        return '<small style="color: #6c757d;">NULL</small>'
    storno_info.short_description = 'Storno'
    storno_info.allow_tags = True

    
    def zobraz_polozky(self, obj):
        items = obj.items.all()[:2]
        html = '<ul style="margin: 0; padding-left: 10px;">'
        for item in items:
            html += f'<li>{item.menu_item.jidlo.nazev} ({item.quantity}x)</li>'
        if obj.items.count() > 2:
            html += f'<li><em>+{obj.items.count() - 2}</em></li>'

        html += '</ul>'
        return format_html(html)
    zobraz_polozky.short_description = 'Položky'
    
    def total_items(self, obj):
        return obj.items.aggregate(total=Sum('quantity'))['total'] or 0
    total_items.short_description = 'Ks'
    
    def total_price(self, obj):
        total = sum(item.quantity * item.cena for item in obj.items.all())
        formatted_total = f'{total:.2f}'  # ✅ Nejdřív naformátuj číslo
        return format_html('<strong>{} Kč</strong>', formatted_total)  # ✅ Pak použij v HTML
    total_price.short_description = 'Cena'

    
    
    
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
