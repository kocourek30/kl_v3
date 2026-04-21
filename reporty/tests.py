from datetime import date
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import RequestFactory, TestCase

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem

from .admin import ReportAdmin, ReportForm
from .models import ReportDummy


class ReportAdminCarkovniceTests(TestCase):
    def setUp(self):
        self.admin = ReportAdmin(ReportDummy, AdminSite())
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="report-user",
            first_name="Test",
            last_name="Stravnik",
            osobni_cislo="12345",
            identifikacni_medium="CARD-1",
        )
        self.snidane = DruhJidla.objects.create(nazev="Snídaně", poradi=10)
        self.obed = DruhJidla.objects.create(nazev="Oběd", poradi=20)

        self.snidane_jidlo = Jidlo.objects.create(
            nazev="Toast",
            cena=Decimal("18.00"),
            druh=self.snidane,
        )
        self.obed_jidlo = Jidlo.objects.create(
            nazev="Guláš",
            cena=Decimal("95.00"),
            druh=self.obed,
        )

        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 1),
            platnost_do=date(2026, 4, 1),
        )
        self.snidane_polozka = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.snidane,
            jidlo=self.snidane_jidlo,
        )
        self.obed_polozka = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.obed,
            jidlo=self.obed_jidlo,
        )

    def test_carkovnice_builds_monthly_matrix_from_issued_items(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 3),
            status="vydano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.snidane_polozka,
            quantity=1,
            cena=Decimal("18.00"),
            vydano=True,
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=1,
            cena=Decimal("60.00"),
            vydano=True,
        )

        form = ReportForm(
            data={
                "period": "current_month",
                "grouping": "day",
                "month": "4",
                "year": "2026",
                "search": "",
            }
        )

        rows, totals, grouping = self.admin.get_carkovnice_report(form)

        self.assertEqual(grouping, "month")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["day_strings"][3], "SN OB")
        self.assertEqual(rows[0]["total_portions"], 2)
        self.assertEqual(totals["users_count"], 1)
        self.assertEqual(totals["total_portions"], 2)
        self.assertEqual(totals["day_totals"][3]["portions"], 2)
        self.assertEqual(totals["day_totals"][3]["users"], 1)
        self.assertEqual(totals["subsidized_portions"], 1)
        self.assertEqual(totals["full_price_total"], Decimal("113.00"))
        self.assertEqual(totals["subsidy_total"], Decimal("35.00"))
        self.assertEqual(totals["paid_total"], Decimal("78.00"))
        self.assertEqual({item["name"] for item in totals["legend"]}, {"Snídaně", "Oběd"})
        self.assertEqual(len(totals["financial_rows"]), 2)

    def test_carkovnice_excludes_not_issued_items(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 4),
            status="objednano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=1,
            cena=Decimal("60.00"),
            vydano=False,
        )

        form = ReportForm(
            data={
                "period": "current_month",
                "grouping": "day",
                "month": "4",
                "year": "2026",
                "search": "",
            }
        )

        rows, totals, grouping = self.admin.get_carkovnice_report(form)

        self.assertEqual(grouping, "month")
        self.assertEqual(rows, [])
        self.assertEqual(totals["total_portions"], 0)

    def test_dashboard_view_carkovnice_defaults_missing_grouping(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 3),
            status="vydano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=1,
            cena=Decimal("60.00"),
            vydano=True,
        )

        request = self.factory.get(
            "/admin/reporty/reportdummy/",
            data=QueryDict("report=carkovnice&month=4&year=2026"),
        )
        request.user = get_user_model().objects.create_superuser(
            username="report-admin",
            password="test12345",
            email="report-admin@example.com",
        )

        response = self.admin.dashboard_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Stravnik")
        self.assertContains(response, "Denní součet porcí a počtu strávníků")

    def test_dashboard_view_carkovnice_pdf_export_returns_pdf(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 3),
            status="vydano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=1,
            cena=Decimal("82.00"),
            vydano=True,
        )

        request = self.factory.get(
            "/admin/reporty/reportdummy/",
            data=QueryDict("report=carkovnice&month=4&year=2026&export=pdf"),
        )
        request.user = get_user_model().objects.create_superuser(
            username="pdf-report-admin",
            password="test12345",
            email="pdf-report-admin@example.com",
        )

        response = self.admin.dashboard_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_dashboard_view_finance_pdf_export_returns_pdf(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 5),
            status="vydano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=2,
            cena=Decimal("82.00"),
            vydano=True,
        )

        request = self.factory.get(
            "/admin/reporty/reportdummy/",
            data=QueryDict("report=finance-dotace&period=current_month&grouping=total&export=pdf"),
        )
        request.user = get_user_model().objects.create_superuser(
            username="finance-pdf-report-admin",
            password="test12345",
            email="finance-pdf-report-admin@example.com",
        )

        response = self.admin.dashboard_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_financial_subsidy_report_aggregates_full_discount_and_paid_price(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 5),
            status="vydano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.obed_polozka,
            quantity=2,
            cena=Decimal("82.00"),
            vydano=True,
        )

        form = ReportForm(
            data={
                "period": "current_month",
                "grouping": "total",
                "search": "",
            }
        )

        rows, totals, grouping = self.admin.get_financial_subsidy_report(form)

        self.assertEqual(grouping, "total")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_portions"], 2)
        self.assertEqual(rows[0]["subsidized_portions"], 2)
        self.assertEqual(rows[0]["full_price_total"], Decimal("190.00"))
        self.assertEqual(rows[0]["subsidy_total"], Decimal("26.00"))
        self.assertEqual(rows[0]["paid_total"], Decimal("164.00"))
        self.assertEqual(totals["subsidy_total"], Decimal("26.00"))
