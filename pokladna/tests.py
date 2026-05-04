from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.utils.timezone import timedelta

from dotace.models import SkupinoveNastaveni
from sklad.models import PohybSkladu, StavSkladu, Surovina
from users.models import Vklad

from .models import DPHSkupina, PLUKategorie, PLUPolozka, Pokladna, PokladniDoklad, PokladniSmazanaPolozka
from .services import (
    pridej_polozku,
    potvrdit_qr_platbu,
    qr_payload_data_uri,
    smaz_polozku,
    stornuj_doklad,
    uzavri_denni_uzaverku,
    uzavri_doklad,
    vytvor_doklad,
    vytvor_vklad_konta,
    zahaj_qr_platbu,
    zrus_rozpracovany_doklad,
)


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

    def test_vytvor_doklad_nastavi_typ_prodej(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)

        self.assertEqual(doklad.typ_dokladu, PokladniDoklad.TYP_PRODEJ)

    def test_uzavreni_kontem_vytvori_cerpani_a_skladovy_vydej(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha, zakaznik=self.zakaznik)
        pridej_polozku(doklad, self.plu, Decimal("2"))

        uzavreny = uzavri_doklad(doklad, PokladniDoklad.PLATBA_KONTO, user=self.obsluha)

        self.assertEqual(uzavreny.stav, PokladniDoklad.STAV_UZAVRENO)
        self.assertEqual(uzavreny.celkem_s_dph, Decimal("50.00"))
        self.assertIsNotNone(uzavreny.konto_pohyb_id)
        self.assertEqual(uzavreny.konto_pohyb.castka, Decimal("-50.00"))
        self.assertEqual(uzavreny.konto_pohyb.status, Vklad.STATUS_PLATBA_UCTU)
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

    def test_vklad_konta_vytvori_pokladni_doklad_o_platbe(self):
        vklad, doklad = vytvor_vklad_konta(
            self.pokladna,
            self.zakaznik,
            Decimal("300.00"),
            Vklad.ZPUSOB_KARTA,
            self.obsluha,
            "Test vklad kartou",
        )

        self.assertEqual(vklad.zpusob_uhrady, Vklad.ZPUSOB_KARTA)
        self.assertEqual(vklad.pokladna, self.pokladna)
        self.assertEqual(doklad.typ_dokladu, PokladniDoklad.TYP_VKLAD_KONTA)
        self.assertEqual(doklad.zpusob_platby, PokladniDoklad.PLATBA_KARTA)
        self.assertEqual(doklad.konto_pohyb, vklad)
        self.assertEqual(doklad.celkem_s_dph, Decimal("300.00"))
        self.assertEqual(doklad.celkem_dph, Decimal("0.00"))
        self.assertEqual(doklad.stav, PokladniDoklad.STAV_UZAVRENO)
        self.assertTrue(doklad.cislo_dokladu.startswith("VKL-"))

    def test_denni_uzaverka_zahrne_vklad_konta_do_plateb(self):
        vytvor_vklad_konta(
            self.pokladna,
            self.zakaznik,
            Decimal("300.00"),
            Vklad.ZPUSOB_KARTA,
            self.obsluha,
            "Test vklad kartou",
        )

        uzaverka = uzavri_denni_uzaverku(
            self.pokladna,
            timezone.localdate(),
            user=self.obsluha,
        )

        self.assertEqual(uzaverka.pocet_dokladu, 1)
        self.assertEqual(uzaverka.karta, Decimal("300.00"))
        self.assertEqual(uzaverka.celkem_trzba, Decimal("300.00"))

    def test_denni_uzaverka_zahrne_potvrzene_qr_platby(self):
        self.pokladna.qr_iban = "CZ6508000000192000145399"
        self.pokladna.save(update_fields=["qr_iban"])

        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("1"))
        cekajici = zahaj_qr_platbu(doklad, user=self.obsluha)
        potvrdit_qr_platbu(cekajici, user=self.obsluha)

        uzaverka = uzavri_denni_uzaverku(self.pokladna, timezone.localdate(), user=self.obsluha)

        self.assertEqual(uzaverka.pocet_dokladu, 1)
        self.assertEqual(uzaverka.qr, Decimal("25.00"))
        self.assertEqual(uzaverka.celkem_trzba, Decimal("25.00"))

    def test_denni_uzaverka_nepovoli_preskocit_starsi_neuzavreny_prodej(self):
        vcera = timezone.now() - timedelta(days=1)
        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("1"))
        uzavreny = uzavri_doklad(doklad, PokladniDoklad.PLATBA_HOTOVOST, user=self.obsluha)
        PokladniDoklad.objects.filter(pk=uzavreny.pk).update(datum=vcera)

        with self.assertRaises(ValidationError):
            uzavri_denni_uzaverku(self.pokladna, timezone.localdate(), user=self.obsluha)

    def test_qr_platba_vyzaduje_iban_pokladny(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("1"))

        with self.assertRaises(ValidationError):
            zahaj_qr_platbu(doklad, user=self.obsluha)

        doklad.refresh_from_db()
        self.assertEqual(doklad.stav, PokladniDoklad.STAV_ROZPRACOVANO)

    def test_qr_platba_nejdrive_ceka_a_neodepise_sklad(self):
        self.pokladna.qr_iban = "CZ6508000000192000145399"
        self.pokladna.qr_prijemce = "Test Jidelna"
        self.pokladna.save(update_fields=["qr_iban", "qr_prijemce"])

        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("2"))

        cekajici = uzavri_doklad(doklad, PokladniDoklad.PLATBA_QR, user=self.obsluha)

        self.assertEqual(cekajici.stav, PokladniDoklad.STAV_CEKA_NA_QR)
        self.assertEqual(cekajici.zpusob_platby, PokladniDoklad.PLATBA_QR)
        self.assertIn("SPD*1.0", cekajici.qr_payload)
        self.assertIn("AM:50.00", cekajici.qr_payload)
        self.assertIn("ACC:CZ6508000000192000145399", cekajici.qr_payload)

        stav = StavSkladu.objects.get(surovina=self.surovina)
        self.assertEqual(stav.mnozstvi, Decimal("10.000"))
        self.assertEqual(PohybSkladu.objects.filter(surovina=self.surovina).count(), 0)

    def test_potvrzeni_qr_platby_uzavre_doklad_a_odepise_sklad(self):
        self.pokladna.qr_iban = "CZ6508000000192000145399"
        self.pokladna.save(update_fields=["qr_iban"])

        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("3"))
        cekajici = zahaj_qr_platbu(doklad, user=self.obsluha)

        uzavreny = potvrdit_qr_platbu(cekajici, user=self.obsluha)

        self.assertEqual(uzavreny.stav, PokladniDoklad.STAV_UZAVRENO)
        self.assertIsNotNone(uzavreny.qr_potvrzen_at)
        self.assertEqual(uzavreny.qr_potvrdil, self.obsluha)
        stav = StavSkladu.objects.get(surovina=self.surovina)
        self.assertEqual(stav.mnozstvi, Decimal("7.000"))
        self.assertEqual(PohybSkladu.objects.filter(surovina=self.surovina, typ=PohybSkladu.TYP_VYDEJ).count(), 1)

    def test_qr_obrazek_lze_vygenerovat_bez_externi_sluzby(self):
        data_uri = qr_payload_data_uri("SPD*1.0*ACC:CZ6508000000192000145399*AM:25.00*CC:CZK")
        self.assertTrue(data_uri.startswith("data:image/png;base64,"))

    def test_smazana_polozka_zustane_v_auditu_uctu(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        polozka = pridej_polozku(doklad, self.plu, Decimal("2"))

        smaz_polozku(doklad, polozka.id, user=self.obsluha, duvod="Test smazání")

        self.assertFalse(doklad.polozky.exists())
        audit = PokladniSmazanaPolozka.objects.get(doklad=doklad)
        self.assertEqual(audit.nazev_snapshot, "Bageta šunková")
        self.assertEqual(audit.mnozstvi, Decimal("2.000"))
        self.assertEqual(audit.castka_celkem, Decimal("50.00"))
        self.assertEqual(audit.smazal, self.obsluha)

    def test_zruseni_rozpracovaneho_uctu_zachova_polozky_a_neodepise_sklad(self):
        doklad = vytvor_doklad(self.pokladna, self.obsluha)
        pridej_polozku(doklad, self.plu, Decimal("1"))

        zruseny = zrus_rozpracovany_doklad(doklad, user=self.obsluha)

        self.assertEqual(zruseny.stav, PokladniDoklad.STAV_STORNOVANO)
        self.assertEqual(zruseny.polozky.count(), 1)
        self.assertEqual(zruseny.stornoval, self.obsluha)
        stav = StavSkladu.objects.get(surovina=self.surovina)
        self.assertEqual(stav.mnozstvi, Decimal("10.000"))
        self.assertEqual(PohybSkladu.objects.filter(surovina=self.surovina).count(), 0)

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
