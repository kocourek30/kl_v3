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
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

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

from django.contrib.admin import ModelAdmin

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
    ReportNakladySkladu,
    ToleranceSpotrebnihoKose,
)

from .services import (
    spocitej_spotrebu_sk_mesic,
    priprav_radky_spotrebi_kos_tabulka,
    spocitej_naklady_mesic,
    priprav_naklady_podle_skupin_sk,
    spocitej_podil_masnych_vyrobku,
    spocitej_podil_bio,
    spocitej_volny_cukr,
)

from .forms import SpotrebniKosForm


# -------------------------------------------------------------------
# Formulář pro náklady skladu
# -------------------------------------------------------------------


class MesicniNakladyForm(forms.Form):
    rok = forms.IntegerField(label="Rok")
    mesic = forms.IntegerField(min_value=1, max_value=12, label="Měsíc")
    stravovaci_skupina = forms.ModelChoiceField(
        queryset=StravovaciSkupina.objects.all(),
        required=False,
        label="Stravovací skupina",
        help_text="Volitelné – bez výběru se počítají náklady za všechny skupiny.",
    )


@admin.register(ReportNakladySkladu)
class ReportNakladySkladuAdmin(admin.ModelAdmin):
    """
    Pseudo-model pro zobrazení reportu v adminu.
    """

    def get_queryset(self, request):
        return ReportNakladySkladu.objects.none()

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
                "",
                self.admin_site.admin_view(self.report_view),
                name="sklad_report_naklady",
            ),
        ]
        return custom + urls

    def report_view(self, request):
        today = date.today()
        initial = {"rok": today.year, "mesic": today.month}
        form = MesicniNakladyForm(request.GET or None, initial=initial)

        souhrnne_naklady = None
        naklady_podle_sk = None

        if form.is_valid():
            rok = form.cleaned_data["rok"]
            mesic = form.cleaned_data["mesic"]
            skupina = form.cleaned_data["stravovaci_skupina"]

            souhrnne_naklady = spocitej_naklady_mesic(rok, mesic, skupina)
            naklady_podle_sk = priprav_naklady_podle_skupin_sk(rok, mesic, skupina)

        context = dict(
            self.admin_site.each_context(request),
            title="Report nákladů na suroviny",
            form=form,
            souhrnne_naklady=souhrnne_naklady,
            naklady_podle_sk=naklady_podle_sk,
        )
        return TemplateResponse(
            request,
            "admin/sklad/report_naklady.html",
            context,
        )


# -------------------------------------------------------------------
# Normy a tolerance spotřebního koše
# -------------------------------------------------------------------


@admin.register(NormaSpotrebnihoKose)
class NormaSKAdmin(admin.ModelAdmin):
    list_display = ("stravovaci_skupina", "skupina_sk", "norma_g_mesic")
    list_filter = ("stravovaci_skupina", "skupina_sk")
    search_fields = ("stravovaci_skupina__nazev",)


@admin.register(ToleranceSpotrebnihoKose)
class ToleranceSKAdmin(admin.ModelAdmin):
    list_display = ("stravovaci_skupina", "skupina_sk", "min_pct", "max_pct")
    list_filter = ("stravovaci_skupina", "skupina_sk")
    search_fields = ("stravovaci_skupina__nazev",)


# -------------------------------------------------------------------
# Starší měsíční SK formulář (jen měsíc/rok)
# -------------------------------------------------------------------


class MesicniSKForm(forms.Form):
    rok = forms.IntegerField(initial=date.today().year, label="Rok")
    mesic = forms.IntegerField(
        initial=date.today().month, min_value=1, max_value=12, label="Měsíc"
    )
    stravovaci_skupina = forms.ModelChoiceField(
        queryset=StravovaciSkupina.objects.all(),
        required=False,
        label="Stravovací skupina",
        help_text="Vyber skupinu strávníků. Bez výběru se tabulka nepočítá.",
    )


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
            suroviny_mnozstvi[pol.surovina_id] += (
                pol.mnozstvi_na_porci * pocet_porci
            )
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
# Skladový dashboard (denní přehled) + starší měsíční SK view
# -------------------------------------------------------------------




# -------------------------------------------------------------------
# Nový přehled spotřebního koše (perioda z formuláře SpotrebniKosForm)
# -------------------------------------------------------------------


@admin.register(SkladDashboard)
class SkladSpotrebniKosAdmin(ModelAdmin):
    change_list_template = "admin/sklad/mesicni_spotrebni_kos.html"

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
                "mesicni-spotrebni-kos/",
                self.admin_site.admin_view(self.spotrebni_kos_view),
                name="sklad_mesicni_spotrebni_kos",
            ),
        ]
        return custom + urls

    @method_decorator(never_cache)
    def spotrebni_kos_view(self, request):
        form = SpotrebniKosForm(request.GET or None)

        rows = []
        circle_row = None
        maso_stat = None
        bio_stat = None
        volny_cukr_g = None

        if form.is_valid():
            date_from, date_to, label = form.get_period()
            stravovaci_skupina = form.cleaned_data.get("stravovaci_skupina")

            if stravovaci_skupina:
                # strávníkodny v období
                qs = OrderItem.objects.filter(
                    order__datum_vydeje__gte=date_from,
                    order__datum_vydeje__lte=date_to,
                    order__user__stravovaci_skupina=stravovaci_skupina,
                )
                stravnikodny = qs.aggregate(celkem=Sum("quantity"))["celkem"] or 0

                # pro normu použijeme rok/měsíc začátku období
                rok = date_from.year
                mesic = date_from.month

                rows = priprav_radky_spotrebi_kos_tabulka(
                    rok=rok,
                    mesic=mesic,
                    stravovaci_skupina=stravovaci_skupina,
                    pocet_stravniku=int(stravnikodny),
                    date_from=date_from,
                    date_to=date_to,
                )

                for r in rows:
                    if r["norma_g"] or r["skutecnost_g"]:
                        circle_row = type(
                            "Row",
                            (),
                            {
                                "label": label,
                                "plneni": float(r["skutecnost_pct"]),
                            },
                        )()
                        break

                maso_stat = spocitej_podil_masnych_vyrobku(
                    date_from, date_to, stravovaci_skupina
                )
                bio_stat = spocitej_podil_bio(
                    date_from, date_to, stravovaci_skupina
                )
                volny_cukr_g = spocitej_volny_cukr(
                    date_from, date_to, stravovaci_skupina
                )

        context = dict(
            self.admin_site.each_context(request),
            title="Spotřební koš – nové metodické ukazatele",
            form=form,
            rows=rows,
            circle_row=circle_row,
            maso_stat=maso_stat,
            bio_stat=bio_stat,
            volny_cukr_g=volny_cukr_g,
        )
        return TemplateResponse(
            request,
            "admin/sklad/mesicni_spotrebni_kos.html",
            context,
        )


# -------------------------------------------------------------------
# Základní sklad – admin
# -------------------------------------------------------------------


@admin.register(Surovina)
class SurovinaAdmin(admin.ModelAdmin):
    list_display = (
        "nazev",
        "jednotka",
        "skupina_sk",
        "je_masny_vyrobek",
        "je_bio",
        "koeficient_sk",
        "hmotnost_ks_g_display",
        "prumerna_cena_za_jednotku",
    )
    list_filter = ("jednotka", "skupina_sk", "je_masny_vyrobek", "je_bio")
    search_fields = ("nazev",)
    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": ("nazev", "jednotka"),
            },
        ),
        (
            "Spotřební koš",
            {
                "fields": (
                    "skupina_sk",
                    "koeficient_sk",
                    "je_masny_vyrobek",
                    "je_bio",
                    "podil_celozrnne_slozky",
                    "volny_cukr_na_100g",
                ),
            },
        ),
        (
            "Hmotnost a ceny",
            {
                "fields": (
                    "hmotnost_ks_g",
                    "prumerna_cena_za_jednotku",
                ),
            },
        ),
    )
    readonly_fields = ("prumerna_cena_za_jednotku",)

    def hmotnost_ks_g_display(self, obj):
        if obj.jednotka != "ks":
            return "-"
        if obj.hmotnost_ks_g is None:
            return "nenastaveno"
        return f"{obj.hmotnost_ks_g} g"

    hmotnost_ks_g_display.short_description = "g / ks"


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
                surovina = pol.surovina

                stav, _ = StavSkladu.objects.select_for_update().get_or_create(
                    surovina=surovina,
                    defaults={"mnozstvi": Decimal("0"), "min_mnozstvi": Decimal("0")},
                )

                # původní stav
                st_mnozstvi = stav.mnozstvi or Decimal("0")
                st_cena = surovina.prumerna_cena_za_jednotku or Decimal("0")

                pr_mnozstvi = pol.mnozstvi or Decimal("0")
                pr_cena = pol.jednotkova_cena or Decimal("0")

                nove_mnozstvi = st_mnozstvi + pr_mnozstvi

                if nove_mnozstvi > 0 and pr_cena is not None:
                    # vážený průměr
                    if st_mnozstvi > 0 and st_cena is not None:
                        nova_cena = (
                            st_mnozstvi * st_cena + pr_mnozstvi * pr_cena
                        ) / nove_mnozstvi
                    else:
                        # sklad byl prázdný nebo bez ceny -> vezmeme cenu z příjmu
                        nova_cena = pr_cena

                    surovina.prumerna_cena_za_jednotku = nova_cena
                    surovina.save(update_fields=["prumerna_cena_za_jednotku"])

                # aktualizace množství na skladě
                stav.mnozstvi = nove_mnozstvi
                stav.save(update_fields=["mnozstvi"])

                # pohyb s cenou za jednotku
                PohybSkladu.objects.create(
                    surovina=surovina,
                    typ="PRIJEM",
                    mnozstvi=pr_mnozstvi,
                    cena_za_jednotku=pr_cena,
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
        # vezmeme všechny suroviny, i ty bez stavu
        suroviny = Surovina.objects.select_related("stav").all()
        polozky = []
        for s in suroviny:
            stav = getattr(s, "stav", None)
            stav_mnozstvi = stav.mnozstvi if stav else Decimal("0")
            polozky.append(
                PolozkaInventury(
                    inventura=inventura,
                    surovina=s,
                    stav_pred=stav_mnozstvi,
                    fyzicky_stav=stav_mnozstvi,
                    rozdil=Decimal("0"),
                )
            )
        PolozkaInventury.objects.bulk_create(polozky)

    @transaction.atomic
    def response_change(self, request, obj):
        if obj.uzavrena:
            # vždy při uložení uzavřené inventury přepiš stav skladu
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
    @transaction.atomic
    def uzavrit_a_odepsat_ze_skladu(self, request, queryset):
        uzavreno = 0
        for vydejka in queryset.select_for_update():
            if vydejka.uzavrena:
                continue

            spotreba = spocitej_spotrebu_pro_vydejku(vydejka)

            for surovina_id, mnozstvi in spotreba.items():
                if mnozstvi <= 0:
                    continue

                surovina = Surovina.objects.select_related("stav").get(pk=surovina_id)

                prumerna_cena = surovina.prumerna_cena_za_jednotku

                stav, _ = StavSkladu.objects.select_for_update().get_or_create(
                    surovina=surovina,
                    defaults={"mnozstvi": Decimal("0"), "min_mnozstvi": Decimal("0")},
                )

                stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - (mnozstvi or Decimal("0"))
                stav.save(update_fields=["mnozstvi"])

                PohybSkladu.objects.create(
                    surovina=surovina,
                    typ="VYDEJ",
                    mnozstvi=mnozstvi,
                    cena_za_jednotku=prumerna_cena,
                    vydejka=vydejka,
                    poznamka=f"Výdej podle výdejky #{vydejka.id}",
                )

            vydejka.uzavrena = True
            vydejka.save(update_fields=["uzavrena"])
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

        sk = (
            vydejka.stravovaci_skupina.kod
            if vydejka.stravovaci_skupina
            else "bez skupiny"
        )
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

    sk = (
        vydejka.stravovaci_skupina.kod
        if vydejka.stravovaci_skupina
        else "bez skupiny"
    )
    nadpis = (
        f"Výdejka #{vydejka.id} – {vydejka.datum.strftime('%d.%m.%Y')} ({sk})"
    )
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
    response["Content-Disposition"] = (
        f'attachment; filename="vydejka_{vydejka.id}.pdf"'
    )
    return response
