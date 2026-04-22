from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem

from .models import AnketniOtazka, HodnoceniJidla, OdpovedHodnoceni
from .services import anketni_report_obdobi


class AnketyViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="student",
            password="test-pass-123",
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(nazev="Testovací oběd", cena=80)
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=timezone.localdate(),
            platnost_do=timezone.localdate(),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )
        self.order = Order.objects.create(
            user=self.user,
            datum_vydeje=timezone.localdate(),
            status="objednano",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.menu_item,
            quantity=1,
            cena=80,
        )
        self.otazka = AnketniOtazka.objects.create(text="Chuť?", poradi=1)
        self.client.login(username="student", password="test-pass-123")

    def test_nevydane_jidlo_nelze_hodnotit(self):
        response = self.client.get(reverse("ankety:hodnotit_jidlo", args=[self.order_item.id]))

        self.assertEqual(response.status_code, 404)

    def test_vydane_jidlo_lze_ohodnotit_jednou(self):
        self.order_item.vydano = True
        self.order_item.datum_vydani = timezone.now()
        self.order_item.save(update_fields=["vydano", "datum_vydani"])

        payload = {
            f"otazka_{otazka.id}": "5"
            for otazka in AnketniOtazka.objects.filter(aktivni=True)
        }
        payload["poznamka"] = "Výborné."
        response = self.client.post(reverse("ankety:hodnotit_jidlo", args=[self.order_item.id]), payload)

        self.assertRedirects(response, reverse("ankety:moje_ankety"))
        self.assertEqual(HodnoceniJidla.objects.count(), 1)

        response = self.client.get(reverse("ankety:hodnotit_jidlo", args=[self.order_item.id]))

        self.assertRedirects(response, reverse("ankety:moje_ankety"))


class AnketyReportTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-ankety",
            email="admin@example.com",
            password="admin-pass-123",
        )
        self.user = get_user_model().objects.create_user(
            username="report-user",
            password="test-pass-123",
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(nazev="Reportovací oběd", cena=95)
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=timezone.localdate(),
            platnost_do=timezone.localdate(),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )
        self.order = Order.objects.create(
            user=self.user,
            datum_vydeje=timezone.localdate(),
            status="vydano",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.menu_item,
            quantity=1,
            cena=95,
            vydano=True,
            datum_vydani=timezone.now(),
        )

    def test_report_counts_only_really_rated_foods(self):
        report = anketni_report_obdobi(timezone.localdate(), timezone.localdate())

        self.assertEqual(report["hodnoceni_count"], 0)
        self.assertEqual(report["jidla_count"], 0)
        self.assertEqual(len(report["nejlepsi"]), 0)
        self.assertEqual(len(report["nejslabsi"]), 0)

    def test_admin_report_defaults_to_yesterday_period(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("admin:ankety_report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["typ"], "yesterday")
        self.assertEqual(response.context["date_from"], timezone.localdate() - timedelta(days=1))
        self.assertEqual(response.context["date_to"], timezone.localdate() - timedelta(days=1))

    def test_report_builds_grouped_sections_by_meal_type(self):
        DruhJidla.objects.create(nazev="Snídaně", poradi=5)
        hodnoceni = HodnoceniJidla.objects.create(
            user=self.user,
            order_item=self.order_item,
            datum_vydeje=timezone.localdate(),
            jidlo_nazev=self.jidlo.nazev,
        )
        otazka = AnketniOtazka.objects.create(text="Chuť", poradi=1)
        OdpovedHodnoceni.objects.create(hodnoceni_jidla=hodnoceni, otazka=otazka, znamka=5)

        report = anketni_report_obdobi(timezone.localdate(), timezone.localdate())

        self.assertEqual(len(report["nejlepsi_podle_druhu"]), 2)
        groups = {group["druh_jidla"]: group for group in report["nejlepsi_podle_druhu"]}
        self.assertIn("Oběd", groups)
        self.assertIn("Snídaně", groups)
        self.assertEqual(groups["Oběd"]["rows"][0]["jidlo"], self.jidlo.nazev)
        self.assertEqual(groups["Snídaně"]["count"], 0)
