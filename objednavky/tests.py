from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku

from .forms import ObjednavkaForm
from .models import Order, OrderItem
from .services import recalculate_order_prices


class ObjednavkyCleanupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="student01",
            password="test",
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(
            nazev="Kuře s rýží",
            cena=Decimal("82.50"),
            druh=self.druh,
        )
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 20),
            platnost_do=date(2026, 4, 20),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )

    def test_order_totals_work_without_prefetch(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 20),
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=2,
            cena=Decimal("82.50"),
        )

        self.assertEqual(order.total_quantity(), 2)
        self.assertEqual(order.total_price(), Decimal("165"))

    def test_legacy_named_form_uses_current_order_models(self):
        form = ObjednavkaForm(
            data={
                "user": self.user.pk,
                "datum_vydeje": "2026-04-20",
                "status": "objednano",
                "menu_items": [self.menu_item.pk],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        order = form.save()

        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().cena, Decimal("82.50"))

    def test_price_recalculation_preview_uses_prefetched_order_items(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 20),
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=1,
            cena=Decimal("80.00"),
        )

        result = recalculate_order_prices(
            date(2026, 4, 20),
            date(2026, 4, 20),
            self.user,
            dry_run=True,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["items_changed"], 1)
        self.assertEqual(result["total_price_diff"], Decimal("2.50"))
