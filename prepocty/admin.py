from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Sum, Value, When
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from .models import PrepoctyDummy
from objednavky.models import PriceRecalculationDetail, PriceRecalculationLog


@admin.register(PrepoctyDummy)
class PrepoctyDummyAdmin(admin.ModelAdmin):
    FILTER_PRESETS = {
        "7": ("Posledních 7 dní", 7),
        "30": ("Posledních 30 dní", 30),
        "90": ("Posledních 90 dní", 90),
        "365": ("Posledních 12 měsíců", 365),
        "all": ("Celá historie", None),
    }

    # nedělej žádné dotazy na model
    def get_queryset(self, request):
        from django.db.models.query import EmptyQuerySet
        return PrepoctyDummy.objects.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "",
                self.admin_site.admin_view(self.dashboard_view),
                name="prepocty_prepoctydummy_changelist",
            ),
            path(
                "spustit/",
                self.admin_site.admin_view(self.redirect_to_recalculation),
                name="prepocty_run_recalculation",
            ),
            path(
                "historie/",
                self.admin_site.admin_view(self.redirect_to_history),
                name="prepocty_history",
            ),
            path(
                "detaily/",
                self.admin_site.admin_view(self.redirect_to_details),
                name="prepocty_details",
            ),
        ]
        return custom + urls

    def redirect_to_recalculation(self, request):
        return redirect("admin:objednavky_order_price_recalculation")

    def redirect_to_history(self, request):
        return redirect("admin:objednavky_pricerecalculationlog_changelist")

    def redirect_to_details(self, request):
        return redirect("admin:objednavky_pricerecalculationdetail_changelist")

    def dashboard_view(self, request):
        now = timezone.now()
        selected_preset = request.GET.get("obdobi", "30")
        if selected_preset not in self.FILTER_PRESETS:
            selected_preset = "30"

        selected_label, selected_days = self.FILTER_PRESETS[selected_preset]
        filtered_from = now - timedelta(days=selected_days) if selected_days else None
        last_7_days = now - timedelta(days=7)
        last_30_days = now - timedelta(days=30)

        logs_qs = PriceRecalculationLog.objects.select_related("created_by").order_by("-created_at")
        latest_run = logs_qs.first()
        filtered_logs_qs = logs_qs.filter(created_at__gte=filtered_from) if filtered_from else logs_qs
        recent_logs = list(filtered_logs_qs[:8])
        filtered_summary = filtered_logs_qs.aggregate(
            run_count=Count("id"),
            orders_affected=Sum("orders_affected"),
            items_affected=Sum("items_affected"),
            total_price_diff=Sum("total_price_diff"),
        )

        summary_7_days = logs_qs.filter(created_at__gte=last_7_days).aggregate(
            run_count=Count("id"),
            orders_affected=Sum("orders_affected"),
            items_affected=Sum("items_affected"),
            total_price_diff=Sum("total_price_diff"),
        )
        summary_30_days = logs_qs.filter(created_at__gte=last_30_days).aggregate(
            run_count=Count("id"),
            orders_affected=Sum("orders_affected"),
            items_affected=Sum("items_affected"),
            total_price_diff=Sum("total_price_diff"),
        )
        overall_summary = logs_qs.aggregate(
            run_count=Count("id"),
            orders_affected=Sum("orders_affected"),
            items_affected=Sum("items_affected"),
            total_price_diff=Sum("total_price_diff"),
        )

        abs_diff = Case(
            When(price_diff__lt=0, then=F("price_diff") * Value(Decimal("-1.00"))),
            default=F("price_diff"),
            output_field=DecimalField(max_digits=8, decimal_places=2),
        )
        line_total_diff = ExpressionWrapper(
            F("price_diff") * F("order_item__quantity"),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
        abs_line_total_diff = Case(
            When(line_total_diff__lt=0, then=F("line_total_diff") * Value(Decimal("-1.00"))),
            default=F("line_total_diff"),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
        biggest_changes_qs = PriceRecalculationDetail.objects.select_related(
            "log",
            "order_item__order__user",
            "order_item__menu_item__jidlo",
            "order_item__menu_item__druh_jidla",
        ).annotate(
            abs_price_diff=abs_diff,
            line_total_diff=line_total_diff,
            abs_line_total_diff=abs_line_total_diff,
        )
        if filtered_from:
            biggest_changes_qs = biggest_changes_qs.filter(log__created_at__gte=filtered_from)
        biggest_changes = list(biggest_changes_qs.order_by("-abs_line_total_diff", "-log__created_at")[:8])

        warning_cards = []

        major_diff_threshold = Decimal("250.00")
        detail_alert_threshold = Decimal("15.00")
        for log in recent_logs:
            abs_total_diff = abs(log.total_price_diff or Decimal("0"))
            log.is_major_impact = abs_total_diff >= major_diff_threshold
            log.impact_label = "Výrazný zásah" if log.is_major_impact else "Běžný běh"
        for detail in biggest_changes:
            detail.is_major_impact = abs(detail.line_total_diff or Decimal("0")) >= detail_alert_threshold

        consistency_window = recent_logs if recent_logs else list(logs_qs[:8])
        consistency_checks = []
        for log in consistency_window:
            detail_totals = (
                PriceRecalculationDetail.objects.filter(log=log)
                .aggregate(
                    detail_items=Count("id"),
                    detail_total_diff=Sum(
                        ExpressionWrapper(
                            F("price_diff") * F("order_item__quantity"),
                            output_field=DecimalField(max_digits=10, decimal_places=2),
                        )
                    ),
                )
            )
            detail_items = detail_totals["detail_items"] or 0
            detail_total_diff = detail_totals["detail_total_diff"] or Decimal("0.00")
            expected_total = log.total_price_diff or Decimal("0.00")
            log.details_match = detail_items == (log.items_affected or 0) and detail_total_diff == expected_total
            consistency_checks.append(
                {
                    "log": log,
                    "detail_items": detail_items,
                    "detail_total_diff": detail_total_diff,
                    "matches": log.details_match,
                }
            )

        inconsistent_runs = [check for check in consistency_checks if not check["matches"]]
        if inconsistent_runs:
            warning_cards.append(
                {
                    "tone": "danger",
                    "title": "Nesoulad mezi souhrnem a detaily přepočtu",
                    "message": "U některých běhů nesedí počet detailních změn nebo jejich finanční součet vůči auditnímu logu. Tyto běhy potřebují ruční kontrolu.",
                }
            )

        if not latest_run:
            warning_cards.append(
                {
                    "tone": "danger",
                    "title": "Přepočet zatím nikdy neběžel",
                    "message": "V systému není žádný auditní záznam o přepočtu cen. Provoz teď stojí jen na ruční jistotě, že ceny a dotace odpovídají aktuálním pravidlům.",
                }
            )
        else:
            if latest_run.created_at < now - timedelta(days=14):
                warning_cards.append(
                    {
                        "tone": "warning",
                        "title": "Přepočet je už delší dobu starý",
                        "message": f"Poslední běh proběhl {timezone.localtime(latest_run.created_at).strftime('%d.%m.%Y %H:%M')}. Pokud se mezitím měnily ceny, dotace nebo pravidla skupin, je dobré to zkontrolovat.",
                    }
                )
            if latest_run.items_affected == 0:
                warning_cards.append(
                    {
                        "tone": "info",
                        "title": "Poslední běh nic nezměnil",
                        "message": "To může být v pořádku, ale stojí za ověření, jestli se přepočet nepouštěl zbytečně nebo na příliš úzké období.",
                    }
                )

        strong_diff_threshold = Decimal("500.00")
        if filtered_logs_qs.filter(total_price_diff__gte=strong_diff_threshold).exists() or filtered_logs_qs.filter(
            total_price_diff__lte=-strong_diff_threshold
        ).exists():
            warning_cards.append(
                {
                    "tone": "warning",
                    "title": "Některé běhy udělaly výrazný finanční zásah",
                    "message": f"Ve zvoleném období „{selected_label}“ jsou přepočty s rozdílem alespoň 500 Kč. Pro účetní a audit je dobré tyto běhy projít detailně.",
                }
            )

        def safe_value(value):
            return value if value is not None else 0

        impact_cards = [
            {
                "label": "Poslední běh",
                "value": timezone.localtime(latest_run.created_at).strftime("%d.%m.%Y %H:%M") if latest_run else "Zatím nikdy",
                "meta": (
                    f"{latest_run.items_affected} položek v {latest_run.orders_affected} objednávkách"
                    if latest_run
                    else "Bez auditního záznamu"
                ),
                "tone": "success" if latest_run else "danger",
            },
            {
                "label": "Za posledních 7 dní",
                "value": safe_value(summary_7_days["run_count"]),
                "meta": f"{safe_value(summary_7_days['items_affected'])} položek, rozdíl {safe_value(summary_7_days['total_price_diff']):+.2f} Kč",
                "tone": "primary",
            },
            {
                "label": f"Ve filtru: {selected_label}",
                "value": safe_value(filtered_summary["run_count"]),
                "meta": (
                    f"{safe_value(filtered_summary['orders_affected'])} objednávek, "
                    f"{safe_value(filtered_summary['items_affected'])} položek, "
                    f"rozdíl {safe_value(filtered_summary['total_price_diff']):+.2f} Kč"
                ),
                "tone": "info",
            },
            {
                "label": "Celkem v historii",
                "value": safe_value(overall_summary["run_count"]),
                "meta": f"{safe_value(overall_summary['items_affected'])} položek, rozdíl {safe_value(overall_summary['total_price_diff']):+.2f} Kč",
                "tone": "warning",
            },
            {
                "label": "Auditní shoda",
                "value": "OK" if not inconsistent_runs else f"{len(inconsistent_runs)}× problém",
                "meta": "Porovnání logu a detailů přepočtu v posledních bězích",
                "tone": "success" if not inconsistent_runs else "danger",
            },
        ]

        context = dict(
            self.admin_site.each_context(request),
            title="Přepočty",
            latest_run=latest_run,
            recent_logs=recent_logs,
            biggest_changes=biggest_changes,
            warning_cards=warning_cards,
            impact_cards=impact_cards,
            consistency_checks=consistency_checks,
            selected_preset=selected_preset,
            filter_presets=[
                {"key": key, "label": label}
                for key, (label, _days) in self.FILTER_PRESETS.items()
            ],
            selected_label=selected_label,
        )
        return TemplateResponse(
            request,
            "admin/prepocty/prepocty_dummy_dashboard.html",
            context,
        )
