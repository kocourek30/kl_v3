from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem, PriceRecalculationDetail, PriceRecalculationLog


class PrepoctyDashboardTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="prepocty-admin",
            password="test",
            email="prepocty@example.com",
        )
        self.client.force_login(self.admin_user)
        self.customer = get_user_model().objects.create_user(
            username="zakaznik-1",
            password="test",
            first_name="Test",
            last_name="Zakaznik",
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(
            nazev="Kuřecí na paprice",
            cena=Decimal("99.00"),
            druh=self.druh,
        )
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 21),
            platnost_do=date(2026, 4, 21),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )
        self.order = Order.objects.create(
            user=self.customer,
            datum_vydeje=date(2026, 4, 21),
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.menu_item,
            quantity=1,
            cena=Decimal("99.00"),
        )

    def test_dashboard_shows_empty_state_when_no_logs_exist(self):
        response = self.client.get(reverse("admin:prepocty_prepoctydummy_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Přepočet zatím nikdy neběžel")
        self.assertContains(response, "Zatím nikdy")

    def test_dashboard_shows_recent_runs_and_biggest_changes(self):
        log = PriceRecalculationLog.objects.create(
            created_by=self.admin_user,
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 21),
            orders_affected=1,
            items_affected=1,
            total_price_diff=Decimal("13.00"),
            note="Testovací přepočet",
        )
        PriceRecalculationDetail.objects.create(
            log=log,
            order_item=self.order_item,
            old_price=Decimal("99.00"),
            new_price=Decimal("112.00"),
            price_diff=Decimal("13.00"),
        )

        response = self.client.get(reverse("admin:prepocty_prepoctydummy_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Poslední běhy přepočtu")
        self.assertContains(response, "Kuřecí na paprice")
        self.assertEqual(response.context["recent_logs"][0], log)
        self.assertEqual(response.context["biggest_changes"][0].price_diff, Decimal("13.00"))
        self.assertTrue(response.context["consistency_checks"][0]["matches"])

    def test_dashboard_filters_selected_period(self):
        old_log = PriceRecalculationLog.objects.create(
            created_by=self.admin_user,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 1, 31),
            orders_affected=1,
            items_affected=1,
            total_price_diff=Decimal("10.00"),
            note="Historický běh",
        )
        old_log.created_at = timezone.now() - timedelta(days=200)
        old_log.save(update_fields=["created_at"])

        fresh_log = PriceRecalculationLog.objects.create(
            created_by=self.admin_user,
            date_from=date(2026, 4, 1),
            date_to=date(2026, 4, 21),
            orders_affected=2,
            items_affected=4,
            total_price_diff=Decimal("50.00"),
            note="Aktuální běh",
        )

        response = self.client.get(reverse("admin:prepocty_prepoctydummy_changelist"), {"obdobi": "30"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["recent_logs"]), [fresh_log])
        self.assertEqual(response.context["selected_preset"], "30")

    def test_run_route_redirects_to_existing_recalculation_form(self):
        response = self.client.get(reverse("admin:prepocty_run_recalculation"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:objednavky_order_price_recalculation"))

    def test_history_route_redirects_to_existing_log_admin(self):
        response = self.client.get(reverse("admin:prepocty_history"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:objednavky_pricerecalculationlog_changelist"))

    def test_details_route_redirects_to_existing_detail_admin(self):
        response = self.client.get(reverse("admin:prepocty_details"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:objednavky_pricerecalculationdetail_changelist"))
