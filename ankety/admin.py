from datetime import date, timedelta
from io import BytesIO

from django.contrib import admin
from django.db.models import Avg, Count
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_date
from django.utils.html import format_html

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from kliknijidlo.pdf_utils import czech_pdf_styles, decimal_cs, html_cell, percent_cs, safe_table

from .models import (
    AnketniOtazka,
    HodnoceniJidla,
    MesicniAnketa,
    MesicniAnketaHlas,
    MesicniAnketaVarianta,
    OdpovedHodnoceni,
)
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
    readonly_fields = ("otazka", "znamka_hvezdy")
    fields = ("otazka", "znamka_hvezdy")

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Hodnocení")
    def znamka_hvezdy(self, obj):
        return hvezdy_html(obj.znamka)


class MesicniAnketaVariantaInline(admin.TabularInline):
    model = MesicniAnketaVarianta
    extra = 1
    fields = ("poradi", "nazev", "popis")
    ordering = ("poradi", "id")


class MesicniAnketaHlasInline(admin.TabularInline):
    model = MesicniAnketaHlas
    extra = 0
    can_delete = False
    readonly_fields = ("user", "varianta", "hlasovano")
    fields = ("user", "varianta", "hlasovano")
    ordering = ("-hlasovano",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(HodnoceniJidla)
class HodnoceniJidlaAdmin(admin.ModelAdmin):
    list_display = ("jidlo_nazev", "user", "datum_vydeje", "prumer_hvezdy", "vytvoreno")
    list_filter = ("datum_vydeje", "vytvoreno")
    search_fields = ("jidlo_nazev", "user__username", "user__first_name", "user__last_name")
    readonly_fields = ("user", "order_item", "datum_vydeje", "jidlo_nazev", "poznamka", "vytvoreno")
    inlines = [OdpovedHodnoceniInline]
    date_hierarchy = "datum_vydeje"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("report/", self.admin_site.admin_view(self.report_view), name="ankety_report"),
            path("report/detail/", self.admin_site.admin_view(self.report_detail_view), name="ankety_report_detail"),
        ]
        return custom + urls

    def _report_dates(self, request):
        today = date.today()
        yesterday = today - timedelta(days=1)
        typ = request.GET.get("typ") or "yesterday"

        if typ == "current_month":
            return typ, today.replace(day=1), today
        if typ == "previous_month":
            last_day_previous_month = today.replace(day=1) - timedelta(days=1)
            return typ, last_day_previous_month.replace(day=1), last_day_previous_month
        if typ == "current_year":
            return typ, date(today.year, 1, 1), today
        if typ == "previous_year":
            return typ, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
        if typ == "custom":
            date_from = parse_date(request.GET.get("od") or "") or yesterday
            date_to = parse_date(request.GET.get("do") or "") or yesterday
            if date_to < date_from:
                date_from, date_to = date_to, date_from
            return typ, date_from, date_to

        return "yesterday", yesterday, yesterday

    def _period_choices(self):
        return [
            ("yesterday", "Včerejšek"),
            ("current_month", "Aktuální měsíc"),
            ("previous_month", "Minulý měsíc"),
            ("current_year", "Aktuální rok"),
            ("previous_year", "Minulý rok"),
            ("custom", "Vlastní období"),
        ]

    def _period_label(self, typ):
        labels = dict(self._period_choices())
        return labels.get(typ, "Včerejšek")

    def report_context(self, request):
        typ, date_from, date_to = self._report_dates(request)
        data = anketni_report_obdobi(date_from, date_to)
        monthly_vote = self._monthly_vote_summary(date_from, date_to)
        query = request.GET.copy()
        query.pop("export", None)
        return {
            **self.admin_site.each_context(request),
            "title": "Vyhodnocení anket",
            "opts": self.model._meta,
            "typ": typ,
            "date_from": date_from,
            "date_to": date_to,
            "period_label": self._period_label(typ),
            "period_choices": self._period_choices(),
            "data": data,
            "monthly_vote": monthly_vote,
            "query_string": query.urlencode(),
        }

    def _monthly_vote_summary(self, date_from, date_to):
        ankety_qs = (
            MesicniAnketa.objects
            .filter(hlasovani_od__lte=date_to, hlasovani_do__gte=date_from)
            .prefetch_related("varianty", "hlasy")
            .order_by("-rok", "-mesic", "-vytvoreno")
        )
        latest = ankety_qs.first()
        if not latest:
            return {
                "exists": False,
                "title": "Měsíční volba menu",
                "subtitle": "Ve vybraném období zatím není žádná měsíční anketa.",
                "total_votes": 0,
                "active_count": 0,
                "variants": [],
            }

        variants = []
        votes = (
            MesicniAnketaHlas.objects.filter(anketa=latest)
            .values("varianta_id")
            .annotate(total=Count("id"))
        )
        vote_map = {row["varianta_id"]: row["total"] for row in votes}
        total_votes = sum(vote_map.values())
        for var in latest.varianty.all().order_by("poradi", "id"):
            count = vote_map.get(var.id, 0)
            pct = round((count * 100 / total_votes), 1) if total_votes else 0
            variants.append({
                "name": var.nazev,
                "count": count,
                "pct": pct,
            })

        return {
            "exists": True,
            "title": latest.nazev,
            "subtitle": f"{latest.get_mesic_display()} {latest.rok}",
            "total_votes": total_votes,
            "active_count": ankety_qs.filter(aktivni=True).count(),
            "variants": variants,
            "is_open": latest.is_open(),
        }

    def report_view(self, request):
        context = self.report_context(request)
        export = request.GET.get("export")
        if export == "xls":
            return self.export_report_xls(context)
        if export == "pdf":
            return self.export_report_pdf(context)
        return TemplateResponse(request, "admin/ankety/report.html", context)

    def report_detail_view(self, request):
        context = self.report_context(request)
        section = request.GET.get("section") or "nejlepsi"
        meal_name = (request.GET.get("meal") or "").strip()
        meal_type = (request.GET.get("meal_type") or "").strip()

        detail_context = {
            **context,
            "section": section,
            "section_label": self._section_label(section),
            "meal_name": meal_name,
            "meal_type": meal_type,
            "detail_rows": self._section_rows(context["data"], section, meal_type=meal_type),
            "meal_detail": self._meal_detail(context, meal_name) if meal_name else None,
        }
        export = request.GET.get("export")
        if export == "xls":
            return self.export_detail_xls(detail_context)
        if export == "pdf":
            return self.export_detail_pdf(detail_context)
        return TemplateResponse(request, "admin/ankety/report_detail.html", detail_context)

    def _fmt(self, value):
        if value is None:
            return "-"
        return decimal_cs(value, places=2, trim=True)

    def _stars_text(self, value):
        if value is None:
            return "-"
        rounded = int(round(float(value)))
        return "★" * rounded + "☆" * (5 - rounded)

    def _section_label(self, section):
        labels = {
            "nejlepsi": "Nejlépe hodnocená jídla",
            "nejslabsi": "Jídla k pozornosti",
            "nejobjednavanejsi": "Nejčastěji objednávaná jídla",
            "otazky": "Otázky v anketě",
            "trendy": "Vývoj podle dní",
            "poznamky": "Poznámky strávníků",
        }
        return labels.get(section, "Detail ankety")

    def _section_rows(self, data, section, *, meal_type=""):
        if section == "nejlepsi":
            return sorted(data["hodnocena_jidla"], key=lambda row: (row["prumer"], row["hodnoceni"]), reverse=True)
        if section == "nejslabsi":
            return sorted(data["hodnocena_jidla"], key=lambda row: (row["prumer"], -row["hodnoceni"]))
        if section == "nejobjednavanejsi":
            return sorted(data["jidla"], key=lambda row: (row["objednano"], row["hodnoceni"]), reverse=True)
        if section == "otazky":
            return data["otazky"]
        if section == "trendy":
            return data["trendy"]
        if section == "poznamky":
            return data["poznamky_all"]
        return []

    def _detail_export_filename(self, context, suffix):
        base = context["section"]
        if context.get("meal_name"):
            base += "-jidlo"
        elif context.get("meal_type"):
            base += "-druh"
        return f"ankety-{base}.{suffix}"

    def export_detail_xls(self, context):
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{self._detail_export_filename(context, "xls")}"'
        rows = [
            "<html><head><meta charset='utf-8'></head><body>",
            f"<h1>{html_cell(context['section_label'])}</h1>",
            f"<p>Období: {context['date_from']:%d.%m.%Y} - {context['date_to']:%d.%m.%Y}</p>",
        ]
        if context.get("meal_detail"):
            meal = context["meal_detail"]
            rows.append(
                f"<h2>{html_cell(meal['jidlo'])}</h2><table border='1'>"
                f"<tr><th>Strávník</th><th>Datum</th><th>Průměr</th><th>Poznámka</th></tr>"
            )
            for response_row in meal["responses"]:
                rows.append(
                    f"<tr><td>{html_cell(response_row['user'])}</td><td>{response_row['datum']:%d.%m.%Y}</td>"
                    f"<td>{response_row['prumer']}</td><td>{html_cell(response_row['poznamka'] or '-')}</td></tr>"
                )
            rows.append("</table>")
        else:
            rows.append("<table border='1'>")
            if context["section"] in {"nejlepsi", "nejslabsi", "nejobjednavanejsi"}:
                rows.append("<tr><th>Jídlo</th><th>Druh jídla</th><th>Objednáno</th><th>Vydáno</th><th>Hodnocení</th><th>Průměr</th></tr>")
                for row in context["detail_rows"]:
                    if isinstance(row, dict) and "jidlo" in row:
                        rows.append(
                            f"<tr><td>{html_cell(row['jidlo'])}</td><td>{html_cell(row.get('druh_jidla') or '-')}</td>"
                            f"<td>{row.get('objednano', '-')}</td><td>{row.get('vydano', '-')}</td>"
                            f"<td>{row.get('hodnoceni', '-')}</td><td>{self._fmt(row.get('prumer'))}</td></tr>"
                        )
            elif context["section"] == "otazky":
                rows.append("<tr><th>Otázka</th><th>Počet</th><th>Průměr</th></tr>")
                for row in context["detail_rows"]:
                    rows.append(f"<tr><td>{html_cell(row['otazka'])}</td><td>{row['pocet']}</td><td>{self._fmt(row['prumer'])}</td></tr>")
            elif context["section"] == "trendy":
                rows.append("<tr><th>Datum</th><th>Hodnocení</th><th>Průměr</th></tr>")
                for row in context["detail_rows"]:
                    rows.append(f"<tr><td>{row['datum']:%d.%m.%Y}</td><td>{row['hodnoceni']}</td><td>{self._fmt(row['prumer'])}</td></tr>")
            elif context["section"] == "poznamky":
                rows.append("<tr><th>Jídlo</th><th>Strávník</th><th>Datum</th><th>Poznámka</th></tr>")
                for row in context["detail_rows"]:
                    rows.append(
                        f"<tr><td>{html_cell(row['jidlo'])}</td><td>{html_cell(row['stravnik'])}</td>"
                        f"<td>{row['datum']:%d.%m.%Y}</td><td>{html_cell(row['poznamka'])}</td></tr>"
                    )
            rows.append("</table>")
        rows.append("</body></html>")
        response.write("\ufeff" + "".join(rows))
        return response

    def export_detail_pdf(self, context):
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=22, leftMargin=22, topMargin=22, bottomMargin=22)
        styles, font_name = czech_pdf_styles()
        story = [
            Paragraph(context["section_label"], styles["Title"]),
            Paragraph(f"Období: {context['date_from']:%d.%m.%Y} - {context['date_to']:%d.%m.%Y}", styles["Normal"]),
            Spacer(1, 10),
        ]
        if context.get("meal_detail"):
            meal = context["meal_detail"]
            story.append(Paragraph(html_cell(meal["jidlo"]), styles["Heading2"]))
            rows = [["Strávník", "Datum", "Průměr", "Poznámka"]]
            rows += [
                [response_row["user"], response_row["datum"].strftime("%d.%m.%Y"), response_row["prumer"], response_row["poznamka"] or "-"]
                for response_row in meal["responses"]
            ]
            story.append(safe_table(rows, [180, 85, 70, 360], font_name=font_name, style_commands=[
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
        else:
            if context["section"] in {"nejlepsi", "nejslabsi", "nejobjednavanejsi"}:
                rows = [["Jídlo", "Druh jídla", "Objednáno", "Vydáno", "Hodnocení", "Průměr"]]
                rows += [
                    [row["jidlo"], row.get("druh_jidla") or "-", row.get("objednano", "-"), row.get("vydano", "-"), row.get("hodnoceni", "-"), self._fmt(row.get("prumer"))]
                    for row in context["detail_rows"]
                    if isinstance(row, dict) and "jidlo" in row
                ]
                if len(rows) == 1:
                    rows.append(["Bez dat", "-", "-", "-", "-", "-"])
                story.append(safe_table(rows, [250, 90, 65, 65, 70, 70], font_name=font_name, style_commands=[
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
            elif context["section"] == "otazky":
                rows = [["Otázka", "Počet", "Průměr"]]
                rows += [[row["otazka"], row["pocet"], self._fmt(row["prumer"])] for row in context["detail_rows"]]
                story.append(safe_table(rows, [430, 90, 90], font_name=font_name))
            elif context["section"] == "trendy":
                rows = [["Datum", "Hodnocení", "Průměr"]]
                rows += [[row["datum"].strftime("%d.%m.%Y"), row["hodnoceni"], self._fmt(row["prumer"])] for row in context["detail_rows"]]
                story.append(safe_table(rows, [180, 100, 100], font_name=font_name))
            elif context["section"] == "poznamky":
                rows = [["Jídlo", "Strávník", "Datum", "Poznámka"]]
                rows += [[row["jidlo"], row["stravnik"], row["datum"].strftime("%d.%m.%Y"), row["poznamka"]] for row in context["detail_rows"]]
                story.append(safe_table(rows, [220, 150, 85, 295], font_name=font_name))
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{self._detail_export_filename(context, "pdf")}"'
        return response

    def _meal_detail(self, context, meal_name):
        hodnoceni_qs = (
            HodnoceniJidla.objects
            .filter(
                datum_vydeje__gte=context["date_from"],
                datum_vydeje__lte=context["date_to"],
                jidlo_nazev=meal_name,
            )
            .select_related("user", "order_item__menu_item__druh_jidla")
            .prefetch_related("odpovedi__otazka")
            .order_by("-datum_vydeje", "-vytvoreno")
        )
        if not hodnoceni_qs.exists():
            return None

        responses = []
        total_scores = 0
        total_answers = 0
        notes_count = 0
        for hodnoceni in hodnoceni_qs:
            odpovedi = list(hodnoceni.odpovedi.all())
            avg_value = sum(o.znamka for o in odpovedi) / len(odpovedi) if odpovedi else None
            total_scores += sum(o.znamka for o in odpovedi)
            total_answers += len(odpovedi)
            if hodnoceni.poznamka:
                notes_count += 1
            responses.append({
                "user": hodnoceni.user.get_full_name() or hodnoceni.user.username,
                "datum": hodnoceni.datum_vydeje,
                "vytvoreno": hodnoceni.vytvoreno,
                "druh_jidla": getattr(getattr(hodnoceni.order_item, "menu_item", None), "druh_jidla", None),
                "prumer": self._fmt(avg_value) if avg_value is not None else "-",
                "prumer_numeric": avg_value or 0,
                "poznamka": hodnoceni.poznamka,
                "odpovedi": [
                    {
                        "otazka": odpoved.otazka.text,
                        "znamka": odpoved.znamka,
                    }
                    for odpoved in odpovedi
                ],
            })

        return {
            "jidlo": meal_name,
            "count": hodnoceni_qs.count(),
            "answers": total_answers,
            "average": self._fmt(total_scores / total_answers) if total_answers else "-",
            "average_numeric": (total_scores / total_answers) if total_answers else 0,
            "notes_count": notes_count,
            "responses": responses,
        }

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

    @admin.display(description="Hodnocení")
    def prumer_hvezdy(self, obj):
        prumer = obj.odpovedi.aggregate(avg=Avg("znamka"))["avg"]
        if prumer is None:
            return "-"
        return format_html(
            '<span style="color:#f5a623;font-size:14px;font-weight:700;">{}</span> <span style="color:#5f6d63;">({})</span>',
            self._stars_text(prumer),
            self._fmt(prumer),
        )


@admin.register(OdpovedHodnoceni)
class OdpovedHodnoceniAdmin(admin.ModelAdmin):
    list_display = ("otazka", "jidlo", "stravnik", "znamka_hvezdy", "datum_vydeje")
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

    @admin.display(description="Hodnocení")
    def znamka_hvezdy(self, obj):
        return hvezdy_html(obj.znamka)


@admin.register(MesicniAnketa)
class MesicniAnketaAdmin(admin.ModelAdmin):
    list_display = (
        "nazev",
        "obdobi",
        "navrhujici_trida",
        "hlasovani_od",
        "hlasovani_do",
        "aktivni",
        "hlasu_celkem",
    )
    list_filter = ("aktivni", "rok", "mesic")
    search_fields = ("nazev", "navrhujici_trida")
    inlines = [MesicniAnketaVariantaInline, MesicniAnketaHlasInline]
    ordering = ("-rok", "-mesic", "-vytvoreno")

    @admin.display(description="Období")
    def obdobi(self, obj):
        return f"{obj.get_mesic_display()} {obj.rok}"

    @admin.display(description="Hlasy")
    def hlasu_celkem(self, obj):
        return obj.hlasy.count()


@admin.register(MesicniAnketaVarianta)
class MesicniAnketaVariantaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "anketa", "poradi", "hlasu_celkem")
    list_filter = ("anketa__rok", "anketa__mesic")
    search_fields = ("nazev", "anketa__nazev")
    ordering = ("anketa__rok", "anketa__mesic", "poradi", "id")

    @admin.display(description="Hlasy")
    def hlasu_celkem(self, obj):
        return obj.hlasy.count()


@admin.register(MesicniAnketaHlas)
class MesicniAnketaHlasAdmin(admin.ModelAdmin):
    list_display = ("anketa", "varianta", "user", "hlasovano")
    list_filter = ("anketa__rok", "anketa__mesic", "anketa")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "varianta__nazev",
        "anketa__nazev",
    )
    ordering = ("-hlasovano",)


def hvezdy_html(value):
    if value is None:
        return "-"
    full = "★" * int(value)
    empty = "☆" * (5 - int(value))
    return format_html(
        '<span style="color:#f5a623;font-size:15px;letter-spacing:1px;">{}{}</span> <span style="color:#5f6d63;">({}/5)</span>',
        full,
        empty,
        value,
    )
