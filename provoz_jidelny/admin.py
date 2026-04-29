from django.contrib import admin, messages
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from django.db.models import Sum

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from vydej.models import VydejSettings as RealVydejSettings
from objednavky.models import OrderCancellationLog, OrderItem, Order

from .models import NastaveniVydaje, ProvozniDashboard
from .services import build_canteen_staff_dashboard


@admin.register(ProvozniDashboard)
class ProvozniDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/provoz_jidelny/provoznidashboard/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "snapshot/",
                self.admin_site.admin_view(self.snapshot_view),
                name="provoz_jidelny_provoznidashboard_snapshot",
            ),
            path(
                "odhlasky-pdf/",
                self.admin_site.admin_view(self.cancellations_pdf_view),
                name="provoz_jidelny_provoznidashboard_cancellations_pdf",
            ),
            path(
                "odhlasky-kuchyn-pdf/",
                self.admin_site.admin_view(self.cancellations_kitchen_pdf_view),
                name="provoz_jidelny_provoznidashboard_cancellations_kitchen_pdf",
            ),
        ]
        return custom_urls + urls

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        dashboard = build_canteen_staff_dashboard()
        selected_date = request.GET.get("report_date") or timezone.localdate().isoformat()
        context = {
            **self.admin_site.each_context(request),
            "title": "Provoz jídelny",
            "subtitle": "Provozní dashboard pro obsluhu jídelny",
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
            "cl": None,
            "media": self.media,
            "dashboard": dashboard,
            "report_date": selected_date,
            "report_pdf_url": reverse("admin:provoz_jidelny_provoznidashboard_cancellations_pdf"),
            "report_kitchen_pdf_url": reverse("admin:provoz_jidelny_provoznidashboard_cancellations_kitchen_pdf"),
        }
        if extra_context:
            context.update(extra_context)
        return render(request, self.change_list_template, context)

    def snapshot_view(self, request):
        dashboard = build_canteen_staff_dashboard()
        context = {
            **self.admin_site.each_context(request),
            "dashboard": dashboard,
        }
        return JsonResponse(
            {
                "hero_html": render_to_string(
                    "admin/provoz_jidelny/provoznidashboard/_hero_stats.html",
                    context,
                    request=request,
                ),
                "sections_html": render_to_string(
                    "admin/provoz_jidelny/provoznidashboard/_live_sections.html",
                    context,
                    request=request,
                ),
            }
        )

    def _build_cancellation_rows(self, target_date):
        logs = (
            OrderCancellationLog.objects.filter(datum_vydeje=target_date)
            .select_related("user")
            .order_by("cancelled_at")
        )

        rows = []
        for log in logs:
            full_name = (log.user.get_full_name() or log.user.username or "").strip()
            cancelled_at_local = timezone.localtime(log.cancelled_at)
            reason = (log.reason or "").strip() or "Bez uvedeného důvodu"
            tone = "Pozdní odhláška" if log.cancelled_late else "Včasná odhláška"

            # Zkusíme dohledat konkrétní položky jídla v okolí času storna
            time_from = cancelled_at_local - timedelta(minutes=15)
            time_to = cancelled_at_local + timedelta(minutes=15)
            candidate_orders = Order.objects.filter(
                user=log.user,
                datum_vydeje=target_date,
                status__in=["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"],
                storno_datum__gte=time_from,
                storno_datum__lte=time_to,
            )
            if not candidate_orders.exists():
                candidate_orders = Order.objects.filter(
                    user=log.user,
                    datum_vydeje=target_date,
                    status__in=["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"],
                )

            item_names = list(
                OrderItem.objects.filter(order__in=candidate_orders)
                .select_related("menu_item__jidlo")
                .values_list("menu_item__jidlo__nazev", flat=True)
                .distinct()[:8]
            )
            meals = ", ".join(item_names) if item_names else "Položky nejsou dostupné"

            rows.append(
                {
                    "user": full_name,
                    "time": cancelled_at_local.strftime("%H:%M:%S"),
                    "meals": meals,
                    "items_count": log.items_count,
                    "total_price": log.total_price or Decimal("0"),
                    "reason": reason,
                    "tone": tone,
                }
            )
        return rows

    def cancellations_pdf_view(self, request):
        raw_date = request.GET.get("report_date") or timezone.localdate().isoformat()
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()

        rows = self._build_cancellation_rows(target_date)

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="odhlasky_{target_date.strftime("%Y_%m_%d")}.pdf"'
        )

        doc = SimpleDocTemplate(
            response,
            pagesize=A4,
            rightMargin=12 * mm,
            leftMargin=12 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )

        # Font s českou diakritikou
        base_dir = Path(__file__).resolve().parent.parent
        regular_font = base_dir / "static" / "fonts" / "DejaVuSans.ttf"
        bold_font = base_dir / "static" / "fonts" / "DejaVuSans-Bold.ttf"
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular_font)))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold_font)))

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TitleCZ",
            parent=styles["Heading1"],
            fontName="DejaVuSans-Bold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#1f3b2d"),
            spaceAfter=8,
        )
        meta_style = ParagraphStyle(
            "MetaCZ",
            parent=styles["Normal"],
            fontName="DejaVuSans",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4f5f55"),
            spaceAfter=6,
        )
        cell_style = ParagraphStyle(
            "CellCZ",
            parent=styles["Normal"],
            fontName="DejaVuSans",
            fontSize=8,
            leading=10,
        )
        cell_style_bold = ParagraphStyle(
            "CellCZBold",
            parent=styles["Normal"],
            fontName="DejaVuSans-Bold",
            fontSize=8,
            leading=10,
        )

        story = [
            Paragraph("Přehled odhlášek jídel", title_style),
            Paragraph(
                f"Datum výdeje: <b>{target_date.strftime('%d.%m.%Y')}</b> | "
                f"Vygenerováno: {timezone.localtime().strftime('%d.%m.%Y %H:%M:%S')}",
                meta_style,
            ),
            Spacer(1, 4),
        ]

        if not rows:
            story.append(
                Paragraph(
                    "Pro vybraný den nebyly nalezeny žádné odhlášky.",
                    ParagraphStyle(
                        "EmptyCZ",
                        parent=styles["Normal"],
                        fontName="DejaVuSans-Bold",
                        fontSize=10,
                        textColor=colors.HexColor("#6b7280"),
                    ),
                )
            )
            doc.build(story)
            return response

        data = [
            [
                Paragraph("Kdo odhlásil", cell_style_bold),
                Paragraph("Čas", cell_style_bold),
                Paragraph("Jídla", cell_style_bold),
                Paragraph("Ks", cell_style_bold),
                Paragraph("Cena", cell_style_bold),
                Paragraph("Typ", cell_style_bold),
                Paragraph("Důvod", cell_style_bold),
            ]
        ]

        total_price = Decimal("0")
        total_items = 0
        for row in rows:
            total_price += row["total_price"]
            total_items += row["items_count"]
            data.append(
                [
                    Paragraph(row["user"], cell_style),
                    Paragraph(row["time"], cell_style),
                    Paragraph(row["meals"], cell_style),
                    Paragraph(str(row["items_count"]), cell_style),
                    Paragraph(f"{row['total_price']} Kč", cell_style),
                    Paragraph(row["tone"], cell_style),
                    Paragraph(row["reason"], cell_style),
                ]
            )

        table = Table(
            data,
            colWidths=[34 * mm, 16 * mm, 58 * mm, 10 * mm, 18 * mm, 24 * mm, 32 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f4e3")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f3b2d")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9dec2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fcf6")]),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 8))
        story.append(
            Paragraph(
                f"Celkem odhlášek: <b>{len(rows)}</b> | Celkem položek: <b>{total_items}</b> | Celková odhlášená cena: <b>{total_price} Kč</b>",
                meta_style,
            )
        )

        doc.build(story)
        return response

    def cancellations_kitchen_pdf_view(self, request):
        raw_date = request.GET.get("report_date") or timezone.localdate().isoformat()
        try:
            target_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()

        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="odhlasky_kuchyn_{target_date.strftime("%Y_%m_%d")}.pdf"'
        )

        doc = SimpleDocTemplate(
            response,
            pagesize=A4,
            rightMargin=12 * mm,
            leftMargin=12 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )

        base_dir = Path(__file__).resolve().parent.parent
        regular_font = base_dir / "static" / "fonts" / "DejaVuSans.ttf"
        bold_font = base_dir / "static" / "fonts" / "DejaVuSans-Bold.ttf"
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(regular_font)))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bold_font)))

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "KitchenTitleCZ",
            parent=styles["Heading1"],
            fontName="DejaVuSans-Bold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#1f3b2d"),
            spaceAfter=8,
        )
        meta_style = ParagraphStyle(
            "KitchenMetaCZ",
            parent=styles["Normal"],
            fontName="DejaVuSans",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#4f5f55"),
            spaceAfter=6,
        )
        cell_style = ParagraphStyle(
            "KitchenCellCZ",
            parent=styles["Normal"],
            fontName="DejaVuSans",
            fontSize=8,
            leading=10,
        )
        cell_style_bold = ParagraphStyle(
            "KitchenCellCZBold",
            parent=styles["Normal"],
            fontName="DejaVuSans-Bold",
            fontSize=8,
            leading=10,
        )

        cancelled_items = (
            OrderItem.objects.filter(
                order__datum_vydeje=target_date,
                order__status__in=["zruseno-uzivatelem", "zruseno-obsluhou", "nevyzvednuto"],
            )
            .values("menu_item__druh_jidla__nazev", "menu_item__jidlo__nazev")
            .annotate(portions=Sum("quantity"))
            .order_by("menu_item__druh_jidla__nazev", "-portions", "menu_item__jidlo__nazev")
        )

        grouped = {}
        for row in cancelled_items:
            kind = row["menu_item__druh_jidla__nazev"] or "Bez druhu"
            grouped.setdefault(kind, []).append(
                {"meal": row["menu_item__jidlo__nazev"], "portions": row["portions"] or 0}
            )

        story = [
            Paragraph("Kuchyňský souhrn odhlášek", title_style),
            Paragraph(
                f"Datum výdeje: <b>{target_date.strftime('%d.%m.%Y')}</b> | "
                f"Vygenerováno: {timezone.localtime().strftime('%d.%m.%Y %H:%M:%S')}",
                meta_style,
            ),
            Spacer(1, 4),
        ]

        if not grouped:
            story.append(
                Paragraph(
                    "Pro vybraný den nejsou evidované žádné odhlášené porce.",
                    ParagraphStyle(
                        "KitchenEmptyCZ",
                        parent=styles["Normal"],
                        fontName="DejaVuSans-Bold",
                        fontSize=10,
                        textColor=colors.HexColor("#6b7280"),
                    ),
                )
            )
            doc.build(story)
            return response

        total_portions = 0
        for meal_type, items in grouped.items():
            story.append(Paragraph(meal_type, cell_style_bold))
            table_data = [[
                Paragraph("Jídlo", cell_style_bold),
                Paragraph("Odhlášeno porcí", cell_style_bold),
            ]]
            for item in items:
                total_portions += item["portions"]
                table_data.append([
                    Paragraph(item["meal"], cell_style),
                    Paragraph(str(item["portions"]), cell_style),
                ])

            table = Table(table_data, colWidths=[130 * mm, 35 * mm], repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f4e3")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f3b2d")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9dec2")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fcf6")]),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 6))

        story.append(Paragraph(f"Celkem odhlášeno porcí: <b>{total_portions}</b>", meta_style))
        doc.build(story)
        return response


@admin.register(NastaveniVydaje)
class NastaveniVydajeAdmin(admin.ModelAdmin):
    list_display = ("timeout_seconds",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        settings_obj, _ = RealVydejSettings.objects.get_or_create()
        return HttpResponseRedirect(
            reverse("admin:provoz_jidelny_nastavenivydaje_change", args=[settings_obj.pk])
        )

    def response_add(self, request, obj, post_url_continue=None):
        messages.success(request, "Nastavení výdeje bylo uloženo.")
        return HttpResponseRedirect(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))

    def response_change(self, request, obj):
        messages.success(request, "Nastavení výdeje bylo upraveno.")
        return HttpResponseRedirect(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))
