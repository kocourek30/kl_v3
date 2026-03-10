from datetime import datetime, date
from decimal import Decimal
from collections import defaultdict
from io import BytesIO
import os

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)

from objednavky.models import OrderItem
from users.models import StravovaciSkupina
from .models import (
    SkladDashboard,
    Surovina,
    StavSkladu,
    PohybSkladu,
    RecepturaPolozka,
    PrijemSkladu,
    PolozkaPrijmu,
    Inventura,
    PolozkaInventury,
    InventurniDoklad,
    Vydejka,
    PolozkaVydejky,
    NormaSpotrebnihoKose,
)


# -------------------------------------------------------------------
# Měsíční report spotřebního koše
# -------------------------------------------------------------------

from .models import NormaSpotrebnihoKose

@admin.register(NormaSpotrebnihoKose)
class NormaSKAdmin(admin.ModelAdmin):
    list_display = ("stravovaci_skupina", "skupina_sk", "norma_kg_mesic")
    list_filter = ("stravovaci_skupina", "skupina_sk")
    search_fields = ("stravovaci_skupina__nazev",)


class MesicniSKForm(forms.Form):
    rok = forms.IntegerField(initial=date.today().year, label="Rok")
    mesic = forms.IntegerField(
        initial=date.today().month, min_value=1, max_value=12, label="Měsíc"
    )
    stravovaci_skupina = forms.ModelChoiceField(
        queryset=StravovaciSkupina.objects.all(),
        required=False,
        label="Stravovací skupina",
    )


def spocitej_spotrebu_sk_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    qs = (
        PohybSkladu.objects.filter(
            typ="VYDEJ",
            vydejka__datum__year=rok,
            vydejka__datum__month=mesic,
        )
        .select_related("surovina", "vydejka")
    )
    if stravovaci_skupina is not None:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    vysledky = defaultdict(Decimal)
    for pohyb in qs:
        sk = pohyb.surovina.skupina_sk
        koef = pohyb.surovina.koeficient_sk or Decimal("1.0")
        vysledky[sk] += pohyb.mnozstvi * koef
    return vysledky


def spocitej_stravnikodny_mesic(rok: int, mesic: int, stravovaci_skupina=None):
    """
    Celkový počet 'strávníkoden' za měsíc = součet porcí.
    """
    qs = OrderItem.objects.filter(
        order__datum_vydeje__year=rok,
        order__datum_vydeje__month=mesic,
    )

    if stravovaci_skupina is not None:
        qs = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)

    total = qs.aggregate(celkem=Sum("quantity"))["celkem"] or 0
    return Decimal(total)


# -------------------------------------------------------------------
# Generování výdejky z objednávek + teoretická spotřeba
# -------------------------------------------------------------------

@transaction.atomic
def generate_vydejka_from_orders(datum, stravovaci_skupina, typ_stravy: str = "OBED"):
    vydejka, created = Vydejka.objects.get_or_create(
        datum=datum,
        stravovaci_skupina=stravovaci_skupina,
        typ_stravy=typ_stravy,
        defaults={"popis": "Generováno z objednávek", "uzavrena": False},
    )

    qs = OrderItem.objects.select_related("menu_item__jidlo").filter(
        order__datum_vydeje=datum,
    )

    suroviny_mnozstvi = defaultdict(lambda: Decimal("0"))

    for item in qs.prefetch_related("menu_item__jidlo__receptura__surovina"):
        jidlo = item.menu_item.jidlo
        pocet_porci = Decimal(item.quantity)
        for pol in jidlo.receptura.all():
            suroviny_mnozstvi[pol.surovina_id] += pol.mnozstvi_na_porci * pocet_porci
        vydejka.jidla.add(jidlo)

    vydejka.polozky.all().delete()

    suroviny = {
        s.id: s
        for s in Surovina.objects.filter(id__in=suroviny_mnozstvi.keys())
    }

    for surovina_id, mnozstvi in suroviny_mnozstvi.items():
        if mnozstvi <= 0:
            continue
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=suroviny[surovina_id],
            mnozstvi=mnozstvi,
        )

    return vydejka, created


def spocitej_spotrebu_pro_vydejku(vydejka: Vydejka):
    order_items = (
        OrderItem.objects
        .select_related("menu_item__jidlo", "order__user")
        .filter(order__datum_vydeje=vydejka.datum)
    )

    spotreba = defaultdict(Decimal)

    for item in order_items:
        jidlo = item.menu_item.jidlo
        pocet_porci = Decimal(item.quantity)

        receptura = RecepturaPolozka.objects.filter(jidlo=jidlo).select_related(
            "surovina"
        )
        for r in receptura:
            celkem = (r.mnozstvi_na_porci or Decimal("0")) * pocet_porci
            spotreba[r.surovina_id] += celkem

    return spotreba


# -------------------------------------------------------------------
# Skladový dashboard + měsíční spotřební koš
# -------------------------------------------------------------------

@admin.register(SkladDashboard)
class SkladDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/sklad/dashboard.html"

    def get_queryset(self, request):
        return SkladDashboard.objects.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "spotrebni-kos/",
                self.admin_site.admin_view(self.mesicni_spotrebni_kos_view),
                name="sklad_mesicni_spotrebni_kos",
            ),
        ]
        return custom + urls

    def mesicni_spotrebni_kos_view(self, request):
        form = MesicniSKForm(request.GET or None)
        rows = None
        circle_row = None

        if form.is_valid():
            rok = form.cleaned_data["rok"]
            mesic = form.cleaned_data["mesic"]
            skupina = form.cleaned_data["stravovaci_skupina"]

            # 1) celková spotřeba v kg za měsíc
            spotreba = spocitej_spotrebu_sk_mesic(rok, mesic, skupina)

            # 2) počet strávníkoden
            stravnikodny = spocitej_stravnikodny_mesic(rok, mesic, skupina) or Decimal("0")

            # 3) načíst normy
            normy_qs = NormaSpotrebnihoKose.objects.all()
            if skupina is not None:
                normy_qs = normy_qs.filter(stravovaci_skupina=skupina)
            normy_dict = {
                (n.stravovaci_skupina_id, n.skupina_sk): n.norma_kg_mesic
                for n in normy_qs
            }

            rows = []
            skupiny_sk = dict(Surovina.SKUPINA_SK)
            sk_id = skupina.id if skupina else None

            for kod, label in skupiny_sk.items():
                celkem_kg = spotreba.get(kod, Decimal("0"))
                if stravnikodny > 0:
                    na_stravnika = celkem_kg / stravnikodny
                else:
                    na_stravnika = Decimal("0")

                norma = normy_dict.get((sk_id, kod)) if sk_id else None
                if norma and norma > 0:
                    plneni = (na_stravnika / norma) * Decimal("100")
                else:
                    plneni = None

                rows.append(
                    {
                        "kod": kod,
                        "label": label,
                        "celkem_kg": celkem_kg,
                        "stravnikodny": stravnikodny,
                        "na_stravnika": na_stravnika,
                        "norma": norma,
                        "plneni": plneni,
                    }
                )

            # vybereme řádek pro kruhový graf (první s plněním)
            for r in rows:
                if r["plneni"] is not None:
                    circle_row = r
                    break

        context = dict(
            self.admin_site.each_context(request),
            title="Měsíční spotřební koš",
            form=form,
            rows=rows,
            circle_row=circle_row,
        )
        return TemplateResponse(
            request,
            "admin/sklad/mesicni_spotrebni_kos.html",
            context,
        )

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}

        date_str = request.GET.get("date")
        if date_str:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = timezone.localdate()
        else:
            target_date = timezone.localdate()

        items_all = OrderItem.objects.select_related(
            "menu_item__jidlo"
        ).filter(order__datum_vydeje=target_date)
        items_issued = items_all.filter(vydano=True)

        expected = defaultdict(lambda: 0)
        real = defaultdict(lambda: 0)

        for item in items_all.prefetch_related(
            "menu_item__jidlo__receptura__surovina"
        ):
            jidlo = item.menu_item.jidlo
            quantity = item.quantity
            for pol in jidlo.receptura.all():
                key = pol.surovina_id
                mnozstvi = pol.mnozstvi_na_porci * quantity
                expected[key] += mnozstvi

        for item in items_issued.prefetch_related(
            "menu_item__jidlo__receptura__surovina"
        ):
            jidlo = item.menu_item.jidlo
            quantity = item.quantity
            for pol in jidlo.receptura.all():
                key = pol.surovina_id
                mnozstvi = pol.mnozstvi_na_porci * quantity
                real[key] += mnozstvi

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

            rows.append(
                {
                    "surovina": s,
                    "expected": exp,
                    "real": real_mnozstvi,
                    "stav": stav_mnozstvi,
                    "min": min_mnozstvi,
                    "pod_min": pod_min,
                }
            )

        extra_context["target_date"] = target_date
        extra_context["rows"] = rows

        return super().changelist_view(request, extra_context=extra_context)


# -------------------------------------------------------------------
# Základní sklad
# -------------------------------------------------------------------

@admin.register(Surovina)
class SurovinaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "jednotka", "skupina_sk", "koeficient_sk")
    list_filter = ("jednotka", "skupina_sk")
    search_fields = ("nazev",)
    fields = ("nazev", "jednotka", "skupina_sk", "koeficient_sk")


@admin.register(StavSkladu)
class StavSkladuAdmin(admin.ModelAdmin):
    list_display = ("surovina", "mnozstvi", "min_mnozstvi")
    readonly_fields = ("surovina", "mnozstvi")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PohybSkladu)
class PohybSkladuAdmin(admin.ModelAdmin):
    list_display = ("datum", "surovina", "typ", "mnozstvi", "doklad_link", "poznamka")
    list_filter = ("typ", "datum", "surovina")
    search_fields = ("surovina__nazev", "vydejka__id", "prijem__id", "poznamka")
    date_hierarchy = "datum"

    def doklad_link(self, obj):
        if obj.vydejka_id:
            url = f"/admin/sklad/vydejka/{obj.vydejka_id}/change/"
            return format_html('<a href="{}">Výdejka #{}</a>', url, obj.vydejka_id)
        if obj.prijem_id:
            url = f"/admin/sklad/prijemskladu/{obj.prijem_id}/change/"
            return format_html('<a href="{}">Příjem #{}</a>', url, obj.prijem_id)
        return "-"

    doklad_link.short_description = "Doklad"


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
        if obj.uzavreny:
            for pol in obj.polozky.select_related("surovina").all():
                stav, _ = StavSkladu.objects.get_or_create(
                    surovina=pol.surovina,
                    defaults={"mnozstvi": 0, "min_mnozstvi": 0},
                )
                stav.mnozstvi = stav.mnozstvi + pol.mnozstvi
                stav.save(update_fields=["mnozstvi"])

                PohybSkladu.objects.create(
                    surovina=pol.surovina,
                    typ="PRIJEM",
                    mnozstvi=pol.mnozstvi,
                    prijem=obj,
                    poznamka=f"Příjem #{obj.id}",
                )
        return super().response_change(request, obj)

    @transaction.atomic
    def delete_model(self, request, obj):
        for pohyb in obj.pohyby.all():
            stav = StavSkladu.objects.select_for_update().get(
                surovina=pohyb.surovina
            )
            stav.mnozstvi = stav.mnozstvi - pohyb.mnozstvi
            stav.save(update_fields=["mnozstvi"])
        obj.pohyby.all().delete()
        super().delete_model(request, obj)


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
        return False

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

        if not change:
            self._napln_polozky_ze_stavu(obj)

    def _napln_polozky_ze_stavu(self, inventura):
        stavy = StavSkladu.objects.select_related("surovina").all()
        polozky = []
        for stav in stavy:
            polozky.append(
                PolozkaInventury(
                    inventura=inventura,
                    surovina=stav.surovina,
                    stav_pred=stav.mnozstvi,
                    fyzicky_stav=stav.mnozstvi,
                    rozdil=Decimal("0"),
                )
            )
        PolozkaInventury.objects.bulk_create(polozky)

    @transaction.atomic
    def response_change(self, request, obj):
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
    list_display = ("id", "datum", "vytvoril", "pocet_polozek")
    list_filter = ("datum",)
    search_fields = ("id", "vytvoril__username")
    inlines = [PolozkaInventuryReadOnlyInline]

    readonly_fields = ("datum", "popis", "vytvoril", "uzavrena")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(uzavrena=True)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False

    def pocet_polozek(self, obj):
        return obj.polozky.count()

    pocet_polozek.short_description = "Počet položek"


class PolozkaVydejkyInline(admin.TabularInline):
    model = PolozkaVydejky
    extra = 1


@admin.register(Vydejka)
class VydejkaAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "stravovaci_skupina", "typ_stravy", "uzavrena")
    list_filter = ("typ_stravy", "stravovaci_skupina", "datum", "uzavrena")
    search_fields = ("id", "stravovaci_skupina__nazev", "popis")
    readonly_fields = ("vytvoril",)
    inlines = [PolozkaVydejkyInline]

    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": ("datum", "stravovaci_skupina", "typ_stravy", "popis"),
            },
        ),
        (
            "Objednané receptury a suroviny",
            {
                "fields": (),
                "description": "",
            },
        ),
        (
            "Stav a audit",
            {
                "fields": ("uzavrena", "vytvoril"),
            },
        ),
    )

    actions = [
        "akce_vygenerovat_z_objednavek",
        "uzavrit_a_odepsat_ze_skladu",
    ]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:vydejka_id>/pdf/",
                self.admin_site.admin_view(vydejka_pdf_view),
                name="sklad_vydejka_pdf",
            ),
        ]
        return custom_urls + urls

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

    @transaction.atomic
    def response_change(self, request, obj):
        if obj.uzavrena:
            for pol in obj.polozky.select_related("surovina").all():
                stav, _ = StavSkladu.objects.get_or_create(
                    surovina=pol.surovina,
                    defaults={"mnozstvi": 0, "min_mnozstvi": 0},
                )
                stav.mnozstvi = stav.mnozstvi - pol.mnozstvi
                stav.save(update_fields=["mnozstvi"])

                PohybSkladu.objects.create(
                    surovina=pol.surovina,
                    typ="VYDEJ",
                    mnozstvi=pol.mnozstvi,
                    vydejka=obj,
                    poznamka=f"Výdejka #{obj.id}",
                )
        return super().response_change(request, obj)

    @transaction.atomic
    def delete_model(self, request, obj):
        for pohyb in obj.pohyby.all():
            stav = StavSkladu.objects.select_for_update().get(
                surovina=pohyb.surovina
            )
            stav.mnozstvi = stav.mnozstvi + pohyb.mnozstvi
            stav.save(update_fields=["mnozstvi"])
        obj.pohyby.all().delete()
        super().delete_model(request, obj)

    @admin.action(
        description="Uzavřít výdejky a promítnout do skladu (podle teoretické spotřeby)"
    )
    def uzavrit_a_odepsat_ze_skladu(self, request, queryset):
        uzavreno = 0
        for vydejka in queryset:
            if vydejka.uzavrena:
                continue

            spotreba = spocitej_spotrebu_pro_vydejku(vydejka)

            for surovina_id, mnozstvi in spotreba.items():
                if mnozstvi <= 0:
                    continue

                surovina = Surovina.objects.get(pk=surovina_id)

                stav, _ = StavSkladu.objects.get_or_create(
                    surovina=surovina,
                    defaults={"mnozstvi": Decimal("0")},
                )
                stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
                stav.save()

                PohybSkladu.objects.create(
                    surovina=surovina,
                    typ="VYDEJ",
                    mnozstvi=mnozstvi,
                    vydejka=vydejka,
                    poznamka=f"Výdej podle výdejky #{vydejka.id}",
                )

            vydejka.uzavrena = True
            vydejka.save()
            uzavreno += 1

        self.message_user(
            request, f"Uzavřeno a promítnuto do skladu: {uzavreno} výdejek."
        )

    @admin.action(
        description="Vygenerovat / přepočítat z objednávek pro zvolené výdejky"
    )
    def akce_vygenerovat_z_objednavek(self, request, queryset):
        pocet = 0
        for vydejka in queryset:
            generate_vydejka_from_orders(
                datum=vydejka.datum,
                stravovaci_skupina=vydejka.stravovaci_skupina,
                typ_stravy=vydejka.typ_stravy,
            )
            pocet += 1

        messages.success(
            request,
            f"Výdejky byly vygenerovány/přepočítány z objednávek ({pocet} ks).",
        )

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))

        if obj and obj.pk:
            title, opts = fieldsets[1]
            opts = dict(opts)

            pdf_button = f"""
                <a href="/admin/sklad/vydejka/{obj.id}/pdf/"
                   class="btn btn-primary"
                   style="margin-bottom: 1em; display: inline-block;">
                    📄 Stáhnout PDF výdejky
                </a>
            """

            opts["description"] = mark_safe(pdf_button + self.objednavky_rekap(obj))
            fieldsets[1] = (title, opts)

        return fieldsets

    def objednavky_rekap(self, obj):
        from django.utils.html import escape

        if not obj or not obj.pk:
            return "Ulož výdejku, aby bylo co spočítat."

        order_items = (
            OrderItem.objects
            .select_related("menu_item__jidlo", "order__user")
            .filter(order__datum_vydeje=obj.datum)
        )

        debug = [
            f"datum: {obj.datum}",
            f"typ_stravy: {obj.typ_stravy}",
            f"stravovaci_skupina: {obj.stravovaci_skupina}",
            f"order_items po datu: {order_items.count()}",
        ]

        if not order_items.exists():
            return mark_safe(
                "<br>".join(escape(line) for line in debug)
                + "<br><strong>Pro tento den nejsou žádné objednávky.</strong>"
            )

        porce_per_jidlo = defaultdict(Decimal)
        jidla = {}

        for item in order_items:
            jidlo = item.menu_item.jidlo
            jidla[jidlo.id] = jidlo
            porce_per_jidlo[jidlo.id] += Decimal(item.quantity)

        bloky = []

        for jidlo_id, pocet_porci in porce_per_jidlo.items():
            jidlo = jidla[jidlo_id]

            header_html = (
                "<h4 style='margin-top: 1em;'>"
                f"{jidlo.nazev} "
                "<br/>"
                f"<span style='font-weight: normal; color: #666;'>(porcí: {pocet_porci})</span>"
                "</h4>"
            )

            suroviny_rows = []
            for pol in jidlo.receptura.select_related("surovina").all():
                celk_mnozstvi = pol.mnozstvi_na_porci * pocet_porci
                suroviny_rows.append(
                    "<tr>"
                    f"<td>{pol.surovina.nazev}</td>"
                    f"<td style='text-align:right;'>{pol.mnozstvi_na_porci} "
                    f"{pol.surovina.jednotka}</td>"
                    f"<td style='text-align:right;'>{celk_mnozstvi} "
                    f"{pol.surovina.jednotka}</td>"
                    "</tr>"
                )

            if suroviny_rows:
                table_html = (
                    '<table class="table" style="width: 100%; margin-bottom: 0.5em;">'
                    "<thead>"
                    "<tr>"
                    "<th>Surovina</th>"
                    '<th style="text-align:right;">Na 1 porci</th>'
                    '<th style="text-align:right;">Celkem pro všechny porce</th>'
                    "</tr>"
                    "</thead>"
                    "<tbody>"
                    + "".join(suroviny_rows)
                    + "</tbody>"
                    "</table>"
                )
            else:
                table_html = "<p style='color:#888;'>Jídlo nemá vyplněnou recepturu.</p>"

            bloky.append(header_html + table_html)

        return mark_safe("".join(bloky))

    objednavky_rekap.short_description = "Rekapitulace jídel a surovin"


def vydejka_pdf_view(request, vydejka_id):
    from django.utils.html import escape

    vydejka = get_object_or_404(Vydejka, pk=vydejka_id)

    font_path = os.path.join(settings.BASE_DIR, "static", "fonts", "DejaVuSans.ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))

    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "DejaVuSans"
    styles["Title"].fontName = "DejaVuSans"
    styles["Heading2"].fontName = "DejaVuSans"

    order_items = (
        OrderItem.objects
        .select_related("menu_item__jidlo", "order__user")
        .filter(order__datum_vydeje=vydejka.datum)
    )

    if not order_items.exists():
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm
        )
        elements = []

        sk = vydejka.stravovaci_skupina.kod if vydejka.stravovaci_skupina else "bez skupiny"
        elements.append(
            Paragraph(
                f"Výdejka #{vydejka.id} – {vydejka.datum.strftime('%d.%m.%Y')} ({sk})",
                styles["Title"],
            )
        )
        elements.append(Spacer(1, 0.5 * cm))
        elements.append(
            Paragraph("Pro tento den nejsou žádné objednávky.", styles["Normal"])
        )

        doc.build(elements)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="vydejka_{vydejka.id}.pdf"'
        )
        return response

    porce_per_jidlo = defaultdict(Decimal)
    jidla = {}
    for item in order_items:
        jidlo = item.menu_item.jidlo
        jidla[jidlo.id] = jidlo
        porce_per_jidlo[jidlo.id] += Decimal(item.quantity)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )
    elements = []

    sk = vydejka.stravovaci_skupina.kod if vydejka.stravovaci_skupina else "bez skupiny"
    nadpis = f"Výdejka #{vydejka.id} – {vydejka.datum.strftime('%d.%m.%Y')} ({sk})"
    elements.append(Paragraph(nadpis, styles["Title"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(
        Paragraph(
            f"Typ stravy: {vydejka.get_typ_stravy_display()}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 0.7 * cm))

    for jidlo_id, pocet_porci in porce_per_jidlo.items():
        jidlo = jidla[jidlo_id]

        elements.append(
            Paragraph(
                f"<b>{escape(jidlo.nazev)}</b> – {pocet_porci} ks",
                styles["Heading2"],
            )
        )
        elements.append(Spacer(1, 0.3 * cm))

        receptura = RecepturaPolozka.objects.filter(jidlo=jidlo).select_related(
            "surovina"
        )

        if receptura.exists():
            data = [["Surovina", "Na 1 porci", "Celkem pro všechny porce"]]
            for r in receptura.order_by("surovina__nazev"):
                na_porci = r.mnozstvi_na_porci
                celkem = na_porci * pocet_porci
                data.append(
                    [
                        r.surovina.nazev,
                        f"{na_porci:.3f} {r.surovina.jednotka}",
                        f"{celkem:.3f} {r.surovina.jednotka}",
                    ]
                )

            table = Table(data, colWidths=[7 * cm, 4 * cm, 5 * cm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                        ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans"),
                        ("FONTNAME", (0, 1), (-1, -1), "DejaVuSans"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ]
                )
            )
            elements.append(table)
        else:
            elements.append(
                Paragraph(
                    "<i>Jídlo nemá vyplněnou recepturu.</i>",
                    styles["Normal"],
                )
            )

        elements.append(Spacer(1, 0.6 * cm))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="vydejka_{vydejka.id}.pdf"'
    return response
