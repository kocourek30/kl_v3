from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem

from .models import DotaceProJidelniskouSkupinu, DotacniPolitika
from .services import vypocet_dotovane_ceny


class DotacniPravidlaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="student-dotace", password="x")
        self.group = Group.objects.create(name="Studenti")
        self.user.groups.add(self.group)
        self.druh = DruhJidla.objects.create(nazev="Oběd")
        self.jidlo = Jidlo.objects.create(
            nazev="Kuře s rýží",
            cena=Decimal("100.00"),
            druh=self.druh,
        )
        self.jidelnicek = Jidelnicek.objects.create(
            platnost_od=date(2026, 4, 1),
            platnost_do=date(2026, 4, 30),
        )
        self.menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=self.jidlo,
        )

    def test_denni_pocet_dotovanych_porci_omezi_cenu(self):
        policy = DotacniPolitika.objects.create(
            skupina=self.group,
            castka=Decimal("40.00"),
            denni_limit=1,
        )

        prvni_cena = vypocet_dotovane_ceny(
            self.user,
            self.menu_item,
            target_date=date(2026, 4, 20),
            quantity=1,
        )
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 20),
            status="objednano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=1,
            cena=prvni_cena,
        )
        druhe_jidlo = Jidlo.objects.create(
            nazev="Rizoto",
            cena=Decimal("100.00"),
            druh=self.druh,
        )
        druha_polozka = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=self.druh,
            jidlo=druhe_jidlo,
        )
        druha_cena = vypocet_dotovane_ceny(
            self.user,
            druha_polozka,
            target_date=date(2026, 4, 20),
            quantity=1,
        )

        self.assertEqual(policy.denni_limit, 1)
        self.assertEqual(prvni_cena, Decimal("60.00"))
        self.assertEqual(druha_cena, Decimal("100.00"))

    def test_mesicni_financni_limit_omezi_dotaci(self):
        DotacniPolitika.objects.create(
            skupina=self.group,
            castka=Decimal("40.00"),
            mesicni_limit_castka=Decimal("50.00"),
        )
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 10),
            status="objednano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=1,
            cena=Decimal("60.00"),
        )

        cena = vypocet_dotovane_ceny(
            self.user,
            self.menu_item,
            target_date=date(2026, 4, 20),
            quantity=1,
        )

        self.assertEqual(cena, Decimal("100.00"))

    def test_limity_nula_znamenaji_bez_omezeni(self):
        DotacniPolitika.objects.create(
            skupina=self.group,
            procento=Decimal("50.00"),
        )

        cena = vypocet_dotovane_ceny(
            self.user,
            self.menu_item,
            target_date=date(2026, 4, 20),
            quantity=3,
        )

        self.assertEqual(cena, Decimal("50.00"))

    def test_pocetni_limity_lze_nastavit_podle_druhu_jidla(self):
        polevka = DruhJidla.objects.create(nazev="Polévka")
        jidlo_polevka = Jidlo.objects.create(
            nazev="Bramboračka",
            cena=Decimal("50.00"),
            druh=polevka,
        )
        menu_polevka = PolozkaJidelnicku.objects.create(
            jidelnicek=self.jidelnicek,
            druh_jidla=polevka,
            jidlo=jidlo_polevka,
        )
        policy = DotacniPolitika.objects.create(
            skupina=self.group,
            castka=Decimal("20.00"),
        )
        DotaceProJidelniskouSkupinu.objects.create(
            dotacni_politika=policy,
            jidelniskova_skupina=self.druh,
            denni_limit=1,
        )
        DotaceProJidelniskouSkupinu.objects.create(
            dotacni_politika=policy,
            jidelniskova_skupina=polevka,
            denni_limit=1,
        )
        order = Order.objects.create(
            user=self.user,
            datum_vydeje=date(2026, 4, 20),
            status="objednano",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.menu_item,
            quantity=1,
            cena=Decimal("80.00"),
        )

        cena_druhy_obed = vypocet_dotovane_ceny(
            self.user,
            self.menu_item,
            target_date=date(2026, 4, 20),
            quantity=1,
        )
        cena_polevka = vypocet_dotovane_ceny(
            self.user,
            menu_polevka,
            target_date=date(2026, 4, 20),
            quantity=1,
        )

        self.assertEqual(cena_druhy_obed, Decimal("100.00"))
        self.assertEqual(cena_polevka, Decimal("30.00"))
