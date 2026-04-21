from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin import JidloAdmin, PolozkaJidelnickuAdminForm
from .models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from .services import (
    build_day_menu_context,
    can_user_access_menu_item,
    check_group_limit,
    get_group_order_limit,
)
from users.models import StravovaciSkupina
from canteen_settings.models import GroupOrderLimit


class DruhJidlaOrderingTests(TestCase):
    def test_denni_jidelnicek_respektuje_poradi_druhu_jidel(self):
        user = get_user_model().objects.create_user(
            username="student",
            password="test",
        )
        hlavni_chod = DruhJidla.objects.create(nazev="Hlavní chod", poradi=20)
        polevka = DruhJidla.objects.create(nazev="Polévka", poradi=10)
        dezert = DruhJidla.objects.create(nazev="Dezert", poradi=30)

        menu = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 20),
            platnost_do=date(2026, 4, 20),
        )

        for druh in (hlavni_chod, dezert, polevka):
            jidlo = Jidlo.objects.create(
                nazev=f"Jídlo {druh.nazev}",
                cena=10,
                druh=druh,
            )
            PolozkaJidelnicku.objects.create(
                jidelnicek=menu,
                druh_jidla=druh,
                jidlo=jidlo,
            )

        context = build_day_menu_context(user, date(2026, 4, 20))

        self.assertEqual(
            list(context["menu_items_grouped"].keys()),
            [polevka, hlavni_chod, dezert],
        )


class JidloVisualFallbackTests(TestCase):
    def test_jidlo_bez_fotky_ma_vychozi_ikonu_podle_nazvu(self):
        druh = DruhJidla.objects.create(nazev="Hlavní chod", poradi=20)
        jidlo = Jidlo.objects.create(
            nazev="Kuře na paprice s těstovinami",
            cena=10,
            druh=druh,
        )

        self.assertEqual(jidlo.vychozi_ikona, "fa-solid fa-drumstick-bite")
        self.assertFalse(jidlo.ma_fotku)

    def test_druh_jidla_ma_vychozi_ikonu_podle_nazvu(self):
        druh = DruhJidla.objects.create(nazev="Polévka", poradi=10)

        self.assertEqual(druh.vychozi_ikona, "fa-solid fa-bowl-food")


class JidelnicekMealKindSyncTests(TestCase):
    def setUp(self):
        self.snidane = DruhJidla.objects.create(nazev="Snídaně", poradi=10)
        self.obed = DruhJidla.objects.create(nazev="Oběd", poradi=20)
        self.menu = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 21),
            platnost_do=date(2026, 4, 21),
        )

    def test_menu_item_accepts_matching_food_kind_for_slot(self):
        jidlo = Jidlo.objects.create(
            nazev="Těstovinový salát",
            cena=45,
            druh=self.snidane,
        )
        item = PolozkaJidelnicku(
            jidelnicek=self.menu,
            jidlo=jidlo,
            druh_jidla=self.snidane,
        )

        item.full_clean()
        item.save()

        self.assertEqual(item.druh_jidla, self.snidane)

    def test_menu_item_rejects_food_with_different_kind_than_slot(self):
        jidlo = Jidlo.objects.create(
            nazev="Těstovinový salát",
            cena=45,
            druh=self.snidane,
        )
        item = PolozkaJidelnicku(
            jidelnicek=self.menu,
            jidlo=jidlo,
            druh_jidla=self.obed,
        )

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_menu_item_requires_food_kind(self):
        jidlo = Jidlo.objects.create(
            nazev="Nezařazené jídlo",
            cena=45,
            druh=None,
        )
        item = PolozkaJidelnicku(
            jidelnicek=self.menu,
            jidlo=jidlo,
            druh_jidla=self.obed,
        )

        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_changing_food_kind_updates_existing_menu_items(self):
        jidlo = Jidlo.objects.create(
            nazev="Kuřecí plátek",
            cena=89,
            druh=self.snidane,
        )
        item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            jidlo=jidlo,
            druh_jidla=self.snidane,
        )

        jidlo.druh = self.obed
        jidlo.full_clean()
        jidlo.save()
        item.refresh_from_db()

        self.assertEqual(item.druh_jidla, self.obed)

    def test_food_used_in_menu_cannot_lose_kind(self):
        jidlo = Jidlo.objects.create(
            nazev="Rizoto",
            cena=79,
            druh=self.obed,
        )
        PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            jidlo=jidlo,
            druh_jidla=self.obed,
        )

        jidlo.druh = None

        with self.assertRaises(ValidationError):
            jidlo.full_clean()


class JidloAdminAutocompleteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = JidloAdmin(Jidlo, AdminSite())
        self.snidane = DruhJidla.objects.create(nazev="Snídaně", poradi=10)
        self.obed = DruhJidla.objects.create(nazev="Oběd", poradi=20)
        self.snidane_jidlo = Jidlo.objects.create(
            nazev="Míchaná vejce",
            cena=35,
            druh=self.snidane,
        )
        self.obed_jidlo = Jidlo.objects.create(
            nazev="Hovězí guláš",
            cena=99,
            druh=self.obed,
        )

    def test_search_results_can_be_scoped_to_requested_kind(self):
        request = self.factory.get("/admin/autocomplete/", {"druh_jidla": str(self.obed.pk)})
        queryset, _ = self.admin.get_search_results(request, Jidlo.objects.all(), "")

        self.assertEqual(list(queryset), [self.obed_jidlo])

    def test_inline_form_limits_food_choices_to_slot_kind(self):
        form = PolozkaJidelnickuAdminForm(initial={"druh_jidla": self.snidane.pk})

        self.assertEqual(list(form.fields["jidlo"].queryset), [self.snidane_jidlo])


class DruhJidlaVisibilityTests(TestCase):
    def setUp(self):
        self.allowed_group = Group.objects.create(name="Učitelé a personál")
        self.denied_group = Group.objects.create(name="Studenti")

        self.allowed_stravovaci = StravovaciSkupina.objects.create(
            kod="UC",
            nazev="Učitelé",
            django_group=self.allowed_group,
        )
        self.denied_stravovaci = StravovaciSkupina.objects.create(
            kod="ST",
            nazev="Studenti",
            django_group=self.denied_group,
        )

        user_model = get_user_model()
        self.allowed_user = user_model.objects.create_user(
            username="teacher",
            password="test",
            stravovaci_skupina=self.allowed_stravovaci,
        )
        self.denied_user = user_model.objects.create_user(
            username="student",
            password="test",
            stravovaci_skupina=self.denied_stravovaci,
        )

        self.druh = DruhJidla.objects.create(nazev="Večeře", poradi=10)
        self.druh.viditelne_pro_skupiny.add(self.allowed_group)
        self.jidlo = Jidlo.objects.create(
            nazev="Testovací večeře",
            cena=50,
            druh=self.druh,
        )
        self.menu = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 21),
            platnost_do=date(2026, 4, 21),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.menu,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )

    def test_day_menu_uses_stravovaci_skupina_django_group_for_visibility(self):
        self.allowed_user.groups.add(self.allowed_group)

        context = build_day_menu_context(self.allowed_user, date(2026, 4, 21))

        self.assertIn(self.druh, context["menu_items_grouped"])
        self.assertEqual(context["menu_items_grouped"][self.druh][0].jidlo, self.jidlo)

    def test_day_menu_hides_item_for_other_group(self):
        context = build_day_menu_context(self.denied_user, date(2026, 4, 21))

        self.assertNotIn(self.druh, context["menu_items_grouped"])

    def test_order_create_rejects_direct_post_for_hidden_item(self):
        self.client.force_login(self.denied_user)

        response = self.client.post(
            reverse("jidelnicek:order_create"),
            {
                "menu_item_id": self.menu_item.id,
                "menu_date": "2026-04-21",
                "quantity": 1,
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_group_limit_uses_stravovaci_skupina_django_group(self):
        GroupOrderLimit.objects.create(
            group=self.allowed_group,
            druh_jidla=self.druh,
            max_orders_per_day=1,
        )

        limit = get_group_order_limit(self.allowed_user, self.druh)
        can_order, _ = check_group_limit(self.allowed_user, self.menu_item, date(2026, 4, 21), 2)

        self.assertEqual(limit, 1)
        self.assertFalse(can_order)

    def test_stravovaci_skupina_alone_does_not_grant_visibility(self):
        context = build_day_menu_context(self.allowed_user, date(2026, 4, 21))

        self.assertNotIn(self.druh, context["menu_items_grouped"])

    def test_staff_user_without_matching_group_still_respects_visibility(self):
        staff_user = get_user_model().objects.create_user(
            username="staff-user",
            password="test",
            is_staff=True,
        )

        context = build_day_menu_context(staff_user, date(2026, 4, 21))

        self.assertNotIn(self.druh, context["menu_items_grouped"])

    def test_superuser_can_still_see_restricted_item(self):
        superuser = get_user_model().objects.create_superuser(
            username="boss",
            password="test",
            email="boss@example.com",
        )

        self.assertTrue(can_user_access_menu_item(superuser, self.menu_item))
