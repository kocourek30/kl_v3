from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku

from .forms import ObjednavkaForm
from .models import Order, OrderItem
from .services import (
    apply_bulk_order_plan,
    build_bulk_order_plan,
    recalculate_order_prices,
    validate_order_quantity,
)


class OrderQuantityValidationTests(TestCase):
    def test_valid_quantity_is_normalized_to_integer(self):
        self.assertEqual(validate_order_quantity("3"), 3)

    def test_quantity_musi_byt_kladna(self):
        with self.assertRaises(ValidationError):
            validate_order_quantity("0")

    def test_quantity_ma_horni_limit(self):
        with self.assertRaises(ValidationError):
            validate_order_quantity("11")

    def test_quantity_musi_byt_cislo(self):
        with self.assertRaises(ValidationError):
            validate_order_quantity("abc")


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


class BulkOrderPlanTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bulk-user",
            password="test",
            first_name="Bulk",
            last_name="User",
        )
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(
            nazev="Těstoviny",
            cena=Decimal("75.00"),
            druh=self.druh,
        )
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 22),
            platnost_do=date(2026, 4, 22),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )

    def test_bulk_plan_marks_new_order_as_create(self):
        plan = build_bulk_order_plan(date(2026, 4, 22), [self.menu_item], [self.user])

        self.assertEqual(plan["summary"]["create"], 1)
        self.assertEqual(plan["entries"][0]["action"], "create")

    def test_bulk_plan_skips_non_editable_existing_order(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 22),
            status="vydano",
        )
        OrderItem.objects.create(order=order, menu_item=self.menu_item, quantity=1, cena=Decimal("75.00"))

        plan = build_bulk_order_plan(date(2026, 4, 22), [self.menu_item], [self.user])

        self.assertEqual(plan["summary"]["skip_status"], 1)
        self.assertEqual(plan["entries"][0]["action"], "skip_status")

    def test_bulk_apply_replaces_existing_draft_order(self):
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 22),
            status="objednano",
        )
        old_jidlo = Jidlo.objects.create(nazev="Staré jídlo", cena=Decimal("10.00"), druh=self.druh)
        old_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=old_jidlo,
        )
        OrderItem.objects.create(order=order, menu_item=old_item, quantity=1, cena=Decimal("10.00"))

        plan = build_bulk_order_plan(date(2026, 4, 22), [self.menu_item], [self.user])
        result = apply_bulk_order_plan(date(2026, 4, 22), [self.menu_item], plan["entries"])

        order.refresh_from_db()
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["replaced"], 1)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().menu_item, self.menu_item)
