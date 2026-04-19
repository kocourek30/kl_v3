from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem

from .models import AnketniOtazka, HodnoceniJidla


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
