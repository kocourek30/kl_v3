from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.utils import timezone

from kliknijidlo.pdf_utils import czech_pdf_styles, decimal_cs, html_cell, money_cs, percent_cs, safe_table

from dotace.models import Dotace
from objednavky.models import OrderItem
from pokladna.models import PokladniDoklad, PokladniPolozka
from users.models import Vklad

from .models import FinancniDashboard


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)
BALANCE_ORDER_STATUSES = ["zalozena-obsluhou", "objednano", "vydano", "nevyzvednuto"]
REPORT_TYPES = [
    ("souhrn", "Souhrn"),
    ("dph", "DPH"),
    ("plu", "PLU"),
    ("dotace", "Dotace"),
    ("konta", "Konta strávníků"),
]


def _money(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def _sum(qs, expression):
    return _money(qs.aggregate(total=Sum(expression))["total"])


def _parse_date(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return default


def _fmt(value):
    return decimal_cs(_money(value), places=2, trim=True)


def _cell(value):
    if isinstance(value, Decimal):
        return _fmt(value)
    return "" if value is None else str(value)


def _label(choices, key):
    return dict(choices).get(key, key or "Neuvedeno")


def _order_sum(qs):
    expression = ExpressionWrapper(F("quantity") * F("cena"), output_field=MONEY_FIELD)
    return _sum(qs, expression)


@admin.register(FinancniDashboard)
class FinancniDashboardAdmin(admin.ModelAdmin):
    change_list_template = "admin/finance/dashboard.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_staff

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).none()

    def changelist_view(self, request, extra_context=None):
        context = self._build_context(request)
        export = request.GET.get("export")
        if export == "xls":
            return self._export_xls(context)
        if export == "pdf":
            return self._export_pdf(context)
        return TemplateResponse(request, self.change_list_template, context)

    def _build_context(self, request):
        today = timezone.localdate()
        datum_od = _parse_date(request.GET.get("od"), today.replace(day=1))
        datum_do = _parse_date(request.GET.get("do"), today)
        report_type = request.GET.get("report") or "souhrn"
        if report_type not in dict(REPORT_TYPES):
            report_type = "souhrn"
        if datum_do < datum_od:
            datum_od, datum_do = datum_do, datum_od

        pokladni_doklady = PokladniDoklad.objects.filter(
            Q(uzavren_at__date__gte=datum_od, uzavren_at__date__lte=datum_do)
            | Q(uzavren_at__isnull=True, datum__date__gte=datum_od, datum__date__lte=datum_do),
            stav=PokladniDoklad.STAV_UZAVRENO,
        )
        pokladni_polozky = PokladniPolozka.objects.filter(doklad__in=pokladni_doklady)
        stornovane_doklady = PokladniDoklad.objects.filter(
            stornovano_at__date__gte=datum_od,
            stornovano_at__date__lte=datum_do,
            stav=PokladniDoklad.STAV_STORNOVANO,
        )

        order_total_expr = ExpressionWrapper(F("quantity") * F("cena"), output_field=MONEY_FIELD)
        menu_price_expr = ExpressionWrapper(
            F("quantity") * F("menu_item__jidlo__cena"),
            output_field=MONEY_FIELD,
        )
        objednane_polozky = OrderItem.objects.select_related(
            "order__user__stravovaci_skupina",
            "menu_item__jidlo",
            "menu_item__druh_jidla",
        ).filter(
            order__datum_vydeje__gte=datum_od,
            order__datum_vydeje__lte=datum_do,
        )
        trzebni_polozky = objednane_polozky.filter(
            Q(vydano=True) | Q(order__status="nevyzvednuto")
        ).exclude(
            order__status__in=["zruseno-uzivatelem", "zruseno-obsluhou"]
        )

        pokladna_podle_plateb = []
        pokladna_castky = {}
        for platba, nazev in PokladniDoklad.ZPUSOBY_PLATBY:
            doklady = pokladni_doklady.filter(zpusob_platby=platba)
            castka = _sum(doklady, "celkem_s_dph")
            pokladna_castky[platba] = castka
            pokladna_podle_plateb.append(
                {
                    "kod": platba,
                    "nazev": nazev,
                    "pocet": doklady.count(),
                    "castka": castka,
                    "penezni_prijem": platba != PokladniDoklad.PLATBA_KONTO,
                }
            )

        vklady = Vklad.objects.filter(datum__date__gte=datum_od, datum__date__lte=datum_do)
        vklady_kladne = _sum(vklady.filter(castka__gt=0, status="standard"), "castka")
        vklady_podle_uhrady = []
        for kod, nazev in Vklad.ZPUSOBY_UHRADY:
            qs = vklady.filter(castka__gt=0, status="standard", zpusob_uhrady=kod)
            vklady_podle_uhrady.append({
                "kod": kod,
                "nazev": nazev,
                "pocet": qs.count(),
                "castka": _sum(qs, "castka"),
            })
        vklady_bez_uhrady = vklady.filter(castka__gt=0, status="standard", zpusob_uhrady="")
        if vklady_bez_uhrady.exists():
            vklady_podle_uhrady.append({
                "kod": "",
                "nazev": "Bez uvedené úhrady",
                "pocet": vklady_bez_uhrady.count(),
                "castka": _sum(vklady_bez_uhrady, "castka"),
            })
        cerpani_konta = abs(_sum(vklady.filter(castka__lt=0, status="standard"), "castka"))
        nulovani_konta = _sum(vklady.filter(status="nulovani_konta"), "castka")

        pokladna_trzba = _sum(pokladni_doklady, "celkem_s_dph")
        objednavky_trzba = _sum(trzebni_polozky, order_total_expr)
        objednavky_cenik = _sum(trzebni_polozky, menu_price_expr)
        objednavky_sleva_dotace = max(Decimal("0"), objednavky_cenik - objednavky_trzba)
        dotace_pripsane = _sum(
            Dotace.objects.filter(datum__gte=datum_od, datum__lte=datum_do),
            "castka",
        )
        storna_pokladna = _sum(stornovane_doklady, "celkem_s_dph")
        penezni_prijem = (
            pokladna_castky.get(PokladniDoklad.PLATBA_HOTOVOST, Decimal("0"))
            + pokladna_castky.get(PokladniDoklad.PLATBA_KARTA, Decimal("0"))
            + pokladna_castky.get(PokladniDoklad.PLATBA_QR, Decimal("0"))
            + vklady_kladne
        )

        dph_souhrn = list(
            pokladni_polozky.values("dph_sazba", "plu__dph_skupina__nazev")
            .annotate(
                zaklad=Sum("zaklad_dph"),
                dph=Sum("castka_dph"),
                celkem=Sum("castka_celkem"),
                radku=Count("id"),
                mnozstvi=Sum("mnozstvi"),
            )
            .order_by("dph_sazba")
        )
        for row in dph_souhrn:
            row["nazev"] = row.pop("plu__dph_skupina__nazev") or f"{row['dph_sazba']} %"
            row["zaklad"] = _money(row["zaklad"])
            row["dph"] = _money(row["dph"])
            row["celkem"] = _money(row["celkem"])

        objednavky_podle_skupin = list(
            trzebni_polozky.values("order__user__stravovaci_skupina__nazev")
            .annotate(
                porci=Sum("quantity"),
                obrat=Sum(order_total_expr),
                objednavek=Count("order", distinct=True),
            )
            .order_by("order__user__stravovaci_skupina__nazev")
        )
        for row in objednavky_podle_skupin:
            row["nazev"] = row.pop("order__user__stravovaci_skupina__nazev") or "Bez skupiny"
            row["obrat"] = _money(row["obrat"])

        objednavky_podle_druhu = list(
            trzebni_polozky.values("menu_item__druh_jidla__nazev")
            .annotate(
                porci=Sum("quantity"),
                obrat=Sum(order_total_expr),
                objednavek=Count("order", distinct=True),
            )
            .order_by("menu_item__druh_jidla__nazev")
        )
        for row in objednavky_podle_druhu:
            row["nazev"] = row.pop("menu_item__druh_jidla__nazev") or "Bez druhu jídla"
            row["obrat"] = _money(row["obrat"])

        pokladna_podle_plu = list(
            pokladni_polozky.values("plu_id", "nazev_snapshot", "plu__kategorie__nazev", "dph_sazba")
            .annotate(
                mnozstvi=Sum("mnozstvi"),
                obrat=Sum("castka_celkem"),
                zaklad=Sum("zaklad_dph"),
                dph=Sum("castka_dph"),
                radku=Count("id"),
            )
            .order_by("-obrat")
        )
        for row in pokladna_podle_plu:
            row["nazev"] = row.pop("nazev_snapshot") or "Bez názvu"
            row["kategorie"] = row.pop("plu__kategorie__nazev") or "Bez kategorie"
            row["obrat"] = _money(row["obrat"])
            row["zaklad"] = _money(row["zaklad"])
            row["dph"] = _money(row["dph"])

        dotace_podle_skupin = list(
            trzebni_polozky.values("order__user__stravovaci_skupina__nazev")
            .annotate(
                porci=Sum("quantity"),
                cenik=Sum(menu_price_expr),
                zaplaceno=Sum(order_total_expr),
                objednavek=Count("order", distinct=True),
            )
            .order_by("order__user__stravovaci_skupina__nazev")
        )
        for row in dotace_podle_skupin:
            row["nazev"] = row.pop("order__user__stravovaci_skupina__nazev") or "Bez skupiny"
            row["cenik"] = _money(row["cenik"])
            row["zaplaceno"] = _money(row["zaplaceno"])
            row["dotace"] = _money(max(Decimal("0"), row["cenik"] - row["zaplaceno"]))

        konta_report = self._konta_report(datum_od, datum_do)
        report_headers, report_rows, report_title = self._detail_report(
            report_type=report_type,
            dph_souhrn=dph_souhrn,
            pokladna_podle_plu=pokladna_podle_plu,
            dotace_podle_skupin=dotace_podle_skupin,
            konta_report=konta_report,
        )

        context = {
            **self.admin_site.each_context(request),
            "title": "Finanční přehled",
            "datum_od": datum_od,
            "datum_do": datum_do,
            "report_type": report_type,
            "report_types": REPORT_TYPES,
            "report_title": report_title,
            "report_headers": report_headers,
            "report_rows": report_rows,
            "obrat_celkem": _money(pokladna_trzba + objednavky_trzba),
            "penezni_prijem": _money(penezni_prijem),
            "pokladna_trzba": pokladna_trzba,
            "objednavky_trzba": objednavky_trzba,
            "objednavky_cenik": objednavky_cenik,
            "objednavky_sleva_dotace": _money(objednavky_sleva_dotace),
            "dotace_pripsane": dotace_pripsane,
            "storna_pokladna": storna_pokladna,
            "vklady_kladne": vklady_kladne,
            "vklady_podle_uhrady": vklady_podle_uhrady,
            "cerpani_konta": _money(cerpani_konta),
            "nulovani_konta": nulovani_konta,
            "konta_report": konta_report,
            "pocet_pokladnich_dokladu": pokladni_doklady.count(),
            "pocet_objednavek": trzebni_polozky.values("order").distinct().count(),
            "pocet_porci": trzebni_polozky.aggregate(total=Sum("quantity"))["total"] or 0,
            "pokladna_podle_plateb": pokladna_podle_plateb,
            "dph_souhrn": dph_souhrn,
            "objednavky_podle_skupin": objednavky_podle_skupin,
            "objednavky_podle_druhu": objednavky_podle_druhu,
            "pokladna_podle_plu": pokladna_podle_plu,
            "dotace_podle_skupin": dotace_podle_skupin,
            "query_string": f"od={datum_od:%Y-%m-%d}&do={datum_do:%Y-%m-%d}&report={report_type}",
        }
        return context

    def _konta_report(self, datum_od, datum_do):
        User = get_user_model()
        users = list(
            User.objects
            .filter(is_active=True)
            .select_related("stravovaci_skupina")
            .order_by("last_name", "first_name", "username")
        )
        user_ids = [user.id for user in users]
        if not user_ids:
            return {
                "rows": [],
                "totals": {
                    "pocatecni": Decimal("0"),
                    "vklady": Decimal("0"),
                    "dotace": Decimal("0"),
                    "cerpani_objednavky": Decimal("0"),
                    "cerpani_pokladna": Decimal("0"),
                    "nulovani": Decimal("0"),
                    "konecny": Decimal("0"),
                },
            }

        order_total_expr = ExpressionWrapper(F("quantity") * F("cena"), output_field=MONEY_FIELD)

        def grouped_sum(qs, user_field, value_field):
            return {
                row[user_field]: _money(row["total"])
                for row in qs.values(user_field).annotate(total=Sum(value_field))
            }

        def grouped_order_sum(qs):
            return {
                row["order__user_id"]: _money(row["total"])
                for row in qs.values("order__user_id").annotate(total=Sum(order_total_expr))
            }

        before_vklady_map = grouped_sum(
            Vklad.objects.filter(uzivatel_id__in=user_ids, datum__date__lt=datum_od),
            "uzivatel_id",
            "castka",
        )
        before_dotace_map = grouped_sum(
            Dotace.objects.filter(uzivatel_id__in=user_ids, datum__lt=datum_od),
            "uzivatel_id",
            "castka",
        )
        before_orders_map = grouped_order_sum(
            OrderItem.objects.filter(
                order__user_id__in=user_ids,
                order__datum_vydeje__lt=datum_od,
                order__status__in=BALANCE_ORDER_STATUSES,
            )
        )
        vklady_map = grouped_sum(
            Vklad.objects.filter(
                uzivatel_id__in=user_ids,
                datum__date__gte=datum_od,
                datum__date__lte=datum_do,
                status="standard",
                castka__gt=0,
            ),
            "uzivatel_id",
            "castka",
        )
        cerpani_pokladna_map = grouped_sum(
            Vklad.objects.filter(
                uzivatel_id__in=user_ids,
                datum__date__gte=datum_od,
                datum__date__lte=datum_do,
                status="standard",
                castka__lt=0,
            ),
            "uzivatel_id",
            "castka",
        )
        nulovani_map = grouped_sum(
            Vklad.objects.filter(
                uzivatel_id__in=user_ids,
                datum__date__gte=datum_od,
                datum__date__lte=datum_do,
                status="nulovani_konta",
            ),
            "uzivatel_id",
            "castka",
        )
        dotace_map = grouped_sum(
            Dotace.objects.filter(
                uzivatel_id__in=user_ids,
                datum__gte=datum_od,
                datum__lte=datum_do,
            ),
            "uzivatel_id",
            "castka",
        )
        cerpani_objednavky_map = grouped_order_sum(
            OrderItem.objects.filter(
                order__user_id__in=user_ids,
                order__datum_vydeje__gte=datum_od,
                order__datum_vydeje__lte=datum_do,
                order__status__in=BALANCE_ORDER_STATUSES,
            )
        )

        rows = []
        totals = {
            "pocatecni": Decimal("0"),
            "vklady": Decimal("0"),
            "dotace": Decimal("0"),
            "cerpani_objednavky": Decimal("0"),
            "cerpani_pokladna": Decimal("0"),
            "nulovani": Decimal("0"),
            "konecny": Decimal("0"),
        }

        for user in users:
            before_vklady = before_vklady_map.get(user.id, Decimal("0"))
            before_dotace = before_dotace_map.get(user.id, Decimal("0"))
            before_orders = before_orders_map.get(user.id, Decimal("0"))
            pocatecni = _money(before_vklady + before_dotace - before_orders)

            vklady = vklady_map.get(user.id, Decimal("0"))
            cerpani_pokladna = abs(cerpani_pokladna_map.get(user.id, Decimal("0")))
            nulovani = nulovani_map.get(user.id, Decimal("0"))
            dotace = dotace_map.get(user.id, Decimal("0"))
            cerpani_objednavky = cerpani_objednavky_map.get(user.id, Decimal("0"))
            konecny = _money(pocatecni + vklady + dotace + nulovani - cerpani_objednavky - cerpani_pokladna)

            if not any([pocatecni, vklady, dotace, cerpani_objednavky, cerpani_pokladna, nulovani, konecny]):
                continue

            row = {
                "uzivatel": user.get_full_name() or user.username,
                "username": user.username,
                "skupina": str(user.stravovaci_skupina) if getattr(user, "stravovaci_skupina_id", None) else "Bez skupiny",
                "pocatecni": pocatecni,
                "vklady": vklady,
                "dotace": dotace,
                "cerpani_objednavky": cerpani_objednavky,
                "cerpani_pokladna": cerpani_pokladna,
                "nulovani": nulovani,
                "konecny": konecny,
            }
            rows.append(row)
            for key in totals:
                totals[key] = _money(totals[key] + row[key])

        return {"rows": rows, "totals": totals}

    def _detail_report(self, report_type, dph_souhrn, pokladna_podle_plu, dotace_podle_skupin, konta_report):
        if report_type == "dph":
            return (
                ["DPH skupina", "Sazba", "Řádků", "Množství", "Základ", "DPH", "Celkem"],
                [
                    [r["nazev"], percent_cs(r["dph_sazba"]), r["radku"], decimal_cs(r["mnozstvi"]), r["zaklad"], r["dph"], r["celkem"]]
                    for r in dph_souhrn
                ],
                "Přehled podle DPH",
            )
        if report_type == "plu":
            return (
                ["PLU", "Kategorie", "Sazba DPH", "Řádků", "Množství", "Základ", "DPH", "Obrat"],
                [
                    [r["nazev"], r["kategorie"], percent_cs(r["dph_sazba"]), r["radku"], decimal_cs(r["mnozstvi"]), r["zaklad"], r["dph"], r["obrat"]]
                    for r in pokladna_podle_plu
                ],
                "Přehled podle PLU",
            )
        if report_type == "dotace":
            return (
                ["Stravovací skupina", "Objednávek", "Porcí", "Ceníková hodnota", "Zaplaceno", "Dotace a sleva"],
                [
                    [r["nazev"], r["objednavek"], r["porci"], r["cenik"], r["zaplaceno"], r["dotace"]]
                    for r in dotace_podle_skupin
                ],
                "Přehled dotací a slev",
            )
        if report_type == "konta":
            return (
                ["Strávník", "Přihlašovací jméno", "Skupina", "Počáteční zůstatek", "Vklady", "Dotace", "Čerpání objednávkami", "Čerpání v pokladně", "Nulování", "Konečný zůstatek"],
                [
                    [
                        r["uzivatel"],
                        r["username"],
                        r["skupina"],
                        r["pocatecni"],
                        r["vklady"],
                        r["dotace"],
                        r["cerpani_objednavky"],
                        r["cerpani_pokladna"],
                        r["nulovani"],
                        r["konecny"],
                    ]
                    for r in konta_report["rows"]
                ],
                "Přehled kont strávníků",
            )
        return (
            ["Ukazatel", "Hodnota"],
            [],
            "Souhrnný finanční přehled",
        )

    def _export_xls(self, context):
        response = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="financni-prehled.xls"'
        rows = [
            "<meta charset='utf-8'>",
            f"<h1>{context['report_title']}</h1>",
            f"<p>Období: {context['datum_od']:%d.%m.%Y} - {context['datum_do']:%d.%m.%Y}</p>",
            "<table border='1'>",
            "<tr><th>Ukazatel</th><th>Částka</th></tr>",
            f"<tr><td>Obrat celkem</td><td>{money_cs(context['obrat_celkem'])}</td></tr>",
            f"<tr><td>Peněžní příjem</td><td>{money_cs(context['penezni_prijem'])}</td></tr>",
            f"<tr><td>Pokladna</td><td>{money_cs(context['pokladna_trzba'])}</td></tr>",
            f"<tr><td>Objednávkový systém</td><td>{money_cs(context['objednavky_trzba'])}</td></tr>",
            f"<tr><td>Vklady na konta</td><td>{money_cs(context['vklady_kladne'])}</td></tr>",
            f"<tr><td>Dotace a slevy objednávek</td><td>{money_cs(context['objednavky_sleva_dotace'])}</td></tr>",
            f"<tr><td>Připsané dotace na konta</td><td>{money_cs(context['dotace_pripsane'])}</td></tr>",
            "</table>",
            f"<h2>{context['report_title']}</h2>",
            "<table border='1'><tr>",
        ]
        rows.extend(f"<th>{html_cell(header)}</th>" for header in context["report_headers"])
        rows.append("</tr>")
        for report_row in context["report_rows"]:
            rows.append("<tr>")
            rows.extend(f"<td>{html_cell(_cell(value))}</td>" for value in report_row)
            rows.append("</tr>")
        rows.append("</table>")
        response.write("".join(rows))
        return response

    def _export_pdf(self, context):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
        styles, font_name = czech_pdf_styles()
        story = [
            Paragraph(context["report_title"], styles["Title"]),
            Paragraph(f"Období: {context['datum_od']:%d.%m.%Y} - {context['datum_do']:%d.%m.%Y}", styles["Normal"]),
            Spacer(1, 12),
        ]
        story.append(self._pdf_table([
            ["Ukazatel", "Částka"],
            ["Obrat celkem", money_cs(context["obrat_celkem"])],
            ["Peněžní příjem", money_cs(context["penezni_prijem"])],
            ["Pokladna", money_cs(context["pokladna_trzba"])],
            ["Objednávkový systém", money_cs(context["objednavky_trzba"])],
            ["Vklady na konta", money_cs(context["vklady_kladne"])],
            ["Dotace a slevy objednávek", money_cs(context["objednavky_sleva_dotace"])],
            ["Připsané dotace na konta", money_cs(context["dotace_pripsane"])],
        ], [260, 160], font_name=font_name))
        story.append(Spacer(1, 12))
        story.append(Paragraph(context["report_title"], styles["Heading2"]))
        data = [context["report_headers"]]
        data.extend([[_cell(value) for value in row] for row in context["report_rows"]])
        if len(data) == 1:
            data.append(["Žádná data"] + [""] * max(0, len(context["report_headers"]) - 1))
        usable_width = 790
        col_count = max(1, len(context["report_headers"]))
        story.append(self._pdf_table(data, [usable_width / col_count] * col_count, font_name=font_name))
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="financni-prehled.pdf"'
        return response

    def _pdf_table(self, data, col_widths, font_name=None):
        from reportlab.lib import colors

        return safe_table(
            data,
            col_widths,
            font_name=font_name,
            style_commands=[
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#54ae43")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf7")]),
                ("PADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ],
        )
