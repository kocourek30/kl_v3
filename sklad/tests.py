from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib import admin
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
    PohybSkladu,
    PolozkaInventury,
    PolozkaPrijmu,
    PolozkaVydejky,
    PrijemSkladu,
    StavSkladu,
    Surovina,
    Vydejka,
)
from .services import (
    generate_vydejka_from_orders,
    najdi_nedostatecne_stavy_pro_vydejku,
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

    def test_vydejka_z_objednavek_odecte_komponentove_suroviny_idempotentne(self):
        mouka = Surovina.objects.create(nazev="Mouka", jednotka="g")
        voda = Surovina.objects.create(nazev="Voda", jednotka="ml")
        StavSkladu.objects.create(surovina=mouka, mnozstvi=Decimal("1000.000"))
        StavSkladu.objects.create(surovina=voda, mnozstvi=Decimal("500.000"))

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
