from datetime import datetime
from decimal import Decimal
from django.utils import timezone
from django.contrib import admin

from .models import SkladDashboard, Surovina, StavSkladu, RecepturaPolozka
from objednavky.models import OrderItem

from .models import (
    SkladDashboard, Surovina, StavSkladu, RecepturaPolozka,
    PrijemSkladu, PolozkaPrijmu,
)
from django.db import transaction

from .models import (
    SkladDashboard, Surovina, StavSkladu, RecepturaPolozka,
    PrijemSkladu, PolozkaPrijmu,
    Inventura, PolozkaInventury, InventurniDoklad,
)

@admin.register(SkladDashboard)
class SkladDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/sklad/dashboard.html"

    def get_queryset(self, request):
        # prázdný queryset – žádná tabulka v DB
        return SkladDashboard.objects.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}

        # datum z GET ?date=YYYY-MM-DD, default dnes
        date_str = request.GET.get("date")
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = timezone.localdate()
        else:
            target_date = timezone.localdate()

        # 1) všechna OrderItem pro daný den (očekávaná spotřeba)
        items_all = OrderItem.objects.select_related(
            "menu_item__jidlo"
        ).filter(order__datum_vydeje=target_date)

        # 2) jen vydané položky pro daný den (reálná spotřeba)
        items_issued = items_all.filter(vydano=True)

        from collections import defaultdict
        expected = defaultdict(lambda: 0)
        real = defaultdict(lambda: 0)

        # očekávaná spotřeba – všechny položky
        for item in items_all.prefetch_related("menu_item__jidlo__receptura__surovina"):
            jidlo = item.menu_item.jidlo
            quantity = item.quantity
            for pol in jidlo.receptura.all():
                key = pol.surovina_id
                mnozstvi = pol.mnozstvi_na_porci * quantity
                expected[key] += mnozstvi

        # reálná spotřeba – jen vydané položky
        for item in items_issued.prefetch_related("menu_item__jidlo__receptura__surovina"):
            jidlo = item.menu_item.jidlo
            quantity = item.quantity
            for pol in jidlo.receptura.all():
                key = pol.surovina_id
                mnozstvi = pol.mnozstvi_na_porci * quantity
                real[key] += mnozstvi

        # 3) složit tabulku
        rows = []
        suroviny = Surovina.objects.select_related("stav").all()
        suroviny_by_id = {s.id: s for s in suroviny}

        for surovina_id, exp in expected.items():
            s = suroviny_by_id.get(surovina_id)
            if not s:
                continue
            real_mnozstvi = real.get(surovina_id, 0)
            stav = getattr(s, "stav", None)
            stav_mnozstvi = stav.mnozstvi if stav else None
            min_mnozstvi = stav.min_mnozstvi if stav else None
            pod_min = (
                stav is not None
                and stav_mnozstvi is not None
                and min_mnozstvi is not None
                and stav_mnozstvi < min_mnozstvi
            )

            rows.append({
                "surovina": s,
                "expected": exp,
                "real": real_mnozstvi,
                "stav": stav_mnozstvi,
                "min": min_mnozstvi,
                "pod_min": pod_min,
            })

        extra_context["target_date"] = target_date
        extra_context["rows"] = rows

        return super().changelist_view(request, extra_context=extra_context)


@admin.register(Surovina)
class SurovinaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "jednotka")
    search_fields = ("nazev",)


@admin.register(StavSkladu)
class StavSkladuAdmin(admin.ModelAdmin):
    list_display = ("surovina", "mnozstvi", "min_mnozstvi")
    readonly_fields = ("surovina", "mnozstvi")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



class RecepturaPolozkaInline(admin.TabularInline):
    model = RecepturaPolozka
    extra = 1


class PolozkaPrijmuInline(admin.TabularInline):
    model = PolozkaPrijmu
    extra = 1


@admin.register(PrijemSkladu)
class PrijemSkladuAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "vytvoril", "uzavreny")
    list_filter = ("uzavreny", "datum")
    inlines = [PolozkaPrijmuInline]
    readonly_fields = ("vytvoril",)

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

    @transaction.atomic
    def response_change(self, request, obj):
        """
        Při označení 'uzavreny' navýší stav skladu podle položek.
        """
        if "uzavreny" in obj.__dict__ and obj.uzavreny:
            # jednorázově navýšit sklad (idempotence: jen pokud dřív nebyl uzavřený)
            prev = PrijemSkladu.objects.get(pk=obj.pk)
            if not prev.uzavreny:
                for pol in obj.polozky.select_related("surovina").all():
                    stav, _ = StavSkladu.objects.get_or_create(
                        surovina=pol.surovina,
                        defaults={"mnozstvi": 0, "min_mnozstvi": 0},
                    )
                    stav.mnozstvi = stav.mnozstvi + pol.mnozstvi
                    stav.save(update_fields=["mnozstvi"])
        return super().response_change(request, obj)
    

    

class PolozkaInventuryInline(admin.TabularInline):
    model = PolozkaInventury
    extra = 0
    readonly_fields = ("stav_pred", "rozdil")


@admin.register(Inventura)
class InventuraAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "vytvoril", "uzavrena")
    list_filter = ("uzavrena", "datum")
    readonly_fields = ("vytvoril",)
    inlines = [PolozkaInventuryInline]

    def has_delete_permission(self, request, obj=None):
        # inventuru raději nemažeme
        return False

    def save_model(self, request, obj, form, change):
        # doplnění vytvoril
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

        # při vytvoření inventury automaticky předvyplnit položky
        if not change:
            self._napln_polozky_ze_stavu(obj)

    def _napln_polozky_ze_stavu(self, inventura):
        """
        Vytvoří PolozkaInventury pro všechny suroviny, které mají StavSkladu.
        stav_pred = aktuální množství na skladě,
        fyzicky_stav = stejné číslo (uživatel pak jen opraví),
        rozdil = 0.
        """
        stavy = StavSkladu.objects.select_related("surovina").all()
        polozky = []
        for stav in stavy:
            polozky.append(PolozkaInventury(
                inventura=inventura,
                surovina=stav.surovina,
                stav_pred=stav.mnozstvi,
                fyzicky_stav=stav.mnozstvi,
                rozdil=Decimal("0"),
            ))
        PolozkaInventury.objects.bulk_create(polozky)
    
    

    @transaction.atomic
    def response_change(self, request, obj):
        """
        Při uzavření inventury promítne rozdíly do StavSkladu:
        nastaví stav skladu na fyzicky_stav.
        """
        if "uzavrena" in obj.__dict__ and obj.uzavrena:
            prev = Inventura.objects.get(pk=obj.pk)
            if not prev.uzavrena:
                for pol in obj.polozky.select_related("surovina").all():
                    stav, _ = StavSkladu.objects.get_or_create(
                        surovina=pol.surovina,
                        defaults={"mnozstvi": 0, "min_mnozstvi": 0},
                    )
                    stav.mnozstvi = pol.fyzicky_stav
                    stav.save(update_fields=["mnozstvi"])
        return super().response_change(request, obj)
    
class PolozkaInventuryReadOnlyInline(admin.TabularInline):
    model = PolozkaInventury
    extra = 0
    can_delete = False
    readonly_fields = ("surovina", "stav_pred", "fyzicky_stav", "rozdil")
    fields = ("surovina", "stav_pred", "fyzicky_stav", "rozdil")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InventurniDoklad)
class InventurniDokladAdmin(admin.ModelAdmin):
    """
    Čistě read-only pohled na uzavřené inventury – inventurní doklady.
    """
    list_display = ("id", "datum", "vytvoril", "pocet_polozek")
    list_filter = ("datum",)
    search_fields = ("id", "vytvoril__username")
    inlines = [PolozkaInventuryReadOnlyInline]

    # hlavička inventury jen ke čtení
    readonly_fields = ("datum", "popis", "vytvoril", "uzavrena")

    def get_queryset(self, request):
        # zobrazovat jen uzavřené inventury
        qs = super().get_queryset(request)
        return qs.filter(uzavrena=True)

    # kompletně zakázat editace / mazání / přidávání
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # umožní zobrazit detail, ale inline i pole jsou readonly
        return True

    def has_delete_permission(self, request, obj=None):
        return False

    def pocet_polozek(self, obj):
        return obj.polozky.count()
    pocet_polozek.short_description = "Počet položek"
