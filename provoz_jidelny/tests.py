from datetime import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from canteen_settings.models import MealPickupTime, OrderClosingTime
from jidelnicek.models import DruhJidla
from objednavky.models import Order
from vydej.models import VydejSettings


User = get_user_model()


class ProvozJidelnyDashboardTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="provoz-admin",
            password="testpass123",
            email="provoz@example.com",
        )
        self.staff = User.objects.create_user(
            username="obsluha",
            password="testpass123",
            email="obsluha@example.com",
            is_staff=True,
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd", poradi=1, ikona="obrazek")
        MealPickupTime.objects.create(druh_jidla=self.druh, pickup_from=time(10, 30), pickup_to=time(13, 0))
        OrderClosingTime.objects.create(
            druh_jidla=self.druh,
            advance_days=1,
            closing_time=time(7, 0),
            cancel_days=1,
            cancel_until_time=time(7, 0),
            je_aktivni=True,
        )
        VydejSettings.objects.create(timeout_seconds=25)
        Order.objects.create(user=self.superuser, datum_vydeje=timezone.localdate(), status="objednano")

    def test_dashboard_change_list_renders(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:provoz_jidelny_provoznidashboard_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provozní dashboard pro obsluhu")
        self.assertContains(response, "Živý výdej")
        self.assertContains(response, "Přehled pro kuchyni")
        self.assertNotContains(response, "Admin přehled")

    def test_admin_index_redirects_staff_to_canteen_dashboard(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:provoz_jidelny_provoznidashboard_changelist"), response["Location"])

