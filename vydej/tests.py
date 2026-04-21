from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from canteen_settings.models import OrderClosingTime
from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from vydej.models import VydejniUctenka
from vydej.services import (
    build_issue_board,
    build_kitchen_overview,
    cancel_receipt_and_order,
    issue_order_from_admin,
)


class VydejServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.operator = user_model.objects.create_user(
            username="operator",
            password="test12345",
            is_staff=True,
        )
        self.customer = user_model.objects.create_user(
            username="stravnik",
            first_name="Testovací",
            last_name="Strávník",
            password="test12345",
        )
        self.target_date = date(2026, 4, 21)

        OrderClosingTime.objects.create(
            advance_days=1,
            closing_time=time(7, 0),
            cancel_days=0,
            cancel_until_time=time(9, 0),
        )

        self.breakfast_type = DruhJidla.objects.create(nazev="Snídaně", poradi=10)
        self.lunch_type = DruhJidla.objects.create(nazev="Oběd", poradi=20)

        self.breakfast_food = Jidlo.objects.create(
            nazev="Houska, šunka, sýr",
            cena=Decimal("22.00"),
            druh=self.breakfast_type,
        )
        self.lunch_food = Jidlo.objects.create(
            nazev="Hovězí guláš",
            cena=Decimal("115.00"),
            druh=self.lunch_type,
        )
        self.second_lunch_food = Jidlo.objects.create(
            nazev="Krůtí plátek",
            cena=Decimal("120.00"),
            druh=self.lunch_type,
        )

        self.menu = Jidelnicek.objects.create(
            platnost_od=self.target_date,
            platnost_do=self.target_date,
        )
        self.breakfast_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            druh_jidla=self.breakfast_type,
            jidlo=self.breakfast_food,
        )
        self.lunch_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            druh_jidla=self.lunch_type,
            jidlo=self.lunch_food,
        )
        self.second_lunch_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            druh_jidla=self.lunch_type,
            jidlo=self.second_lunch_food,
        )

    def test_issue_order_creates_receipt_and_marks_all_items_issued(self):
        order = Order.objects.create(
            user=self.customer,
            datum_vydeje=self.target_date,
            status="objednano",
        )
        breakfast_order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.breakfast_item,
            quantity=1,
            cena=Decimal("18.00"),
        )
        lunch_order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.lunch_item,
            quantity=2,
            cena=Decimal("99.00"),
        )

        result = issue_order_from_admin(order.id, self.operator)

        order.refresh_from_db()
        breakfast_order_item.refresh_from_db()
        lunch_order_item.refresh_from_db()
        receipt = result["receipt"]

        self.assertEqual(order.status, "vydano")
        self.assertTrue(breakfast_order_item.vydano)
        self.assertTrue(lunch_order_item.vydano)
        self.assertEqual(receipt.vydal, self.operator)
        self.assertEqual(receipt.polozky.count(), 2)
        self.assertEqual(receipt.celkova_cena, Decimal("216.00"))
        self.assertEqual(receipt.celkova_dotace, Decimal("36.00"))

    def test_issue_order_finishes_partial_order_into_existing_receipt(self):
        order = Order.objects.create(
            user=self.customer,
            datum_vydeje=self.target_date,
            status="castecne-vydano",
        )
        already_issued_item = OrderItem.objects.create(
            order=order,
            menu_item=self.breakfast_item,
            quantity=1,
            cena=Decimal("18.00"),
            vydano=True,
        )
        pending_item = OrderItem.objects.create(
            order=order,
            menu_item=self.lunch_item,
            quantity=1,
            cena=Decimal("99.00"),
        )
        receipt = VydejniUctenka.objects.create(
            order=order,
            vydal=self.operator,
            celkova_cena=Decimal("18.00"),
            celkova_dotace=Decimal("4.00"),
        )

        result = issue_order_from_admin(order.id, self.operator)

        order.refresh_from_db()
        pending_item.refresh_from_db()
        receipt.refresh_from_db()

        self.assertTrue(result["already_partial"])
        self.assertEqual(result["receipt"].id, receipt.id)
        self.assertEqual(order.status, "vydano")
        self.assertTrue(pending_item.vydano)
        self.assertEqual(receipt.polozky.count(), 1)
        self.assertEqual(receipt.celkova_cena, Decimal("117.00"))
        self.assertEqual(receipt.celkova_dotace, Decimal("20.00"))
        self.assertTrue(already_issued_item.vydano)

    def test_cancel_receipt_and_order_marks_order_cancelled_and_resets_items(self):
        order = Order.objects.create(
            user=self.customer,
            datum_vydeje=self.target_date,
            status="objednano",
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.lunch_item,
            quantity=1,
            cena=Decimal("99.00"),
        )
        issue_result = issue_order_from_admin(order.id, self.operator)
        receipt = issue_result["receipt"]

        cancelled_order = cancel_receipt_and_order(receipt.id, self.operator)

        order_item.refresh_from_db()
        cancelled_order.refresh_from_db()

        self.assertEqual(cancelled_order.status, "zruseno-obsluhou")
        self.assertEqual(cancelled_order.storno_user, self.operator)
        self.assertFalse(order_item.vydano)
        self.assertIsNone(order_item.datum_vydani)
        self.assertFalse(VydejniUctenka.objects.filter(pk=receipt.id).exists())

    def test_build_kitchen_overview_counts_only_pending_items(self):
        second_customer = get_user_model().objects.create_user(
            username="stravnik2",
            first_name="Druhý",
            last_name="Strávník",
            password="test12345",
        )
        pending_order = Order.objects.create(
            user=self.customer,
            datum_vydeje=self.target_date,
            status="objednano",
        )
        partial_order = Order.objects.create(
            user=second_customer,
            datum_vydeje=self.target_date,
            status="castecne-vydano",
        )
        OrderItem.objects.create(
            order=pending_order,
            menu_item=self.lunch_item,
            quantity=2,
            cena=Decimal("99.00"),
        )
        OrderItem.objects.create(
            order=partial_order,
            menu_item=self.breakfast_item,
            quantity=1,
            cena=Decimal("18.00"),
            vydano=True,
        )
        OrderItem.objects.create(
            order=partial_order,
            menu_item=self.second_lunch_item,
            quantity=1,
            cena=Decimal("104.00"),
        )

        overview = build_kitchen_overview(self.target_date)

        self.assertEqual(overview["total_objednavek"], 2)
        self.assertEqual(overview["total_porci"], 3)
        self.assertEqual(overview["stats"]["Oběd"]["Hovězí guláš"]["celkem"], 2)
        self.assertEqual(overview["stats"]["Oběd"]["Krůtí plátek"]["celkem"], 1)
        self.assertNotIn("Snídaně", overview["stats"])

    def test_build_issue_board_returns_upcoming_day_summary(self):
        order = Order.objects.create(
            user=self.customer,
            datum_vydeje=self.target_date,
            status="objednano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.lunch_item,
            quantity=2,
            cena=Decimal("99.00"),
        )

        board = build_issue_board(start_date=self.target_date, days_ahead=3)

        self.assertEqual(board["total_dni_s_vydejem"], 1)
        self.assertEqual(board["total_objednavek"], 1)
        self.assertEqual(board["total_kusu"], 2)
        self.assertEqual(board["stats_vsechny_dny"][0]["datum"], self.target_date)
        self.assertEqual(
            board["stats_vsechny_dny"][0]["druhy"]["Oběd"]["Hovězí guláš"]["celkem"],
            2,
        )
