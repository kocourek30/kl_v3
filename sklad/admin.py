from datetime import date
from decimal import Decimal
from io import BytesIO
import os

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path
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
    KomponentaJidla,
    KomponentaSurovina,
    JidloKomponenta,
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
    generate_vydejka_from_orders,
    objednavky_rekap_data,
    get_order_items_for_vydejka,
    najdi_nedostatecne_stavy_pro_vydejku,
    uzavri_prijem,
    uzavri_vydejku,
    uzavri_inventuru,
    validace_surovin_pro_sk,
    spocitej_stravnikodny_obdobi,
    spocitej_spotrebu_sk_mesic,
    priprav_radky_spotrebi_kos_tabulka,
    spocitej_naklady_mesic,
    priprav_naklady_podle_skupin_sk,
    spocitej_podil_masnych_vyrobku,
    spocitej_podil_bio,
    spocitej_volny_cukr,
)

from .forms import SpotrebniKosForm


def _prepare_uzavreni_po_ulozeni(model, obj, change):
    """
    Admin nejdřív ukládá model a až potom inline položky.
    Pokud uživatel zaškrtne uzavření, necháme doklad dočasně otevřený
    a skladovou službu zavoláme až po uložení všech položek.
    """
    puvodni_uzavreny = False
    if change and obj.pk:
        puvodni_uzavreny = bool(
            model.objects
            .filter(pk=obj.pk)
            .values_list("uzavreny", flat=True)
            .first()
        )

    uzavrit_po_ulozeni = bool(obj.uzavreny and not puvodni_uzavreny)
    obj._puvodni_uzavreny = puvodni_uzavreny
    obj._uzavrit_po_ulozeni = uzavrit_po_ulozeni

    if uzavrit_po_ulozeni:
        obj.uzavreny = False
        if hasattr(obj, "uzavren_at"):
            obj.uzavren_at = None
        if hasattr(obj, "uzavrel"):
            obj.uzavrel = None

    return obj


def _dopln_vydejku_z_objednavek_pokud_je_prazdna(vydejka):
    if not vydejka.polozky.exists():
        generate_vydejka_from_orders(
            datum=vydejka.datum,
            stravovaci_skupina=vydejka.stravovaci_skupina,
            typ_stravy=vydejka.typ_stravy,
        )


def _upozorni_na_nedostatecne_stavy(request, vydejka):
    nedostatky = najdi_nedostatecne_stavy_pro_vydejku(vydejka)
    if not nedostatky:
        return

    popis = ", ".join(
        f"{radek['surovina'].nazev}: chybí {radek['chybi']} {radek['surovina'].jednotka}"
        for radek in nedostatky[:8]
    )
    if len(nedostatky) > 8:
        popis += f" a dalších {len(nedostatky) - 8}"

    messages.warning(
        request,
        f"Výdejka #{vydejka.id} odepsala některé suroviny do mínusu: {popis}.",
    )


def _stav_dokladu_text(obj):
    if not obj:
        return "Rozpracováno"
    if getattr(obj, "stornovano", False):
        return "Stornováno"
    if getattr(obj, "uzavreny", False):
        return "Uzavřeno a promítnuto do skladu"
    return "Rozpracováno, sklad ještě nebyl změněn"


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
        data_warnings = validace_surovin_pro_sk()

        if form.is_valid():
            date_from, date_to, label = form.get_period()
            stravovaci_skupina = form.cleaned_data.get("stravovaci_skupina")

            if stravovaci_skupina:
                qs = OrderItem.objects.filter(
                    order__datum_vydeje__gte=date_from,
                    order__datum_vydeje__lte=date_to,
                    order__user__stravovaci_skupina=stravovaci_skupina,
                )
                stravnikodny = qs.aggregate(celkem=Sum("quantity"))["celkem"] or 0

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
                    if r.get("norma_g") or r.get("skutecnost_g"):
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
            data_warnings=data_warnings,
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
        if getattr(obj, "inventura_id", None):
            url = f"/admin/sklad/inventura/{obj.inventura_id}/change/"
            return format_html('<a href="{}">Inventura #{}</a>', url, obj.inventura_id)
        return "-"

    doklad_link.short_description = "Doklad"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class RecepturaPolozkaInline(admin.TabularInline):
    model = RecepturaPolozka
    extra = 1


class KomponentaSurovinaInline(admin.TabularInline):
    model = KomponentaSurovina
    extra = 1
    autocomplete_fields = ("surovina",)


class JidloKomponentaInline(admin.TabularInline):
    model = JidloKomponenta
    extra = 1
    autocomplete_fields = ("komponenta",)
    ordering = ("poradi", "id")


class PolozkaPrijmuInline(admin.TabularInline):
    model = PolozkaPrijmu
    extra = 1
    autocomplete_fields = ("surovina",)

    def get_extra(self, request, obj=None, **kwargs):
        if obj and obj.uzavreny:
            return 0
        return super().get_extra(request, obj, **kwargs)

    def has_add_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_delete_permission(request, obj)


class PolozkaInventuryInline(admin.TabularInline):
    model = PolozkaInventury
    extra = 0
    readonly_fields = ("stav_pred", "rozdil")
    autocomplete_fields = ("surovina",)

    def has_add_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_delete_permission(request, obj)


class PohybSkladuInlineBase(admin.TabularInline):
    model = PohybSkladu
    extra = 0
    can_delete = False
    fields = ("datum", "surovina", "typ", "mnozstvi", "cena_za_jednotku", "poznamka")
    readonly_fields = fields
    verbose_name = "Skladový pohyb"
    verbose_name_plural = "Skladové pohyby"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


class PohybPrijmuInline(PohybSkladuInlineBase):
    fk_name = "prijem"


class PohybVydejkyInline(PohybSkladuInlineBase):
    fk_name = "vydejka"


class PohybInventuryInline(PohybSkladuInlineBase):
    fk_name = "inventura"


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


@admin.register(KomponentaJidla)
class KomponentaJidlaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "typ", "aktivni", "porce_text")
    list_filter = ("typ", "aktivni")
    search_fields = ("nazev",)
    inlines = [KomponentaSurovinaInline]


@admin.register(PrijemSkladu)
class PrijemSkladuAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "vytvoril", "uzavreny", "uzavren_at", "uzavrel")
    list_filter = ("uzavreny", "datum")
    inlines = [PolozkaPrijmuInline, PohybPrijmuInline]
    readonly_fields = ("stav_dokladu", "vytvoril", "uzavren_at", "uzavrel")
    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": ("datum", "popis"),
            },
        ),
        (
            "Stav a audit",
            {
                "fields": ("stav_dokladu", "uzavreny", "vytvoril", "uzavren_at", "uzavrel"),
            },
        ),
    )

    def stav_dokladu(self, obj):
        return _stav_dokladu_text(obj)

    stav_dokladu.short_description = "Stav dokladu"

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

    def save_form(self, request, form, change):
        obj = super().save_form(request, form, change)
        return _prepare_uzavreni_po_ulozeni(PrijemSkladu, obj, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance

        if getattr(obj, "_uzavrit_po_ulozeni", False):
            uzavri_prijem(obj, user=request.user)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj and obj.uzavreny:
            ro += ["datum", "popis", "uzavreny"]
        return ro

    def has_delete_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_change_permission(request, obj)


@admin.register(Inventura)
class InventuraAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "vytvoril", "uzavreny", "uzavren_at", "uzavrel")
    list_filter = ("uzavreny", "datum")
    readonly_fields = ("stav_dokladu", "vytvoril", "uzavren_at", "uzavrel")
    inlines = [PolozkaInventuryInline, PohybInventuryInline]
    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": ("datum", "popis"),
            },
        ),
        (
            "Stav a audit",
            {
                "fields": ("stav_dokladu", "uzavreny", "vytvoril", "uzavren_at", "uzavrel"),
            },
        ),
    )

    def stav_dokladu(self, obj):
        return _stav_dokladu_text(obj)

    stav_dokladu.short_description = "Stav dokladu"

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

        if not change:
            self._napln_polozky_ze_stavu(obj)

    def save_form(self, request, form, change):
        obj = super().save_form(request, form, change)
        return _prepare_uzavreni_po_ulozeni(Inventura, obj, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance

        if getattr(obj, "_uzavrit_po_ulozeni", False):
            uzavri_inventuru(obj, user=request.user)

    def _napln_polozky_ze_stavu(self, inventura):
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

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj and obj.uzavreny:
            ro += ["datum", "popis", "uzavreny"]
        return ro

    def has_change_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_change_permission(request, obj)


@admin.register(InventurniDoklad)
class InventurniDokladAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "vytvoril", "pocet_polozek")
    list_filter = ("datum",)
    search_fields = ("id", "vytvoril__username")
    inlines = [PolozkaInventuryReadOnlyInline]
    readonly_fields = ("datum", "popis", "vytvoril", "uzavreny", "uzavren_at", "uzavrel")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(uzavreny=True)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False

    def pocet_polozek(self, obj):
        return obj.polozky.count()

    pocet_polozek.short_description = "Počet položek"


@admin.register(Vydejka)
class VydejkaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "datum",
        "stravovaci_skupina",
        "typ_stravy",
        "uzavreny",
        "uzavren_at",
        "uzavrel",
    )
    list_filter = ("typ_stravy", "stravovaci_skupina", "datum", "uzavreny")
    search_fields = ("id", "stravovaci_skupina__nazev", "popis")
    readonly_fields = ("stav_dokladu", "vytvoril", "uzavren_at", "uzavrel")
    inlines = [PohybVydejkyInline]

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
                "fields": ("stav_dokladu", "uzavreny", "vytvoril", "uzavren_at", "uzavrel"),
            },
        ),
    )

    actions = [
        "akce_vygenerovat_z_objednavek",
        "uzavrit_vydejky",
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

    def stav_dokladu(self, obj):
        return _stav_dokladu_text(obj)

    stav_dokladu.short_description = "Stav dokladu"

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.vytvoril:
            obj.vytvoril = request.user
        super().save_model(request, obj, form, change)

    def save_form(self, request, form, change):
        obj = super().save_form(request, form, change)
        return _prepare_uzavreni_po_ulozeni(Vydejka, obj, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance

        if getattr(obj, "_uzavrit_po_ulozeni", False):
            _dopln_vydejku_z_objednavek_pokud_je_prazdna(obj)
            _upozorni_na_nedostatecne_stavy(request, obj)
            uzavri_vydejku(obj, user=request.user)

    @admin.action(description="Uzavřít výdejky a promítnout do skladu")
    def uzavrit_vydejky(self, request, queryset):
        uzavreno = 0
        for vydejka in queryset:
            if not vydejka.uzavreny:
                _dopln_vydejku_z_objednavek_pokud_je_prazdna(vydejka)
                _upozorni_na_nedostatecne_stavy(request, vydejka)
            if uzavri_vydejku(vydejka, user=request.user):
                uzavreno += 1

        self.message_user(
            request,
            f"Uzavřeno a promítnuto do skladu: {uzavreno} výdejek.",
        )

    @admin.action(description="Vygenerovat / přepočítat z objednávek pro zvolené výdejky")
    def akce_vygenerovat_z_objednavek(self, request, queryset):
        pocet = 0
        for vydejka in queryset:
            if vydejka.uzavreny:
                continue
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

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj and obj.uzavreny:
            ro += ["datum", "stravovaci_skupina", "typ_stravy", "popis", "uzavreny"]
        return ro

    def has_delete_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_delete_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if obj and obj.uzavreny:
            return False
        return super().has_change_permission(request, obj)

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

        order_items = get_order_items_for_vydejka(obj)

        debug = [
            f"datum: {obj.datum}",
            f"typ_stravy: {obj.typ_stravy}",
            f"stravovaci_skupina: {obj.stravovaci_skupina}",
            f"order_items po filtrech: {order_items.count()}",
        ]

        if not order_items.exists():
            return mark_safe(
                "<br>".join(escape(line) for line in debug)
                + "<br><strong>Pro tento filtr nejsou žádné objednávky.</strong>"
            )

        porce_per_jidlo, jidla, detail = objednavky_rekap_data(obj)

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

            komponenty_html = []

            for blok in detail.get(jidlo_id, []):
                komponenta = blok["komponenta"]
                radky = blok["radky"]

                if komponenta:
                    komponenty_html.append(
                        f"<h5 style='margin: .5em 0; color:#444;'>{escape(komponenta.nazev)}</h5>"
                    )

                if radky:
                    rows_html = []
                    for row in radky:
                        rows_html.append(
                            "<tr>"
                            f"<td>{escape(row['surovina'].nazev)}</td>"
                            f"<td style='text-align:right;'>{row['na_porci']} {escape(row['surovina'].jednotka)}</td>"
                            f"<td style='text-align:right;'>{row['celkem']} {escape(row['surovina'].jednotka)}</td>"
                            "</tr>"
                        )

                    komponenty_html.append(
                        '<div style="width:100%; max-width:none; overflow-x:auto;">'
                        '<table class="table" style="width:100%; max-width:none; table-layout:auto; margin-bottom: 1em;">'
                        "<thead>"
                        "<tr>"
                        "<th style='width:40%;'>Surovina</th>"
                        '<th style="text-align:right; width:30%;">Na 1 porci</th>'
                        '<th style="text-align:right; width:30%;">Celkem pro všechny porce</th>'
                        "</tr>"
                        "</thead>"
                        "<tbody>"
                        + "".join(rows_html)
                        + "</tbody>"
                        "</table>"
                        "</div>"
                    )
                else:
                    komponenty_html.append("<p style='color:#888;'>Komponenta nemá suroviny.</p>")

            bloky.append(header_html + "".join(komponenty_html))

        return mark_safe(
            "<div style='width:100%; max-width:none;'>"
            + "".join(bloky)
            + "</div>"
        )


def vydejka_pdf_view(request, vydejka_id):
    from django.utils.html import escape

    vydejka = get_object_or_404(Vydejka, pk=vydejka_id)

    font_path = os.path.join(settings.BASE_DIR, "static", "fonts", "DejaVuSans.ttf")
    pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))

    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "DejaVuSans"
    styles["Title"].fontName = "DejaVuSans"
    styles["Heading2"].fontName = "DejaVuSans"

    order_items = get_order_items_for_vydejka(vydejka)

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
            Paragraph("Pro tento filtr nejsou žádné objednávky.", styles["Normal"])
        )

        doc.build(elements)
        pdf = buffer.getvalue()
        buffer.close()

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="vydejka_{vydejka.id}.pdf"'
        return response

    porce_per_jidlo, jidla, detail = objednavky_rekap_data(vydejka)

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
        elements.append(Spacer(1, 0.2 * cm))

        for blok in detail.get(jidlo_id, []):
            komponenta = blok["komponenta"]
            radky = blok["radky"]

            if komponenta:
                elements.append(
                    Paragraph(
                        f"<b>{escape(komponenta.nazev)}</b>",
                        styles["Normal"],
                    )
                )
                elements.append(Spacer(1, 0.15 * cm))

            if radky:
                data = [["Surovina", "Na 1 porci", "Celkem"]]
                for row in radky:
                    surovina = row["surovina"]
                    data.append([
                        surovina.nazev,
                        f"{row['na_porci']:.3f} {surovina.jednotka}",
                        f"{row['celkem']:.3f} {surovina.jednotka}",
                    ])

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
                        "<i>Komponenta nemá vyplněné suroviny.</i>",
                        styles["Normal"],
                    )
                )

            elements.append(Spacer(1, 0.4 * cm))

        elements.append(Spacer(1, 0.3 * cm))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="vydejka_{vydejka.id}.pdf"'
    return response
