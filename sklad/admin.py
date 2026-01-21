from datetime import datetime
from django.utils import timezone
from django.contrib import admin

from .models import SkladDashboard, Surovina, StavSkladu, RecepturaPolozka
from objednavky.models import OrderItem


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
    list_editable = ("mnozstvi", "min_mnozstvi")


class RecepturaPolozkaInline(admin.TabularInline):
    model = RecepturaPolozka
    extra = 1
