from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.test import TestCase

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, PolozkaJidelnicku
from objednavky.models import Order, OrderItem
from users.models import StravovaciSkupina

from .models import (
    Inventura,
    JidloKomponenta,
    KomponentaJidla,
    KomponentaSurovina,
    Dodavatel,
    PohybSkladu,
    PolozkaInventury,
    PolozkaPrijmu,
    PolozkaVydejky,
    PrijemSkladu,
    NormaSpotrebnihoKose,
    OdpisExpirace,
    SkladDashboard,
    SarzeSkladu,
    StavSkladu,
    Surovina,
    Vydejka,
)
from .services import (
    generate_vydejka_from_orders,
    najdi_nedostatecne_stavy_pro_vydejku,
    preved_na_gramy,
    priprav_radky_spotrebi_kos_tabulka,
    spocitej_naklady_mesic,
    spocitej_souhrn_spotrebniho_kose,
    spocitej_zapocitatelnou_hmotnost_sk,
    stav_skladu_k_datu,
    uzavri_odpis_expirace,
    stornuj_prijem,
    stornuj_inventuru,
    stornuj_vydejku,
    uzavri_inventuru,
    uzavri_prijem,
    uzavri_vydejku,
)


class SkladovePohybyTests(TestCase):
    def setUp(self):
        self.datum = date(2026, 4, 14)
        self.skupina = StravovaciSkupina.objects.create(
            kod="TEST",
            nazev="Testovaci skupina",
            typ_vzdelavani="SS",
        )
        self.user = get_user_model().objects.create_user(
            username="student",
            password="test",
            stravovaci_skupina=self.skupina,
        )
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin",
            password="test",
            email="admin@example.test",
        )

    def pridej_sarzi(self, surovina, mnozstvi, datum_spotreby=None, sarze="TEST"):
        return SarzeSkladu.objects.create(
            surovina=surovina,
            sarze=sarze,
            typ_data_spotreby="POUZITELNOST",
            datum_spotreby=datum_spotreby or date(2099, 1, 1),
            mnozstvi_prijato=mnozstvi,
            mnozstvi_zbyva=mnozstvi,
            cena_za_jednotku=surovina.prumerna_cena_za_jednotku or Decimal("1.0000"),
            stav=SarzeSkladu.STAV_POUZITELNA,
        )

    def test_vydejka_z_objednavek_odecte_komponentove_suroviny_idempotentne(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g")
        voda = Surovina.objects.create(nazev="Voda", jednotka="ml")
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        StavSkladu.objects.create(surovina=voda, mnozstvi=Decimal("500.000"))
        self.pridej_sarzi(mouka, Decimal("1000.000"), sarze="MOUKA-1")
        self.pridej_sarzi(voda, Decimal("500.000"), sarze="VODA-1")

        komponenta = KomponentaJidla.objects.create(
            nazev="Testovaci omacka",
            typ=KomponentaJidla.TYP_OMACKA,
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=mouka,
            mnozstvi_na_porci=Decimal("100.000"),
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=voda,
            mnozstvi_na_porci=Decimal("50.000"),
        )

        druh = DruhJidla.objects.create(nazev="Obed")
        jidlo = Jidlo.objects.create(nazev="Testovaci jidlo", cena=Decimal("80.00"), druh=druh)
        JidloKomponenta.objects.create(
            jidlo=jidlo,
            komponenta=komponenta,
            mnozstvi_nasobek=Decimal("2.000"),
        )

        jidelnicek = Jidelnicek.objects.create(
            platnost_od=self.datum,
            platnost_do=self.datum,
        )
        polozka_menu = PolozkaJidelnicku.objects.create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        objednavka = Order.objects.create(user=self.user, datum_vydeje=self.datum)
        OrderItem.objects.create(
            order=objednavka,
            menu_item=polozka_menu,
            quantity=3,
            cena=jidlo.cena,
        )

        vydejka, created = generate_vydejka_from_orders(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )

        self.assertTrue(created)
        self.assertEqual(vydejka.polozky.get(surovina=mouka).mnozstvi, Decimal("600.000"))
        self.assertEqual(vydejka.polozky.get(surovina=voda).mnozstvi, Decimal("300.000"))

        self.assertTrue(uzavri_vydejku(vydejka, user=self.user))

        self.assertEqual(StavSkladu.objects.get(surovina=mouka).mnozstvi, Decimal("400.000"))
        self.assertEqual(StavSkladu.objects.get(surovina=voda).mnozstvi, Decimal("200.000"))
        self.assertEqual(PohybSkladu.objects.filter(vydejka=vydejka, typ=PohybSkladu.TYP_VYDEJ).count(), 2)

        self.assertFalse(uzavri_vydejku(vydejka, user=self.user))
        self.assertEqual(PohybSkladu.objects.filter(vydejka=vydejka).count(), 2)

    def test_uzavri_prijem_navysi_stav_vytvori_pohyb_a_spocita_vazenou_cenu(self):
        ryze = Surovina.objects.create(
            nazev="Ryze",
            jednotka="kg",
            prumerna_cena_za_jednotku=Decimal("20.0000"),
        )
        StavSkladu.objects.create(surovina=ryze, mnozstvi=Decimal("10.000"))
        prijem = PrijemSkladu.objects.create(datum=self.datum)
        PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=ryze,
            mnozstvi=Decimal("5.000"),
            jednotkova_cena=Decimal("35.0000"),
        )

        self.assertTrue(uzavri_prijem(prijem, user=self.user))

        ryze.refresh_from_db()
        self.assertEqual(StavSkladu.objects.get(surovina=ryze).mnozstvi, Decimal("15.000"))
        self.assertEqual(ryze.prumerna_cena_za_jednotku, Decimal("25.0000"))

        pohyb = PohybSkladu.objects.get(prijem=prijem)
        self.assertEqual(pohyb.typ, PohybSkladu.TYP_PRIJEM)
        self.assertEqual(pohyb.mnozstvi, Decimal("5.000"))
        self.assertEqual(pohyb.cena_za_jednotku, Decimal("35.0000"))

        self.assertFalse(uzavri_prijem(prijem, user=self.user))
        self.assertEqual(PohybSkladu.objects.filter(prijem=prijem).count(), 1)
        self.assertEqual(StavSkladu.objects.get(surovina=ryze).mnozstvi, Decimal("15.000"))

    def test_polozka_prijmu_spocita_mnozstvi_a_cenu_z_baleni(self):
        dodavatel = Dodavatel.objects.create(nazev="Test dodavatel", ico="12345678")
        testoviny = Surovina.objects.create(nazev="Testoviny", jednotka="kg")
        prijem = PrijemSkladu.objects.create(
            datum=self.datum,
            dodavatel=dodavatel,
            cislo_faktury="FV-1",
            castka_faktury_celkem=Decimal("627.20"),
        )

        polozka = PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=testoviny,
            pocet_baleni=Decimal("2.000"),
            mnozstvi_v_baleni=Decimal("5.000"),
            jednotka_baleni="kg",
            cena_za_baleni_bez_dph=Decimal("280.0000"),
            sazba_dph=Decimal("12.00"),
            sarze="LOT-2026-04",
            datum_spotreby=self.datum,
        )

        self.assertEqual(polozka.mnozstvi, Decimal("10.000000"))
        self.assertEqual(polozka.jednotkova_cena, Decimal("56.0000"))
        self.assertEqual(polozka.cena_celkem_bez_dph, Decimal("560.0000000"))
        self.assertEqual(polozka.cena_za_baleni_s_dph, Decimal("313.600000"))
        self.assertEqual(polozka.cena_celkem_s_dph, Decimal("627.2000000"))
        self.assertEqual(prijem.rozdil_faktury, Decimal("0E-7"))

        uzavri_prijem(prijem, user=self.user)
        testoviny.refresh_from_db()

        self.assertEqual(StavSkladu.objects.get(surovina=testoviny).mnozstvi, Decimal("10.000"))
        self.assertEqual(testoviny.prumerna_cena_za_jednotku, Decimal("56.0000"))

    def test_prazdny_prijem_nelze_uzavrit(self):
        prijem = PrijemSkladu.objects.create(datum=self.datum)

        with self.assertRaises(ValidationError):
            uzavri_prijem(prijem, user=self.user)

        prijem.refresh_from_db()
        self.assertFalse(prijem.uzavreny)

    def test_prijem_s_nulovou_polozkou_nelze_uzavrit(self):
        ryze = Surovina.objects.create(nazev="Ryze", jednotka="kg")
        prijem = PrijemSkladu.objects.create(datum=self.datum)
        PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=ryze,
            mnozstvi=Decimal("0.000"),
            jednotkova_cena=Decimal("35.0000"),
        )

        with self.assertRaises(ValidationError):
            uzavri_prijem(prijem, user=self.user)

        prijem.refresh_from_db()
        self.assertFalse(prijem.uzavreny)

    def test_storno_prijmu_vytvori_opacny_pohyb_a_snizi_stav(self):
        ryze = Surovina.objects.create(nazev="Ryze", jednotka="kg")
        prijem = PrijemSkladu.objects.create(datum=self.datum)
        PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=ryze,
            mnozstvi=Decimal("5.000"),
            jednotkova_cena=Decimal("35.0000"),
        )
        uzavri_prijem(prijem, user=self.user)

        self.assertTrue(stornuj_prijem(prijem, user=self.user))

        prijem.refresh_from_db()
        self.assertTrue(prijem.stornovano)
        self.assertEqual(StavSkladu.objects.get(surovina=ryze).mnozstvi, Decimal("0.000"))
        self.assertEqual(PohybSkladu.objects.filter(prijem=prijem).count(), 2)
        self.assertFalse(stornuj_prijem(prijem, user=self.user))

    def test_admin_uzavreny_prijem_je_readonly_a_ukazuje_pohyby(self):
        from .admin import PohybPrijmuInline, PolozkaPrijmuInline, PrijemSkladuAdmin

        ryze = Surovina.objects.create(nazev="Ryze", jednotka="kg")
        prijem = PrijemSkladu.objects.create(datum=self.datum)
        PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=ryze,
            mnozstvi=Decimal("5.000"),
            jednotkova_cena=Decimal("35.0000"),
        )
        uzavri_prijem(prijem, user=self.admin_user)
        prijem.refresh_from_db()

        request = RequestFactory().get("/")
        request.user = self.admin_user
        model_admin = PrijemSkladuAdmin(PrijemSkladu, admin.site)
        polozky_inline = PolozkaPrijmuInline(PrijemSkladu, admin.site)
        pohyby_inline = PohybPrijmuInline(PrijemSkladu, admin.site)

        self.assertTrue(model_admin.has_view_permission(request, prijem))
        self.assertFalse(model_admin.has_change_permission(request, prijem))
        self.assertFalse(model_admin.has_delete_permission(request, prijem))

        self.assertFalse(polozky_inline.has_add_permission(request, prijem))
        self.assertFalse(polozky_inline.has_change_permission(request, prijem))
        self.assertFalse(polozky_inline.has_delete_permission(request, prijem))

        self.assertTrue(pohyby_inline.has_view_permission(request, prijem))
        self.assertFalse(pohyby_inline.has_add_permission(request, prijem))
        self.assertFalse(pohyby_inline.has_change_permission(request, prijem))
        self.assertFalse(pohyby_inline.has_delete_permission(request, prijem))
        self.assertEqual(prijem.pohyby.count(), 1)

    def test_admin_uzavreni_prazdne_vydejky_ji_nejdriv_doplni_z_objednavek(self):
        from .admin import _dopln_vydejku_z_objednavek_pokud_je_prazdna

        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g")
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        self.pridej_sarzi(mouka, Decimal("1000.000"), sarze="MOUKA-ADMIN")
        komponenta = KomponentaJidla.objects.create(
            nazev="Testovaci zaklad",
            typ=KomponentaJidla.TYP_OSTATNI,
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=mouka,
            mnozstvi_na_porci=Decimal("100.000"),
        )
        druh = DruhJidla.objects.create(nazev="Obed")
        jidlo = Jidlo.objects.create(nazev="Testovaci jidlo", cena=Decimal("80.00"), druh=druh)
        JidloKomponenta.objects.create(jidlo=jidlo, komponenta=komponenta)
        jidelnicek = Jidelnicek.objects.create(
            platnost_od=self.datum,
            platnost_do=self.datum,
        )
        polozka_menu = PolozkaJidelnicku.objects.create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        objednavka = Order.objects.create(user=self.user, datum_vydeje=self.datum)
        OrderItem.objects.create(
            order=objednavka,
            menu_item=polozka_menu,
            quantity=2,
            cena=jidlo.cena,
        )
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )

        _dopln_vydejku_z_objednavek_pokud_je_prazdna(vydejka)
        self.assertEqual(vydejka.polozky.get(surovina=mouka).mnozstvi, Decimal("200.000"))

        self.assertTrue(uzavri_vydejku(vydejka, user=self.user))
        self.assertEqual(StavSkladu.objects.get(surovina=mouka).mnozstvi, Decimal("800.000"))
        self.assertEqual(PohybSkladu.objects.filter(vydejka=vydejka).count(), 1)

    def test_skladovy_dashboard_ukazuje_expirace_a_blizici_se_minimum(self):
        from .admin import SkladSpotrebniKosAdmin

        mleko = Surovina.objects.create(nazev="Mléko dashboard", jednotka="l")
        mouka = Surovina.objects.create(nazev="Mouka dashboard", jednotka="kg")
        StavSkladu.objects.create(
            surovina=mleko,
            mnozstvi=Decimal("8.000"),
            min_mnozstvi=Decimal("10.000"),
        )
        StavSkladu.objects.create(
            surovina=mouka,
            mnozstvi=Decimal("11.000"),
            min_mnozstvi=Decimal("10.000"),
        )
        prijem = PrijemSkladu.objects.create(
            datum=self.datum,
            uzavreny=True,
        )
        polozka_prijmu = PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=mleko,
            pocet_baleni=Decimal("1.000"),
            mnozstvi_v_baleni=Decimal("5.000"),
            jednotka_baleni="l",
            cena_za_baleni_bez_dph=Decimal("100.0000"),
            sarze="EXP-1",
            datum_spotreby=self.datum + timedelta(days=5),
        )
        SarzeSkladu.objects.create(
            surovina=mleko,
            polozka_prijmu=polozka_prijmu,
            sarze="EXP-1",
            typ_data_spotreby="POUZITELNOST",
            datum_spotreby=self.datum + timedelta(days=5),
            mnozstvi_prijato=Decimal("5.000"),
            mnozstvi_zbyva=Decimal("5.000"),
            cena_za_jednotku=Decimal("20.0000"),
            stav=SarzeSkladu.STAV_POUZITELNA,
        )

        model_admin = SkladSpotrebniKosAdmin(SkladDashboard, admin.site)
        expirace = list(model_admin._dashboard_expirace(self.datum))
        alerty = model_admin._dashboard_minimum_alerty()

        self.assertEqual(expirace[0].surovina, mleko)
        self.assertEqual(expirace[0].sarze, "EXP-1")
        self.assertEqual([row["surovina"] for row in alerty], [mleko, mouka])
        self.assertTrue(alerty[0]["pod_min"])
        self.assertFalse(alerty[1]["pod_min"])

    def test_storno_vydejky_vrati_suroviny_na_sklad(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g", prumerna_cena_za_jednotku=Decimal("2.0000"))
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        self.pridej_sarzi(mouka, Decimal("1000.000"), sarze="MOUKA-STORNO")
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )
        PolozkaVydejky.objects.create(vydejka=vydejka, surovina=mouka, mnozstvi=Decimal("200.000"))
        uzavri_vydejku(vydejka, user=self.user)

        self.assertTrue(stornuj_vydejku(vydejka, user=self.user))

        vydejka.refresh_from_db()
        self.assertTrue(vydejka.stornovano)
        self.assertEqual(StavSkladu.objects.get(surovina=mouka).mnozstvi, Decimal("1000.000"))
        self.assertEqual(PohybSkladu.objects.filter(vydejka=vydejka).count(), 2)

    def test_po_stornu_lze_vytvorit_novou_vydejku_pro_stejny_den_skupinu_a_typ(self):
        mouka = Surovina.objects.create(nazev="Mouka po stornu", jednotka="g")
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        self.pridej_sarzi(mouka, Decimal("1000.000"), sarze="MOUKA-STORNO-2")
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy=Vydejka.TYP_STRAVY_OBED,
        )
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=mouka,
            mnozstvi=Decimal("100.000"),
        )
        uzavri_vydejku(vydejka, user=self.user)
        stornuj_vydejku(vydejka, user=self.user)

        nova_vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy=Vydejka.TYP_STRAVY_OBED,
        )

        self.assertNotEqual(vydejka.id, nova_vydejka.id)
        self.assertFalse(nova_vydejka.stornovano)

    def test_vydejka_muže_byt_bez_stravovaci_skupiny_a_typu_stravy(self):
        vydejka = Vydejka.objects.create(datum=self.datum)

        self.assertIsNone(vydejka.stravovaci_skupina)
        self.assertEqual(vydejka.typ_stravy, "")

    def test_stav_skladu_k_datu_se_rekonstruuje_z_pohybu(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g")
        prijem = PrijemSkladu.objects.create(datum=self.datum)
        PolozkaPrijmu.objects.create(
            prijem=prijem,
            surovina=mouka,
            mnozstvi=Decimal("500.000"),
            jednotkova_cena=Decimal("2.0000"),
        )
        uzavri_prijem(prijem, user=self.user)
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )
        PolozkaVydejky.objects.create(vydejka=vydejka, surovina=mouka, mnozstvi=Decimal("120.000"))
        uzavri_vydejku(vydejka, user=self.user)

        self.assertEqual(stav_skladu_k_datu(self.datum, mouka), Decimal("380.000"))

    def test_najdi_nedostatecne_stavy_pro_vydejku_vraci_chybejici_mnozstvi(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g")
        voda = Surovina.objects.create(nazev="Voda", jednotka="ml")
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("50.000"))
        StavSkladu.objects.create(surovina=voda, mnozstvi=Decimal("200.000"))
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=mouka,
            mnozstvi=Decimal("80.000"),
        )
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=voda,
            mnozstvi=Decimal("100.000"),
        )

        nedostatky = najdi_nedostatecne_stavy_pro_vydejku(vydejka)

        self.assertEqual(len(nedostatky), 1)
        self.assertEqual(nedostatky[0]["surovina"], mouka)
        self.assertEqual(nedostatky[0]["stav"], Decimal("50.000"))
        self.assertEqual(nedostatky[0]["pozadovano"], Decimal("80.000"))
        self.assertEqual(nedostatky[0]["chybi"], Decimal("30.000"))

    def test_naklady_mesic_pocita_vydeje_a_storno_vydeje_jako_korekci(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g", prumerna_cena_za_jednotku=Decimal("2.0000"))
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        self.pridej_sarzi(mouka, Decimal("1000.000"), sarze="MOUKA-NAKLADY")
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy="OBED",
        )
        PolozkaVydejky.objects.create(vydejka=vydejka, surovina=mouka, mnozstvi=Decimal("200.000"))
        uzavri_vydejku(vydejka, user=self.user)

        naklady = spocitej_naklady_mesic(self.datum.year, self.datum.month, self.skupina)
        self.assertEqual(naklady["vydeje"], Decimal("400.0000000"))

        stornuj_vydejku(vydejka, user=self.user)
        naklady_po_stornu = spocitej_naklady_mesic(self.datum.year, self.datum.month, self.skupina)
        self.assertEqual(naklady_po_stornu["vydeje"], Decimal("0E-7"))

    def test_preved_na_gramy_resi_zakladni_jednotky(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="kg")
        mleko = Surovina.objects.create(nazev="Mleko", jednotka="l")
        vejce = Surovina.objects.create(nazev="Vejce", jednotka="ks", hmotnost_ks_g=Decimal("55.000"))

        self.assertEqual(preved_na_gramy(mouka, Decimal("2.500")), Decimal("2500.000"))
        self.assertEqual(preved_na_gramy(mleko, Decimal("1.500")), Decimal("1500.000"))
        self.assertEqual(preved_na_gramy(vejce, Decimal("2.000")), Decimal("110.000000"))

    def test_spotrebni_kos_2025_pocita_z_uzavrene_vydejky_a_denni_normy(self):
        maso = Surovina.objects.create(
            nazev="Kuřecí maso SK",
            jednotka="g",
            skupina_sk=Surovina.SK_MASO,
            koeficient_ciste_hmotnosti_sk=Decimal("0.8000"),
            koeficient_zapoctu_sk=Decimal("1.0000"),
        )
        StavSkladu.objects.create(surovina=maso, mnozstvi=Decimal("1000.000"))
        self.pridej_sarzi(maso, Decimal("1000.000"), sarze="MASO-SK")
        NormaSpotrebnihoKose.objects.create(
            vekova_kategorie=NormaSpotrebnihoKose.VEK_15_PLUS,
            typ_jidla=NormaSpotrebnihoKose.TYP_OBED,
            skupina_sk=Surovina.SK_MASO,
            norma_g_den=Decimal("65.000"),
        )
        druh = DruhJidla.objects.create(nazev="Oběd")
        jidlo = Jidlo.objects.create(nazev="Kuře test SK", cena=Decimal("80.00"), druh=druh)
        komponenta = KomponentaJidla.objects.create(
            nazev="Kuře test SK komponenta",
            typ=KomponentaJidla.TYP_OSTATNI,
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=maso,
            mnozstvi_na_porci=Decimal("100.000"),
        )
        JidloKomponenta.objects.create(jidlo=jidlo, komponenta=komponenta)
        jidelnicek = Jidelnicek.objects.create(platnost_od=self.datum, platnost_do=self.datum)
        menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        objednavka = Order.objects.create(user=self.user, datum_vydeje=self.datum)
        OrderItem.objects.create(order=objednavka, menu_item=menu_item, quantity=1, cena=Decimal("80.00"))
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy=Vydejka.TYP_STRAVY_OBED,
        )
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=maso,
            mnozstvi=Decimal("100.000"),
        )
        uzavri_vydejku(vydejka, user=self.user)

        self.assertEqual(
            spocitej_zapocitatelnou_hmotnost_sk(maso, Decimal("100.000")),
            Decimal("80.00000000000"),
        )
        rows = priprav_radky_spotrebi_kos_tabulka(
            self.datum.year,
            self.datum.month,
            self.skupina,
            date_from=self.datum,
            date_to=self.datum,
        )

        maso_row = next(r for r in rows if r["skupina_kod"] == Surovina.SK_MASO)
        self.assertEqual(maso_row["norma_g"], Decimal("65.000"))
        self.assertEqual(maso_row["skutecnost_g"], Decimal("80.00000000000"))
        self.assertEqual(maso_row["stav"], "ok")

    def test_spotrebni_kos_normalizuje_stare_skupiny_a_fallbackuje_stravniky(self):
        mleko = Surovina.objects.create(
            nazev="Mléko legacy SK",
            jednotka="g",
            skupina_sk="mleko",
            koeficient_ciste_hmotnosti_sk=Decimal("1.0000"),
            koeficient_zapoctu_sk=Decimal("1.0000"),
        )
        self.pridej_sarzi(mleko, Decimal("100.000"), sarze="MLEKO-LEGACY")
        NormaSpotrebnihoKose.objects.create(
            vekova_kategorie=NormaSpotrebnihoKose.VEK_15_PLUS,
            typ_jidla=NormaSpotrebnihoKose.TYP_OBED,
            skupina_sk=Surovina.SK_MLEKO,
            norma_g_den=Decimal("111.000"),
        )
        druh = DruhJidla.objects.create(nazev="Hlavní jídlo")
        jidlo = Jidlo.objects.create(nazev="Kaše test SK", cena=Decimal("80.00"), druh=druh)
        komponenta = KomponentaJidla.objects.create(
            nazev="Kaše test SK komponenta",
            typ=KomponentaJidla.TYP_OSTATNI,
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=mleko,
            mnozstvi_na_porci=Decimal("50.000"),
        )
        JidloKomponenta.objects.create(jidlo=jidlo, komponenta=komponenta)
        jidelnicek = Jidelnicek.objects.create(platnost_od=self.datum, platnost_do=self.datum)
        menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        student_bez_skupiny = get_user_model().objects.create_user(
            username="student_bez_skupiny",
            password="test",
        )
        objednavka = Order.objects.create(user=student_bez_skupiny, datum_vydeje=self.datum)
        OrderItem.objects.create(order=objednavka, menu_item=menu_item, quantity=2, cena=Decimal("80.00"))
        vydejka = Vydejka.objects.create(
            datum=self.datum,
            stravovaci_skupina=self.skupina,
            typ_stravy=Vydejka.TYP_STRAVY_OBED,
        )
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=mleko,
            mnozstvi=Decimal("100.000"),
        )
        uzavri_vydejku(vydejka, user=self.user)

        rows = priprav_radky_spotrebi_kos_tabulka(
            self.datum.year,
            self.datum.month,
            self.skupina,
            date_from=self.datum,
            date_to=self.datum,
        )
        souhrn = spocitej_souhrn_spotrebniho_kose(self.datum, self.datum, self.skupina)

        self.assertNotIn("mleko", {r["skupina_kod"] for r in rows})
        mleko_row = next(r for r in rows if r["skupina_kod"] == Surovina.SK_MLEKO)
        self.assertEqual(mleko_row["norma_g"], Decimal("222.000"))
        self.assertEqual(mleko_row["skutecnost_g"], Decimal("100.00000000000"))
        self.assertEqual(souhrn["pocet_jidel"], Decimal("2"))
        self.assertEqual(souhrn["pocet_stravniku"], 1)

    def test_spotrebni_kos_skupiny_pocita_i_z_vydejky_bez_skupiny(self):
        maso = Surovina.objects.create(
            nazev="Maso bez skupiny výdejky SK",
            jednotka="g",
            skupina_sk=Surovina.SK_MASO,
            koeficient_ciste_hmotnosti_sk=Decimal("1.0000"),
            koeficient_zapoctu_sk=Decimal("1.0000"),
        )
        self.pridej_sarzi(maso, Decimal("240.000"), sarze="MASO-BEZ-SKUPINY")
        NormaSpotrebnihoKose.objects.create(
            vekova_kategorie=NormaSpotrebnihoKose.VEK_15_PLUS,
            typ_jidla=NormaSpotrebnihoKose.TYP_OBED,
            skupina_sk=Surovina.SK_MASO,
            norma_g_den=Decimal("65.000"),
        )
        druh = DruhJidla.objects.create(nazev="Hlavní jídlo")
        jidlo = Jidlo.objects.create(nazev="Oběd bez skupiny výdejky", cena=Decimal("80.00"), druh=druh)
        komponenta = KomponentaJidla.objects.create(
            nazev="Maso bez skupiny výdejky komponenta",
            typ=KomponentaJidla.TYP_OSTATNI,
        )
        KomponentaSurovina.objects.create(
            komponenta=komponenta,
            surovina=maso,
            mnozstvi_na_porci=Decimal("80.000"),
        )
        JidloKomponenta.objects.create(jidlo=jidlo, komponenta=komponenta)
        jidelnicek = Jidelnicek.objects.create(platnost_od=self.datum, platnost_do=self.datum)
        menu_item = PolozkaJidelnicku.objects.create(
            jidelnicek=jidelnicek,
            druh_jidla=druh,
            jidlo=jidlo,
        )
        objednavka = Order.objects.create(user=self.user, datum_vydeje=self.datum)
        OrderItem.objects.create(order=objednavka, menu_item=menu_item, quantity=3, cena=Decimal("80.00"))
        vydejka = Vydejka.objects.create(datum=self.datum)
        PolozkaVydejky.objects.create(
            vydejka=vydejka,
            surovina=maso,
            mnozstvi=Decimal("240.000"),
        )
        uzavri_vydejku(vydejka, user=self.user)

        rows = priprav_radky_spotrebi_kos_tabulka(
            self.datum.year,
            self.datum.month,
            self.skupina,
            date_from=self.datum,
            date_to=self.datum,
        )
        souhrn = spocitej_souhrn_spotrebniho_kose(self.datum, self.datum, self.skupina)

        maso_row = next(r for r in rows if r["skupina_kod"] == Surovina.SK_MASO)
        self.assertEqual(maso_row["norma_g"], Decimal("195.000"))
        self.assertEqual(maso_row["skutecnost_g"], Decimal("240.00000000000"))
        self.assertEqual(souhrn["pocet_jidel"], Decimal("3"))
        self.assertEqual(souhrn["pocet_vydejek"], 1)

    def test_prosla_sarze_se_nepouzije_a_odpis_expirace_ji_odepise(self):
        mleko = Surovina.objects.create(nazev="Mléko expirace", jednotka="l")
        StavSkladu.objects.create(surovina=mleko, mnozstvi=Decimal("8.000"))
        self.pridej_sarzi(
            mleko,
            Decimal("5.000"),
            datum_spotreby=self.datum - timedelta(days=1),
            sarze="EXP",
        )
        self.pridej_sarzi(
            mleko,
            Decimal("3.000"),
            datum_spotreby=self.datum + timedelta(days=10),
            sarze="OK",
        )

        vydejka = Vydejka.objects.create(datum=self.datum)
        PolozkaVydejky.objects.create(vydejka=vydejka, surovina=mleko, mnozstvi=Decimal("4.000"))

        with self.assertRaises(ValidationError):
            uzavri_vydejku(vydejka, user=self.user)

        odpis = OdpisExpirace.objects.create(datum=self.datum, popis="Test expirace")
        self.assertTrue(uzavri_odpis_expirace(odpis, user=self.user))

        exp_sarze = SarzeSkladu.objects.get(sarze="EXP")
        ok_sarze = SarzeSkladu.objects.get(sarze="OK")
        self.assertEqual(exp_sarze.stav, SarzeSkladu.STAV_ODEPSANA)
        self.assertEqual(exp_sarze.mnozstvi_zbyva, Decimal("0"))
        self.assertEqual(ok_sarze.mnozstvi_zbyva, Decimal("3.000"))
        self.assertEqual(StavSkladu.objects.get(surovina=mleko).mnozstvi, Decimal("3.000"))
        self.assertEqual(PohybSkladu.objects.filter(odpis_expirace=odpis).count(), 1)

    def test_uzavri_inventuru_vytvori_rozdilovy_pohyb_a_prepise_stav_idempotentne(self):
        cukr = Surovina.objects.create(nazev="Cukr", jednotka="kg")
        StavSkladu.objects.create(surovina=cukr, mnozstvi=Decimal("10.000"))
        inventura = Inventura.objects.create(datum=self.datum)
        polozka = PolozkaInventury.objects.create(
            inventura=inventura,
            surovina=cukr,
            stav_pred=Decimal("10.000"),
            fyzicky_stav=Decimal("7.500"),
        )

        self.assertTrue(uzavri_inventuru(inventura, user=self.user))

        polozka.refresh_from_db()
        self.assertEqual(polozka.stav_pred, Decimal("10.000"))
        self.assertEqual(polozka.rozdil, Decimal("-2.500"))
        self.assertEqual(StavSkladu.objects.get(surovina=cukr).mnozstvi, Decimal("7.500"))

        pohyb = PohybSkladu.objects.get(inventura=inventura)
        self.assertEqual(pohyb.typ, PohybSkladu.TYP_INVENTURA_MINUS)
        self.assertEqual(pohyb.mnozstvi, Decimal("2.500"))

        self.assertFalse(uzavri_inventuru(inventura, user=self.user))
        self.assertEqual(PohybSkladu.objects.filter(inventura=inventura).count(), 1)

    def test_uzavri_inventuru_vytvori_plusovy_rozdilovy_pohyb(self):
        cukr = Surovina.objects.create(nazev="Cukr", jednotka="kg")
        StavSkladu.objects.create(surovina=cukr, mnozstvi=Decimal("10.000"))
        inventura = Inventura.objects.create(datum=self.datum)
        polozka = PolozkaInventury.objects.create(
            inventura=inventura,
            surovina=cukr,
            stav_pred=Decimal("10.000"),
            fyzicky_stav=Decimal("12.250"),
        )

        self.assertTrue(uzavri_inventuru(inventura, user=self.user))

        polozka.refresh_from_db()
        self.assertEqual(polozka.stav_pred, Decimal("10.000"))
        self.assertEqual(polozka.rozdil, Decimal("2.250"))
        self.assertEqual(StavSkladu.objects.get(surovina=cukr).mnozstvi, Decimal("12.250"))

        pohyb = PohybSkladu.objects.get(inventura=inventura)
        self.assertEqual(pohyb.typ, PohybSkladu.TYP_INVENTURA_PLUS)
        self.assertEqual(pohyb.mnozstvi, Decimal("2.250"))

    def test_storno_inventury_vytvori_opacny_rozdilovy_pohyb(self):
        cukr = Surovina.objects.create(nazev="Cukr", jednotka="kg")
        StavSkladu.objects.create(surovina=cukr, mnozstvi=Decimal("10.000"))
        inventura = Inventura.objects.create(datum=self.datum)
        PolozkaInventury.objects.create(
            inventura=inventura,
            surovina=cukr,
            stav_pred=Decimal("10.000"),
            fyzicky_stav=Decimal("12.000"),
        )
        uzavri_inventuru(inventura, user=self.user)

        self.assertTrue(stornuj_inventuru(inventura, user=self.user))

        inventura.refresh_from_db()
        self.assertTrue(inventura.stornovano)
        self.assertEqual(StavSkladu.objects.get(surovina=cukr).mnozstvi, Decimal("10.000"))
        self.assertEqual(PohybSkladu.objects.filter(inventura=inventura).count(), 2)
        self.assertEqual(
            PohybSkladu.objects.filter(
                inventura=inventura,
                typ=PohybSkladu.TYP_INVENTURA_MINUS,
            ).count(),
            1,
        )
