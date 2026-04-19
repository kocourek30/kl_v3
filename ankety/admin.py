import calendar
from datetime import date
from io import BytesIO

from django.contrib import admin
from django.db.models import Avg, Count
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from kliknijidlo.pdf_utils import czech_pdf_styles, decimal_cs, html_cell, percent_cs, safe_table

from .models import AnketniOtazka, HodnoceniJidla, OdpovedHodnoceni
from .services import anketni_report_obdobi


@admin.register(AnketniOtazka)
class AnketniOtazkaAdmin(admin.ModelAdmin):
    list_display = ("text", "poradi", "aktivni", "povinna")
    list_editable = ("poradi", "aktivni", "povinna")
    search_fields = ("text", "napoveda")
    ordering = ("poradi", "id")


class OdpovedHodnoceniInline(admin.TabularInline):
    model = OdpovedHodnoceni
    extra = 0
    can_delete = False
    readonly_fields = ("otazka", "znamka")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(HodnoceniJidla)
class HodnoceniJidlaAdmin(admin.ModelAdmin):
    list_display = ("jidlo_nazev", "user", "datum_vydeje", "prumer_admin", "vytvoreno")
    list_filter = ("datum_vydeje", "vytvoreno")
    search_fields = ("jidlo_nazev", "user__username", "user__first_name", "user__last_name")
    readonly_fields = ("user", "order_item", "datum_vydeje", "jidlo_nazev", "poznamka", "vytvoreno")
    inlines = [OdpovedHodnoceniInline]
    date_hierarchy = "datum_vydeje"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("report/", self.admin_site.admin_view(self.report_view), name="ankety_report"),
        ]
        return custom + urls

    def _report_dates(self, request):
        today = date.today()
        typ = request.GET.get("typ") or "month"
        rok = int(request.GET.get("rok") or today.year)
        mesic = int(request.GET.get("mesic") or today.month)

        if typ == "day":
            den = parse_date(request.GET.get("den") or "") or today
            return typ, den, den, rok, mesic, den
        if typ == "custom":
            date_from = parse_date(request.GET.get("od") or "") or date(rok, mesic, 1)
            date_to = parse_date(request.GET.get("do") or "") or today
            if date_to < date_from:
                date_from, date_to = date_to, date_from
            return typ, date_from, date_to, rok, mesic, today

        last_day = calendar.monthrange(rok, mesic)[1]
        return "month", date(rok, mesic, 1), date(rok, mesic, last_day), rok, mesic, today

    def report_context(self, request):
        typ, date_from, date_to, rok, mesic, den = self._report_dates(request)
        min_hodnoceni = max(1, int(request.GET.get("min_hodnoceni") or 1))
        data = anketni_report_obdobi(date_from, date_to, min_hodnoceni=min_hodnoceni)
        query = request.GET.copy()
        query.pop("export", None)
        return {
            **self.admin_site.each_context(request),
            "title": "Vyhodnocení anket",
            "opts": self.model._meta,
            "typ": typ,
            "rok": rok,
            "mesic": mesic,
            "den": den,
            "date_from": date_from,
            "date_to": date_to,
            "min_hodnoceni": min_hodnoceni,
            "data": data,
            "query_string": query.urlencode(),
            "mesice": [(i, f"{i:02d}") for i in range(1, 13)],
            "roky": range(today.year - 3, today.year + 2) if (today := date.today()) else [],
        }

    def report_view(self, request):
        context = self.report_context(request)
        export = request.GET.get("export")
        if export == "xls":
            return self.export_report_xls(context)
        if export == "pdf":
            return self.export_report_pdf(context)
        return TemplateResponse(request, "admin/ankety/report.html", context)

    def _fmt(self, value):
        if value is None:
            return "-"
        return decimal_cs(value, places=2, trim=True)

    def export_report_xls(self, context):
        data = context["data"]
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="vyhodnoceni-anket.xls"'
        rows = [
            "<html><head><meta charset='utf-8'></head><body>",
            "<h1>Vyhodnocení anket</h1>",
            f"<p>Období: {context['date_from']:%d.%m.%Y} - {context['date_to']:%d.%m.%Y}</p>",
            "<h2>Souhrn</h2>",
            "<table border='1'>",
            f"<tr><td>Hodnocení</td><td>{data['hodnoceni_count']}</td></tr>",
            f"<tr><td>Průměr</td><td>{self._fmt(data['prumer'])}</td></tr>",
            f"<tr><td>Návratnost</td><td>{percent_cs(data['navratnost_pct'])}</td></tr>",
            f"<tr><td>Objednáno porcí</td><td>{data['objednane_count']}</td></tr>",
            "</table>",
            "<h2>Nejlepší jídla</h2>",
            "<table border='1'><tr><th>Jídlo</th><th>Průměr</th><th>Hodnocení</th><th>Objednáno</th><th>Návratnost</th></tr>",
        ]
        for row in data["nejlepsi"]:
            rows.append(
                f"<tr><td>{html_cell(row['jidlo'])}</td><td>{self._fmt(row['prumer'])}</td><td>{row['hodnoceni']}</td>"
                f"<td>{row['objednano']}</td><td>{percent_cs(row['navratnost'])}</td></tr>"
            )
        rows.append("</table><h2>Nejčastěji objednávaná jídla</h2><table border='1'><tr><th>Jídlo</th><th>Objednáno</th><th>Vydáno</th><th>Hodnocení</th><th>Průměr</th></tr>")
        for row in data["nejobjednavanejsi"]:
            rows.append(
                f"<tr><td>{html_cell(row['jidlo'])}</td><td>{row['objednano']}</td><td>{row['vydano']}</td>"
                f"<td>{row['hodnoceni']}</td><td>{self._fmt(row['prumer'])}</td></tr>"
            )
        rows.append("</table><h2>Otázky</h2><table border='1'><tr><th>Otázka</th><th>Počet</th><th>Průměr</th></tr>")
        for row in data["otazky"]:
            rows.append(f"<tr><td>{html_cell(row['otazka'])}</td><td>{row['pocet']}</td><td>{self._fmt(row['prumer'])}</td></tr>")
        rows.append("</table></body></html>")
        response.write("\ufeff" + "".join(rows))
        return response

    def export_report_pdf(self, context):
        data = context["data"]
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=22, leftMargin=22, topMargin=22, bottomMargin=22)
        styles, font_name = czech_pdf_styles()
        story = [
            Paragraph("Vyhodnocení anket", styles["Title"]),
            Paragraph(f"Období: {context['date_from']:%d.%m.%Y} - {context['date_to']:%d.%m.%Y}", styles["Normal"]),
            Spacer(1, 10),
        ]
        summary = [
            ["Ukazatel", "Hodnota"],
            ["Hodnocení", data["hodnoceni_count"]],
            ["Průměr", self._fmt(data["prumer"])],
            ["Návratnost", percent_cs(data["navratnost_pct"])],
            ["Objednáno porcí", data["objednane_count"]],
        ]
        story.append(safe_table(summary, [180, 120], font_name=font_name, style_commands=[
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Nejlepší jídla", styles["Heading2"]))
        best = [["Jídlo", "Průměr", "Hodnocení", "Objednáno", "Návratnost"]]
        best += [[r["jidlo"], self._fmt(r["prumer"]), r["hodnoceni"], r["objednano"], percent_cs(r["navratnost"])] for r in data["nejlepsi"]]
        if len(best) == 1:
            best.append(["Bez dat", "-", "-", "-", "-"])
        story.append(safe_table(best, [320, 70, 75, 75, 90], font_name=font_name, style_commands=[
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(Spacer(1, 12))
        story.append(Paragraph("Nejčastěji objednávaná jídla", styles["Heading2"]))
        ordered = [["Jídlo", "Objednáno", "Vydáno", "Hodnocení", "Průměr"]]
        ordered += [[r["jidlo"], r["objednano"], r["vydano"], r["hodnoceni"], self._fmt(r["prumer"])] for r in data["nejobjednavanejsi"]]
        if len(ordered) == 1:
            ordered.append(["Bez dat", "-", "-", "-", "-"])
        story.append(safe_table(ordered, [340, 75, 75, 75, 70], font_name=font_name, style_commands=[
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f28f28")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="vyhodnoceni-anket.pdf"'
        return response

    def prumer_admin(self, obj):
        prumer = obj.odpovedi.aggregate(avg=Avg("znamka"))["avg"]
        return self._fmt(prumer) if prumer is not None else "-"

    prumer_admin.short_description = "Průměr"


@admin.register(OdpovedHodnoceni)
class OdpovedHodnoceniAdmin(admin.ModelAdmin):
    list_display = ("otazka", "jidlo", "stravnik", "znamka", "datum_vydeje")
    list_filter = ("otazka", "znamka", "hodnoceni_jidla__datum_vydeje")
    search_fields = ("hodnoceni_jidla__jidlo_nazev", "hodnoceni_jidla__user__username")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("otazka", "hodnoceni_jidla", "hodnoceni_jidla__user")

    def jidlo(self, obj):
        return obj.hodnoceni_jidla.jidlo_nazev

    def stravnik(self, obj):
        return obj.hodnoceni_jidla.user

    def datum_vydeje(self, obj):
        return obj.hodnoceni_jidla.datum_vydeje
