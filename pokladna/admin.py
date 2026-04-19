# pokladna/admin.py

from decimal import Decimal
from io import BytesIO

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html
from django.utils.dateparse import parse_date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from kliknijidlo.pdf_utils import czech_pdf_styles, decimal_cs, html_cell, money_cs, percent_cs, safe_table

from .reports import dph_souhrn, doklady_za_obdobi, plu_obraty, trzby_podle_druhu, trzby_podle_plateb
from .services import stornuj_doklad, uzavri_denni_uzaverku
from .models import (
    DPHSkupina,
    PLUKategorie,
    PLUPolozka,
    Pokladna,
    PokladniDoklad,
    PokladniPolozka,
    PokladniSmazanaPolozka,
    PokladnaTile,
    PokladniUzaverka,
)


@admin.register(DPHSkupina)
class DPHSkupinaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "sazba")
    search_fields = ("nazev",)


@admin.register(PLUKategorie)
class PLUKategorieAdmin(admin.ModelAdmin):
    list_display = ("nazev",)
    search_fields = ("nazev",)


@admin.register(PLUPolozka)
class PLUPolozkaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "typ", "cena", "dph_skupina", "kategorie", "surovina", "jidlo", "aktivni")
    list_filter = ("aktivni", "typ", "kategorie", "dph_skupina")
    search_fields = ("nazev", "surovina__nazev", "jidlo__nazev")


class PokladniPolozkaInline(admin.TabularInline):
    model = PokladniPolozka
    extra = 0
    readonly_fields = (
        "plu",
        "nazev_snapshot",
        "mnozstvi",
        "jednotka_text",
        "cena_jednotkova",
        "dph_sazba",
        "zaklad_dph",
        "castka_dph",
        "castka_celkem",
        "skladovy_pohyb",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PokladniSmazanaPolozkaInline(admin.TabularInline):
    model = PokladniSmazanaPolozka
    extra = 0
    readonly_fields = (
        "plu",
        "nazev_snapshot",
        "mnozstvi",
        "jednotka_text",
        "cena_jednotkova",
        "dph_sazba",
        "zaklad_dph",
        "castka_dph",
        "castka_celkem",
        "smazano_at",
        "smazal",
        "duvod",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Pokladna)
class PokladnaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "aktivni", "hotovostni_zustatek", "qr_platby")
    list_filter = ("aktivni",)
    search_fields = ("nazev",)
    actions = ("uzavrit_dnesni_trzbu",)
    fieldsets = (
        (None, {
            "fields": ("nazev", "popis", "aktivni", "hotovostni_zustatek")
        }),
        ("QR platby", {
            "fields": ("qr_iban", "qr_bic", "qr_prijemce", "qr_zprava"),
            "description": "Údaje pro český platební QR kód. Doklad se odečte ze skladu až po ručním potvrzení přijaté platby.",
        }),
    )

    def qr_platby(self, obj):
        if obj.qr_iban:
            return format_html('<span style="color:#198754;font-weight:700;">Nastaveno</span>')
        return format_html('<span style="color:#dc3545;font-weight:700;">Chybí IBAN</span>')

    qr_platby.short_description = "QR platby"

    @admin.action(description="Vytvořit dnešní denní uzávěrku")
    def uzavrit_dnesni_trzbu(self, request, queryset):
        from django.utils import timezone

        for pokladna in queryset:
            try:
                uzaverka = uzavri_denni_uzaverku(pokladna, timezone.localdate(), user=request.user)
                self.message_user(
                    request,
                    f"Uzávěrka pro {pokladna} byla vytvořena: {uzaverka.celkem_trzba} Kč.",
                    level=messages.SUCCESS,
                )
            except ValidationError as exc:
                self.message_user(request, f"{pokladna}: {exc.messages[0]}", level=messages.ERROR)


@admin.register(PokladniDoklad)
class PokladniDokladAdmin(admin.ModelAdmin):
    list_display = (
        "cislo_dokladu",
        "datum",
        "pokladna",
        "stav",
        "zpusob_platby",
        "qr_stav",
        "zakaznik",
        "obsluha",
        "celkem_s_dph",
    )
    list_filter = ("stav", "zpusob_platby", "pokladna", "datum")
    search_fields = ("id", "cislo_dokladu", "obsluha__username", "zakaznik__username")
    inlines = [PokladniPolozkaInline, PokladniSmazanaPolozkaInline]
    readonly_fields = (
        "cislo_dokladu",
        "stav",
        "datum",
        "uzavren_at",
        "uzavrel",
        "stornovano_at",
        "stornoval",
        "konto_pohyb",
        "uzaverka",
        "qr_payload",
        "qr_vytvoren_at",
        "qr_potvrzen_at",
        "qr_potvrdil",
        "celkem_bez_dph",
        "celkem_dph",
        "celkem_s_dph",
    )
    actions = ("stornovat_doklady",)

    fieldsets = (
        (None, {
            "fields": (
                ("pokladna", "cislo_dokladu", "stav"),
                ("datum", "obsluha", "zakaznik"),
                "zpusob_platby",
            )
        }),
        ("Uzavření a storno", {
            "fields": (
                ("uzavren_at", "uzavrel"),
                ("stornovano_at", "stornoval"),
                "storno_duvod",
                "konto_pohyb",
                "uzaverka",
            )
        }),
        ("QR platba", {
            "fields": (
                "qr_payload",
                ("qr_vytvoren_at", "qr_potvrzen_at"),
                "qr_potvrdil",
            ),
            "classes": ("collapse",),
        }),
        ("Součty", {
            "fields": ("celkem_bez_dph", "celkem_dph", "celkem_s_dph")
        }),
    )

    def qr_stav(self, obj):
        if obj.zpusob_platby != PokladniDoklad.PLATBA_QR:
            return "-"
        if obj.ceka_na_qr:
            return format_html('<span style="color:#856404;font-weight:700;">Čeká</span>')
        if obj.qr_potvrzen_at:
            return format_html('<span style="color:#198754;font-weight:700;">Potvrzeno</span>')
        if obj.je_stornovany:
            return format_html('<span style="color:#dc3545;font-weight:700;">Zrušeno</span>')
        return "QR"

    qr_stav.short_description = "QR"

    def get_readonly_fields(self, request, obj=None):
        if obj and not obj.je_rozpracovany:
            return tuple(f.name for f in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return bool(obj and obj.je_rozpracovany)

    @admin.action(description="Stornovat vybrané uzavřené doklady")
    def stornovat_doklady(self, request, queryset):
        ok = 0
        for doklad in queryset:
            try:
                stornuj_doklad(doklad, user=request.user, duvod="Storno z administrace")
                ok += 1
            except ValidationError as exc:
                self.message_user(request, f"{doklad}: {exc.messages[0]}", level=messages.ERROR)
        if ok:
            self.message_user(request, f"Stornováno dokladů: {ok}.", level=messages.SUCCESS)


@admin.register(PokladniUzaverka)
class PokladniUzaverkaAdmin(admin.ModelAdmin):
    change_list_template = "admin/pokladna_uzaverka_change_list.html"
    list_display = (
        "datum",
        "pokladna",
        "pocet_dokladu",
        "hotovost",
        "karta",
        "qr",
        "konto",
        "storna",
        "rozdil_hotovosti",
        "uzavrel",
    )
    list_filter = ("pokladna", "datum")
    search_fields = ("pokladna__nazev",)
    readonly_fields = (
        "pocet_dokladu",
        "pocet_storen",
        "hotovost",
        "karta",
        "qr",
        "konto",
        "storna",
        "rozdil_hotovosti",
    )
    actions = ("prepocitat_uzaverky",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "financni-report/",
                self.admin_site.admin_view(self.financni_report_view),
                name="pokladna_financni_report_admin",
            ),
        ]
        return custom + urls

    def _report_context(self, request):
        today = timezone.localdate()
        datum_od = parse_date(request.GET.get("od") or "") or today
        datum_do = parse_date(request.GET.get("do") or "") or datum_od
        if datum_do < datum_od:
            datum_od, datum_do = datum_do, datum_od

        pokladny = Pokladna.objects.filter(aktivni=True).order_by("nazev")
        pokladna_id = request.GET.get("pokladna")
        pokladna = pokladny.filter(pk=pokladna_id).first() if pokladna_id else pokladny.first()
        doklady = doklady_za_obdobi(pokladna, datum_od, datum_do) if pokladna else PokladniDoklad.objects.none()
        hotovost = doklady.filter(
            zpusob_platby=PokladniDoklad.PLATBA_HOTOVOST
        ).aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")

        return {
            **self.admin_site.each_context(request),
            "title": "Finanční report pokladny",
            "opts": self.model._meta,
            "pokladny": pokladny,
            "pokladna": pokladna,
            "datum_od": datum_od,
            "datum_do": datum_do,
            "trzby_podle_plateb": trzby_podle_plateb(doklady),
            "trzby_podle_druhu": trzby_podle_druhu(doklady),
            "dph_souhrn": dph_souhrn(doklady),
            "plu_obraty": plu_obraty(doklady),
            "celkem": doklady.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0"),
            "pocet_dokladu": doklady.count(),
            "pokladni_hotovost": ((pokladna.hotovostni_zustatek if pokladna else Decimal("0")) or Decimal("0")) + hotovost,
        }

    def financni_report_view(self, request):
        context = self._report_context(request)
        export = request.GET.get("export")
        if export == "xls":
            return self._export_xls(context)
        if export == "pdf":
            return self._export_pdf(context)
        return TemplateResponse(request, "admin/pokladna_financni_report.html", context)

    def _fmt2(self, value):
        return decimal_cs(value or 0, places=2, trim=True)

    def _export_xls(self, context):
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="financni-report-pokladny.xls"'
        rows = [
            "<html><head><meta charset='utf-8'></head><body>",
            "<h1>Finanční report pokladny</h1>",
            f"<p>{context['datum_od']:%d.%m.%Y} - {context['datum_do']:%d.%m.%Y}</p>",
            f"<p>Pokladna: {html_cell(context['pokladna'] or '-')}</p>",
            f"<p>Tržba celkem: {money_cs(context['celkem'])}</p>",
            "<h2>Tržby podle platební metody</h2><table border='1'><tr><th>Platební metoda</th><th>Dokladů</th><th>Částka Kč</th></tr>",
        ]
        for r in context["trzby_podle_plateb"]:
            rows.append(f"<tr><td>{html_cell(r['nazev'])}</td><td>{r['pocet']}</td><td>{self._fmt2(r['castka'])}</td></tr>")
        rows.append("</table><h2>DPH</h2><table border='1'><tr><th>Sazba</th><th>Základ Kč</th><th>DPH Kč</th><th>Celkem Kč</th></tr>")
        for r in context["dph_souhrn"]:
            rows.append(f"<tr><td>{percent_cs(r['dph_sazba'])}</td><td>{self._fmt2(r['zaklad'])}</td><td>{self._fmt2(r['dph'])}</td><td>{self._fmt2(r['celkem'])}</td></tr>")
        rows.append("</table><h2>Druh tržby</h2><table border='1'><tr><th>Druh</th><th>Množství</th><th>Základ Kč</th><th>DPH Kč</th><th>Celkem Kč</th></tr>")
        for r in context["trzby_podle_druhu"]:
            rows.append(f"<tr><td>{html_cell(r['nazev'])}</td><td>{self._fmt2(r['mnozstvi'])}</td><td>{self._fmt2(r['zaklad'])}</td><td>{self._fmt2(r['dph'])}</td><td>{self._fmt2(r['celkem'])}</td></tr>")
        rows.append("</table><h2>Obrat podle PLU</h2><table border='1'><tr><th>PLU</th><th>Množství</th><th>Řádků</th><th>Obrat Kč</th></tr>")
        for r in context["plu_obraty"]:
            rows.append(f"<tr><td>{html_cell(r['nazev_snapshot'] or 'Bez názvu')}</td><td>{self._fmt2(r['mnozstvi'])}</td><td>{r['pocet_radku']}</td><td>{self._fmt2(r['obrat'])}</td></tr>")
        rows.append("</table></body></html>")
        response.write("\ufeff" + "".join(rows))
        return response

    def _export_pdf(self, context):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
        styles, font_name = czech_pdf_styles()
        story = [
            Paragraph("Finanční report pokladny", styles["Title"]),
            Paragraph(f"Pokladna: {context['pokladna'] or '-'} | Období: {context['datum_od']:%d.%m.%Y} - {context['datum_do']:%d.%m.%Y}", styles["Normal"]),
            Paragraph(f"Tržba celkem: {money_cs(context['celkem'])} | Dokladů: {context['pocet_dokladu']} | Pokladní hotovost: {money_cs(context['pokladni_hotovost'])}", styles["Normal"]),
            Spacer(1, 12),
        ]
        story.append(Paragraph("Tržby podle platební metody", styles["Heading2"]))
        data = [["Platební metoda", "Dokladů", "Částka"]]
        data += [[r["nazev"], r["pocet"], money_cs(r["castka"])] for r in context["trzby_podle_plateb"]]
        story.append(self._pdf_table(data, [240, 90, 130], font_name=font_name))
        story.append(Spacer(1, 10))

        story.append(Paragraph("DPH", styles["Heading2"]))
        data = [["Sazba", "Základ", "DPH", "Celkem"]]
        data += [[percent_cs(r["dph_sazba"]), money_cs(r["zaklad"]), money_cs(r["dph"]), money_cs(r["celkem"])] for r in context["dph_souhrn"]]
        story.append(self._pdf_table(data, [90, 130, 130, 130], font_name=font_name))
        story.append(Spacer(1, 10))

        story.append(Paragraph("Obrat podle PLU", styles["Heading2"]))
        data = [["PLU", "Množství", "Řádků", "Obrat"]]
        data += [[r["nazev_snapshot"] or "Bez názvu", self._fmt2(r["mnozstvi"]), r["pocet_radku"], money_cs(r["obrat"])] for r in context["plu_obraty"][:30]]
        story.append(self._pdf_table(data, [340, 90, 70, 110], font_name=font_name))

        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="financni-report-pokladny.pdf"'
        return response

    def _pdf_table(self, data, col_widths, font_name=None):
        return safe_table(
            data,
            col_widths,
            font_name=font_name,
            style_commands=[
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#17202a")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dbe1e8")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ],
        )

    @admin.action(description="Přepočítat vybrané uzávěrky")
    def prepocitat_uzaverky(self, request, queryset):
        for uzaverka in queryset:
            uzavri_denni_uzaverku(
                uzaverka.pokladna,
                uzaverka.datum,
                user=request.user,
                hotovost_spoctena=uzaverka.hotovost_spoctena,
                poznamka=uzaverka.poznamka,
            )
        self.message_user(request, "Vybrané uzávěrky byly přepočítány.", level=messages.SUCCESS)


@admin.register(PokladnaTile)
class PokladnaTileAdmin(admin.ModelAdmin):
    list_display = ("pokladna", "plu", "nazev", "tile_preview", "aktivni", "poradi")
    list_filter = ("pokladna", "aktivni")
    search_fields = ("nazev", "plu__nazev")
    list_editable = ("aktivni", "poradi")
    ordering = ("pokladna", "poradi", "id")

    fieldsets = (
        (None, {
            "fields": ("pokladna", "plu", "nazev", "aktivni", "poradi")
        }),
        ("Vzhled", {
            "fields": (
                ("barva_pozadi", "barva_pozadi_custom", "barva_textu"),
                ("font_bold", "font_size_px", "font_family"),
                "ikona",
            )
        }),
    )

    def tile_preview(self, obj):
        bg = obj.effective_bg_color
        fg = obj.barva_textu or "#ffffff"
        return format_html(
            '<span style="display:inline-block;padding:.25rem .6rem;border-radius:6px;'
            'background:{bg};color:{fg};font-size:0.8rem;">{text}</span>',
            bg=bg,
            fg=fg,
            text=obj.nazev or obj.plu.nazev,
        )

    tile_preview.short_description = "Náhled"
