from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from canteen_settings.models import MealPickupTime
from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from sklad.models import PohybSkladu
from vydej.models import PolozkaUctenky, VydejniUctenka

from .services import vydat_objednavku


class VydejBezSkladovehoOdpisuTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.student = user_model.objects.create_user(
            username="student-vydej",
            password="test",
            first_name="Test",
            last_name="Student",
        )
        self.obsluha = user_model.objects.create_user(
            username="obsluha-vydej",
            password="test",
            is_staff=True,
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        MealPickupTime.objects.create(
            druh_jidla=self.druh,
            pickup_from=time(0, 0),
            pickup_to=time(23, 59),
        )
        self.jidlo = Jidlo.objects.create(
            nazev="Testovací oběd",
            cena=Decimal("85.00"),
            druh=self.druh,
        )
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date.today(),
            platnost_do=date.today(),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )
        self.order = Order.objects.create(
            user=self.student,
            datum_vydeje=date.today(),
            status="objednano",
        )
        self.item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.menu_item,
            quantity=2,
            cena=Decimal("70.00"),
        )

    def test_vydej_objednavky_nevytvari_skladovy_pohyb(self):
        result = vydat_objednavku(self.order.pk, self.obsluha)

        self.order.refresh_from_db()
        self.item.refresh_from_db()

        self.assertEqual(self.order.status, "vydano")
        self.assertTrue(self.item.vydano)
        self.assertEqual(PohybSkladu.objects.count(), 0)
        self.assertEqual(VydejniUctenka.objects.count(), 1)
        self.assertEqual(PolozkaUctenky.objects.count(), 1)
        self.assertEqual(result["uctenka"].celkova_cena, Decimal("140.00"))


class RfidTokenTests(TestCase):
    @override_settings(RFID_API_TOKEN="tajny-token")
    def test_rfid_scan_odmitne_chybny_token(self):
        response = self.client.post(
            reverse("vydej_frontend:rfid_scan"),
            data={"rfid_tag": "123456", "token": "spatne"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["success"])
