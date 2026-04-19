from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from dotace.models import SkupinoveNastaveni
from sklad.models import PohybSkladu, StavSkladu, Surovina
from users.models import Vklad

from .models import DPHSkupina, PLUKategorie, PLUPolozka, Pokladna, PokladniDoklad
from .services import pridej_polozku, stornuj_doklad, uzavri_denni_uzaverku, uzavri_doklad, vytvor_doklad


class PokladnaServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.obsluha = User.objects.create_user(username="pokladni", password="x")
        self.zakaznik = User.objects.create_user(username="student", password="x")
        Vklad.objects.create(uzivatel=self.zakaznik, castka=Decimal("100.00"), poznamka="Test vklad")

        self.pokladna = Pokladna.objects.create(nazev="Test pokladna")
        self.dph = DPHSkupina.objects.create(nazev="Jídlo", sazba=Decimal("12.00"))
        self.kategorie = PLUKategorie.objects.create(nazev="Bufet")
        self.surovina = Surovina.objects.create(nazev="Bageta", jednotka=Surovina.JEDNOTKA_KS)
        StavSkladu.objects.create(surovina=self.surovina, mnozstvi=Decimal("10.000"))
        self.plu = PLUPolozka.objects.create(
            nazev="Bageta šunková",
            cena=Decimal("25.00"),
            dph_skupina=self.dph,
            kategorie=self.kategorie,
            typ=PLUPolozka.TYP_RECEPTURA,
            surovina=self.surovina,
        )

    def test_uzavreni_kontem_vytvori_cerpani_a_skladovy_vydej(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)
        pridej_polozku(doklad, self.plu, Decimal("2"))

        uzavreny = uzavri_doklad(doklad, PokladniDoklad.PLATBA_KONTO, user=self.obsluha)

        self.assertEqual(uzavreny.stav, PokladniDoklad.STAV_UZAVRENO)
        self.assertEqual(uzavreny.celkem_s_dph, Decimal("50.00"))
        self.assertIsNotNone(uzavreny.konto_pohyb_id)
        self.assertEqual(self.zakaznik.aktualni_zustatek, Decimal("50.00"))

        stav = StavSkladu.objects.get(surovina=self.surovina)
        self.assertEqual(stav.mnozstvi, Decimal("8.000"))
        self.assertEqual(PohybSkladu.objects.filter(surovina=self.surovina, typ=PohybSkladu.TYP_VYDEJ).count(), 1)

    def test_konto_nepusti_prodej_bez_dostatecneho_zustatku(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)
        pridej_polozku(doklad, self.plu, Decimal("5"))

        with self.assertRaises(ValidationError):
            uzavri_doklad(doklad, PokladniDoklad.PLATBA_KONTO, user=self.obsluha)

        doklad.refresh_from_db()
        self.assertEqual(doklad.stav, PokladniDoklad.STAV_ROZPRACOVANO)
        self.assertEqual(self.zakaznik.aktualni_zustatek, Decimal("100.00"))

    def test_konto_povoli_debet_dle_skupinoveho_limitu(self):
        skupina = Group.objects.create(name="DM15+")
        SkupinoveNastaveni.objects.create(
            skupina=skupina,
            cerpani_debit=True,
            debit_limit=Decimal("-200.00"),
        )
        self.zakaznik.groups.add(skupina)

        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)
        pridej_polozku(doklad, self.plu, Decimal("7"))  # 175 Kč

        uzavreny = uzavri_doklad(doklad, PokladniDoklad.PLATBA_KONTO, user=self.obsluha)

        self.assertEqual(uzavreny.stav, PokladniDoklad.STAV_UZAVRENO)
        self.assertEqual(self.zakaznik.aktualni_zustatek, Decimal("-75.00"))

    def test_storno_vrati_konto_i_sklad(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)
        pridej_polozku(doklad, self.plu, Decimal("1"))
        uzavreny = uzavri_doklad(doklad, PokladniDoklad.PLATBA_KONTO, user=self.obsluha)

        stornovany = stornuj_doklad(uzavreny, user=self.obsluha, duvod="Test storno")

        self.assertEqual(stornovany.stav, PokladniDoklad.STAV_STORNOVANO)
        self.assertEqual(self.zakaznik.aktualni_zustatek, Decimal("100.00"))
        stav = StavSkladu.objects.get(surovina=self.surovina)
        self.assertEqual(stav.mnozstvi, Decimal("10.000"))
        self.assertEqual(PohybSkladu.objects.filter(surovina=self.surovina, typ=PohybSkladu.TYP_PRIJEM).count(), 1)

    def test_denni_uzaverka_secte_platby_a_priradi_doklady(self):
        hotovost = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(hotovost, self.plu, Decimal("1"))
        uzavri_doklad(hotovost, PokladniDoklad.PLATBA_HOTOVOST, user=self.obsluha)

        karta = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(karta, self.plu, Decimal("2"))
        uzavri_doklad(karta, PokladniDoklad.PLATBA_KARTA, user=self.obsluha)

        uzaverka = uzavri_denni_uzaverku(
            self.pokladna,
            timezone.localdate(),
            user=self.obsluha,
            hotovost_spoctena=Decimal("25.00"),
        )

        self.assertEqual(uzaverka.pocet_dokladu, 2)
        self.assertEqual(uzaverka.hotovost, Decimal("25.00"))
        self.assertEqual(uzaverka.karta, Decimal("50.00"))
        self.assertEqual(uzaverka.konto, Decimal("0.00"))
        self.assertEqual(uzaverka.rozdil_hotovosti, Decimal("0.00"))
        self.assertEqual(PokladniDoklad.objects.filter(uzaverka=uzaverka).count(), 2)

    def test_plu_validace_pro_vazene_zbozi(self):
        plu = PLUPolozka(
            nazev="Salatovy mix",
            cena=Decimal("10.00"),
            dph_skupina=self.dph,
            kategorie=self.kategorie,
            typ=PLUPolozka.TYP_VAZENE,
            surovina=None,
        )
        with self.assertRaises(ValidationError):
            plu.full_clean()
