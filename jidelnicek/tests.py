from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from .services import build_day_menu_context


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
