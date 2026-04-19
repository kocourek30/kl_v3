from datetime import date
from io import BytesIO

from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from kliknijidlo.pdf_utils import csv_row, czech_pdf_styles, decimal_cs, html_cell, money_cs, safe_table

from .models import FakturacniDavka, FakturacniNastaveni, FakturacniPolozka
from .services import vytvor_nebo_prepocitej_davku


def _fmt(value):
    return decimal_cs(value or 0, places=2, trim=True)


@admin.register(FakturacniNastaveni)
class FakturacniNastaveniAdmin(admin.ModelAdmin):
    filter_horizontal = ("zamestnanecke_skupiny",)
    fields = ("nazev", "zamestnanecke_skupiny", "zahrnout_nevyzvednute", "fakturovat_dotace")


class FakturacniPolozkaInline(admin.TabularInline):
    model = FakturacniPolozka
    extra = 0
    can_delete = False
    readonly_fields = (
        "typ",
        "jmeno_snapshot",
        "username_snapshot",
        "osobni_cislo_snapshot",
        "skupina_snapshot",
        "pocet_porci",
        "castka",
        "detail",
    )
    fields = readonly_fields
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FakturacniDavka)
class FakturacniDavkaAdmin(admin.ModelAdmin):
    change_list_template = "admin/fakturace/davka_change_list.html"
    list_display = ("obdobi", "stav", "dotace_celkem", "srazky_celkem", "celkem", "polozek", "exporty", "vytvoreno")
    list_filter = ("rok", "mesic", "stav")
    readonly_fields = ("dotace_celkem", "srazky_celkem", "polozek", "vytvoreno", "vytvoril")
    inlines = [FakturacniPolozkaInline]

    @admin.display(description="Období")
    def obdobi(self, obj):
        return f"{obj.mesic:02d}/{obj.rok}"

    @admin.display(description="Export")
    def exporty(self, obj):
        csv_url = reverse("admin:fakturace_export_davky", args=[obj.id, "csv"])
        xls_url = reverse("admin:fakturace_export_davky", args=[obj.id, "xls"])
        pdf_url = reverse("admin:fakturace_export_davky", args=[obj.id, "pdf"])
        return format_html(
            '<a class="button" href="{}">CSV</a> '
            '<a class="button" href="{}">XLS</a> '
            '<a class="button" href="{}">PDF</a>',
            csv_url,
            xls_url,
            pdf_url,
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("vytvorit/", self.admin_site.admin_view(self.vytvorit_view), name="fakturace_vytvorit_davku"),
            path("<int:davka_id>/export/<str:format>/", self.admin_site.admin_view(self.export_view), name="fakturace_export_davky"),
        ]
        return custom + urls

    def changelist_view(self, request, extra_context=None):
        today = date.today()
        context = extra_context or {}
        context.update({
            "default_rok": today.year,
            "default_mesic": today.month - 1 or 12,
            "vytvorit_url": reverse("admin:fakturace_vytvorit_davku"),
        })
        if today.month == 1:
            context["default_rok"] = today.year - 1
        return super().changelist_view(request, context)

    def vytvorit_view(self, request):
        rok = int(request.POST.get("rok") or request.GET.get("rok") or date.today().year)
        mesic = int(request.POST.get("mesic") or request.GET.get("mesic") or date.today().month)
        try:
            davka = vytvor_nebo_prepocitej_davku(rok, mesic, user=request.user)
            messages.success(request, f"Fakturační dávka {mesic:02d}/{rok} byla vytvořena nebo přepočítána.")
            return redirect("admin:fakturace_fakturacnidavka_change", davka.id)
        except Exception as exc:
            messages.error(request, str(exc))
            return redirect("admin:fakturace_fakturacnidavka_changelist")

    def export_view(self, request, davka_id, format):
        davka = self.get_queryset(request).get(pk=davka_id)
        if format == "csv":
            return self._export_csv(davka)
        if format == "xls":
            return self._export_xls(davka)
        if format == "pdf":
            return self._export_pdf(davka)
        messages.error(request, "Neznámý formát exportu.")
        return redirect("admin:fakturace_fakturacnidavka_change", davka.id)

    def _rows(self, davka):
        return davka.polozky.all().order_by("typ", "skupina_snapshot", "jmeno_snapshot")

    def _export_csv(self, davka):
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="fakturace-{davka.rok}-{davka.mesic:02d}.csv"'
        response.write("\ufeff")
        response.write(csv_row(["Typ", "Jméno", "Login", "Osobní číslo", "Skupina", "Počet porcí", "Částka Kč", "Detail"]))
        for p in self._rows(davka):
            response.write(csv_row([
                p.get_typ_display(),
                p.jmeno_snapshot,
                p.username_snapshot,
                p.osobni_cislo_snapshot,
                p.skupina_snapshot,
                _fmt(p.pocet_porci),
                _fmt(p.castka),
                p.detail,
            ]))
        return response

    def _export_xls(self, davka):
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="fakturace-{davka.rok}-{davka.mesic:02d}.xls"'
        rows = [
            "<meta charset='utf-8'>",
            f"<h1>Fakturace {davka.mesic:02d}/{davka.rok}</h1>",
            f"<p>Dotace: {money_cs(davka.dotace_celkem)} | Srážky ze mzdy: {money_cs(davka.srazky_celkem)}</p>",
            "<table border='1'><tr><th>Typ</th><th>Jméno</th><th>Login</th><th>Osobní číslo</th><th>Skupina</th><th>Počet porcí</th><th>Částka Kč</th><th>Detail</th></tr>",
        ]
        for p in self._rows(davka):
            rows.append(
                f"<tr><td>{html_cell(p.get_typ_display())}</td><td>{html_cell(p.jmeno_snapshot)}</td><td>{html_cell(p.username_snapshot)}</td>"
                f"<td>{html_cell(p.osobni_cislo_snapshot)}</td><td>{html_cell(p.skupina_snapshot)}</td><td>{_fmt(p.pocet_porci)}</td>"
                f"<td>{_fmt(p.castka)}</td><td>{html_cell(p.detail)}</td></tr>"
            )
        rows.append("</table>")
        response.write("".join(rows))
        return response

    def _export_pdf(self, davka):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        from reportlab.platypus import TableStyle

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
        styles, font_name = czech_pdf_styles()
        data = [["Typ", "Jméno", "Osobní číslo", "Skupina", "Porcí", "Částka"]]
        for p in self._rows(davka):
            data.append([p.get_typ_display(), p.jmeno_snapshot, p.osobni_cislo_snapshot, p.skupina_snapshot, _fmt(p.pocet_porci), money_cs(p.castka)])
        table = safe_table(
            data,
            [85, 155, 85, 145, 65, 90],
            font_name=font_name,
            style_commands=[
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf7")]),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ],
        )
        story = [
            Paragraph(f"Fakturace {davka.mesic:02d}/{davka.rok}", styles["Title"]),
            Paragraph(f"Dotace: {money_cs(davka.dotace_celkem)} | Srážky ze mzdy: {money_cs(davka.srazky_celkem)}", styles["Normal"]),
            Spacer(1, 12),
            table,
        ]
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="fakturace-{davka.rok}-{davka.mesic:02d}.pdf"'
        return response


@admin.register(FakturacniPolozka)
class FakturacniPolozkaAdmin(admin.ModelAdmin):
    list_display = ("davka", "typ", "jmeno_snapshot", "osobni_cislo_snapshot", "skupina_snapshot", "pocet_porci", "castka")
    list_filter = ("typ", "davka__rok", "davka__mesic")
    search_fields = ("jmeno_snapshot", "username_snapshot", "osobni_cislo_snapshot", "skupina_snapshot")
    readonly_fields = [field.name for field in FakturacniPolozka._meta.fields]

    def has_add_permission(self, request):
        return False
