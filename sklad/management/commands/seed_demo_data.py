from decimal import Decimal
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from jidelnicek.models import DruhJidla, Jidlo, Jidelnicek, PolozkaJidelnicku
from sklad.models import (
    Surovina,
    StavSkladu,
    RecepturaPolozka,
)

# Nový komponentový model
try:
    from sklad.models import KomponentaJidla, KomponentaSurovina, JidloKomponenta
    HAS_COMPONENTS = True
except Exception:
    HAS_COMPONENTS = False
    KomponentaJidla = None
    KomponentaSurovina = None
    JidloKomponenta = None


DRUHY_JIDEL = [
    {"nazev": "Polévka", "ikona": "bi-cup-hot"},
    {"nazev": "Hlavní jídlo", "ikona": "bi-egg-fried"},
    {"nazev": "Dezert", "ikona": "bi-cake2"},
]


SUROVINY = [
    # zelenina / základ
    {"nazev": "Cibule", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.035", "stav": "5000"},
    {"nazev": "Mrkev", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.028", "stav": "6000"},
    {"nazev": "Celer", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.045", "stav": "2500"},
    {"nazev": "Petržel", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.050", "stav": "2000"},
    {"nazev": "Brambory", "jednotka": "g", "skupina_sk": "brambory", "cena": "0.012", "stav": "25000"},
    {"nazev": "Rajčatový protlak", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.080", "stav": "3000"},
    {"nazev": "Česnek", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.140", "stav": "500"},
    {"nazev": "Kořenová zelenina mix", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.030", "stav": "5000"},

    # maso / mléko / obiloviny
    {"nazev": "Hovězí maso", "jednotka": "g", "skupina_sk": "maso", "cena": "0.220", "stav": "12000"},
    {"nazev": "Vepřové mleté maso", "jednotka": "g", "skupina_sk": "maso", "cena": "0.165", "stav": "10000"},
    {"nazev": "Kuřecí maso", "jednotka": "g", "skupina_sk": "maso", "cena": "0.145", "stav": "12000"},
    {"nazev": "Mléko", "jednotka": "ml", "skupina_sk": "mleko", "cena": "0.022", "stav": "15000"},
    {"nazev": "Smetana", "jednotka": "ml", "skupina_sk": "mleko", "cena": "0.055", "stav": "5000"},
    {"nazev": "Hladká mouka", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.018", "stav": "12000"},
    {"nazev": "Krupice", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.020", "stav": "4000"},
    {"nazev": "Těstoviny", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.032", "stav": "10000"},
    {"nazev": "Houskový knedlík", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.040", "stav": "10000"},
    {"nazev": "Karlovarský knedlík", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.048", "stav": "7000"},
    {"nazev": "Rýže", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.026", "stav": "12000"},
    {"nazev": "Nudle", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.034", "stav": "3000"},

    # tuky / cukr / ostatní
    {"nazev": "Řepkový olej", "jednotka": "g", "skupina_sk": "tuky", "cena": "0.050", "stav": "5000"},
    {"nazev": "Cukr", "jednotka": "g", "skupina_sk": "cukr", "cena": "0.020", "stav": "5000"},
    {"nazev": "Sůl", "jednotka": "g", "skupina_sk": "", "cena": "0.005", "stav": "3000"},
    {"nazev": "Paprika mletá", "jednotka": "g", "skupina_sk": "", "cena": "0.180", "stav": "500"},
    {"nazev": "Kmín", "jednotka": "g", "skupina_sk": "", "cena": "0.120", "stav": "300"},
    {"nazev": "Majoránka", "jednotka": "g", "skupina_sk": "", "cena": "0.180", "stav": "200"},
    {"nazev": "Jogurt bílý", "jednotka": "g", "skupina_sk": "mleko", "cena": "0.050", "stav": "3000"},
    {"nazev": "Jablko", "jednotka": "g", "skupina_sk": "ovoce", "cena": "0.030", "stav": "6000"},
    {"nazev": "Mražená zelenina mix", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.040", "stav": "5000"},
]


KOMPONENTY = [
    {
        "nazev": "Bramboračka",
        "typ": "POLEVKA",
        "porce_text": "300 ml",
        "suroviny": [
            ("Brambory", "120"),
            ("Mrkev", "20"),
            ("Celer", "10"),
            ("Cibule", "10"),
            ("Řepkový olej", "5"),
            ("Hladká mouka", "5"),
            ("Česnek", "2"),
            ("Majoránka", "1"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Kuřecí vývar",
        "typ": "POLEVKA",
        "porce_text": "300 ml",
        "suroviny": [
            ("Kuřecí maso", "80"),
            ("Kořenová zelenina mix", "40"),
            ("Nudle", "20"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Rajská omáčka",
        "typ": "OMACKA",
        "porce_text": "180 ml",
        "suroviny": [
            ("Rajčatový protlak", "70"),
            ("Cibule", "20"),
            ("Hladká mouka", "12"),
            ("Cukr", "6"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Hovězí vařené",
        "typ": "MASO",
        "porce_text": "100 g",
        "suroviny": [
            ("Hovězí maso", "120"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Masové kuličky",
        "typ": "MASO",
        "porce_text": "100 g",
        "suroviny": [
            ("Vepřové mleté maso", "120"),
            ("Cibule", "10"),
            ("Sůl", "2"),
            ("Paprika mletá", "1"),
        ],
    },
    {
        "nazev": "Kuře na paprice",
        "typ": "MASO",
        "porce_text": "120 g",
        "suroviny": [
            ("Kuřecí maso", "120"),
            ("Cibule", "25"),
            ("Paprika mletá", "3"),
            ("Smetana", "40"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Houskový knedlík - porce",
        "typ": "PRILOHA",
        "porce_text": "150 g",
        "suroviny": [
            ("Houskový knedlík", "150"),
        ],
    },
    {
        "nazev": "Karlovarský knedlík - porce",
        "typ": "PRILOHA",
        "porce_text": "150 g",
        "suroviny": [
            ("Karlovarský knedlík", "150"),
        ],
    },
    {
        "nazev": "Těstoviny vařené - porce",
        "typ": "PRILOHA",
        "porce_text": "100 g",
        "suroviny": [
            ("Těstoviny", "100"),
        ],
    },
    {
        "nazev": "Rýže vařená - porce",
        "typ": "PRILOHA",
        "porce_text": "80 g",
        "suroviny": [
            ("Rýže", "80"),
        ],
    },
    {
        "nazev": "Zeleninové rizoto",
        "typ": "OSTATNI",
        "porce_text": "280 g",
        "suroviny": [
            ("Rýže", "80"),
            ("Mražená zelenina mix", "100"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Krupicová kaše",
        "typ": "DEZERT",
        "porce_text": "250 ml",
        "suroviny": [
            ("Mléko", "250"),
            ("Krupice", "30"),
            ("Cukr", "10"),
        ],
    },
    {
        "nazev": "Jogurt s jablkem",
        "typ": "DEZERT",
        "porce_text": "200 g",
        "suroviny": [
            ("Jogurt bílý", "150"),
            ("Jablko", "50"),
        ],
    },
]


JIDLA = [
    {
        "nazev": "Bramboračka",
        "druh": "Polévka",
        "cena": "18.00",
        "komponenty": [("Bramboračka", "1.0")],
    },
    {
        "nazev": "Kuřecí vývar s nudlemi",
        "druh": "Polévka",
        "cena": "18.00",
        "komponenty": [("Kuřecí vývar", "1.0")],
    },
    {
        "nazev": "Rajská s hovězím a houskovým knedlíkem",
        "druh": "Hlavní jídlo",
        "cena": "95.00",
        "komponenty": [
            ("Rajská omáčka", "1.0"),
            ("Hovězí vařené", "1.0"),
            ("Houskový knedlík - porce", "1.0"),
        ],
    },
    {
        "nazev": "Rajská s hovězím a těstovinami",
        "druh": "Hlavní jídlo",
        "cena": "92.00",
        "komponenty": [
            ("Rajská omáčka", "1.0"),
            ("Hovězí vařené", "1.0"),
            ("Těstoviny vařené - porce", "1.0"),
        ],
    },
    {
        "nazev": "Rajská s masovými kuličkami a těstovinami",
        "druh": "Hlavní jídlo",
        "cena": "88.00",
        "komponenty": [
            ("Rajská omáčka", "1.0"),
            ("Masové kuličky", "1.0"),
            ("Těstoviny vařené - porce", "1.0"),
        ],
    },
    {
        "nazev": "Kuře na paprice s těstovinami",
        "druh": "Hlavní jídlo",
        "cena": "89.00",
        "komponenty": [
            ("Kuře na paprice", "1.0"),
            ("Těstoviny vařené - porce", "1.0"),
        ],
    },
    {
        "nazev": "Zeleninové rizoto",
        "druh": "Hlavní jídlo",
        "cena": "76.00",
        "komponenty": [
            ("Zeleninové rizoto", "1.0"),
        ],
    },
    {
        "nazev": "Krupicová kaše",
        "druh": "Dezert",
        "cena": "35.00",
        "komponenty": [("Krupicová kaše", "1.0")],
    },
    {
        "nazev": "Jogurt s jablkem",
        "druh": "Dezert",
        "cena": "32.00",
        "komponenty": [("Jogurt s jablkem", "1.0")],
    },
]


JIDELNICEK_PLAN = [
    ("Polévka", "Bramboračka"),
    ("Hlavní jídlo", "Rajská s hovězím a houskovým knedlíkem"),
    ("Dezert", "Jogurt s jablkem"),

    ("Polévka", "Kuřecí vývar s nudlemi"),
    ("Hlavní jídlo", "Kuře na paprice s těstovinami"),
    ("Dezert", "Krupicová kaše"),

    ("Polévka", "Bramboračka"),
    ("Hlavní jídlo", "Rajská s hovězím a těstovinami"),
    ("Dezert", "Jogurt s jablkem"),

    ("Polévka", "Kuřecí vývar s nudlemi"),
    ("Hlavní jídlo", "Rajská s masovými kuličkami a těstovinami"),
    ("Dezert", "Krupicová kaše"),

    ("Polévka", "Bramboračka"),
    ("Hlavní jídlo", "Zeleninové rizoto"),
    ("Dezert", "Jogurt s jablkem"),
]


class Command(BaseCommand):
    help = "Naplní demo databázi surovinami, komponentami, jídly a volitelným jídelníčkem."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-menu",
            action="store_true",
            help="Smaže existující demo jídelníček pro aktuální týden a vytvoří ho znovu.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seed demo databáze startuje..."))

        druhy = self.seed_druhy_jidel()
        suroviny = self.seed_suroviny()
        self.seed_skladove_stavy(suroviny)

        if HAS_COMPONENTS:
            komponenty = self.seed_komponenty(suroviny)
            jidla = self.seed_jidla_pres_komponenty(druhy, komponenty)
            self.stdout.write(self.style.SUCCESS(f"Komponentový režim aktivní: {len(komponenty)} komponent, {len(jidla)} jídel."))
        else:
            jidla = self.seed_jidla_pres_starou_recepturu(druhy, suroviny)
            self.stdout.write(self.style.WARNING("Komponentové modely nebyly nalezeny, použita stará přímá receptura."))

        self.seed_jidelnicek(druhy, jidla, reset_menu=options["reset_menu"])

        self.stdout.write(self.style.SUCCESS("Demo seed databáze je hotový."))

    def seed_druhy_jidel(self):
        result = {}
        for item in DRUHY_JIDEL:
            obj, _ = DruhJidla.objects.update_or_create(
                nazev=item["nazev"],
                defaults={"ikona": item["ikona"]},
            )
            result[obj.nazev] = obj
        return result

    def seed_suroviny(self):
        result = {}
        for item in SUROVINY:
            defaults = {
                "jednotka": item["jednotka"],
                "skupina_sk": item["skupina_sk"],
                "koeficient_sk": Decimal("1.0000"),
                "prumerna_cena_za_jednotku": Decimal(item["cena"]),
            }
            obj, _ = Surovina.objects.update_or_create(
                nazev=item["nazev"],
                defaults=defaults,
            )
            result[obj.nazev] = obj
        return result

    def seed_skladove_stavy(self, suroviny):
        for item in SUROVINY:
            surovina = suroviny[item["nazev"]]
            StavSkladu.objects.update_or_create(
                surovina=surovina,
                defaults={
                    "mnozstvi": Decimal(item["stav"]),
                    "min_mnozstvi": Decimal("0"),
                },
            )

    def seed_komponenty(self, suroviny):
        result = {}

        for item in KOMPONENTY:
            komponenta, _ = KomponentaJidla.objects.update_or_create(
                nazev=item["nazev"],
                defaults={
                    "typ": item["typ"],
                    "aktivni": True,
                    "porce_text": item["porce_text"],
                    "poznamka": "Demo seed",
                },
            )
            result[komponenta.nazev] = komponenta

            existing = {
                ks.surovina.nazev: ks
                for ks in komponenta.suroviny.select_related("surovina").all()
            }

            required_names = set()
            for surovina_nazev, mnozstvi in item["suroviny"]:
                required_names.add(surovina_nazev)
                KomponentaSurovina.objects.update_or_create(
                    komponenta=komponenta,
                    surovina=suroviny[surovina_nazev],
                    defaults={"mnozstvi_na_porci": Decimal(mnozstvi)},
                )

            # smaž jen seed vazby, které už v definici nejsou
            for surovina_nazev, obj in existing.items():
                if surovina_nazev not in required_names:
                    obj.delete()

        return result

    def seed_jidla_pres_komponenty(self, druhy, komponenty):
        result = {}

        for item in JIDLA:
            jidlo, _ = Jidlo.objects.update_or_create(
                nazev=item["nazev"],
                defaults={
                    "druh": druhy[item["druh"]],
                    "cena": Decimal(item["cena"]),
                    "ikona": "",
                },
            )
            result[jidlo.nazev] = jidlo

            existing = {
                jk.komponenta.nazev: jk
                for jk in jidlo.komponenty_jidla.select_related("komponenta").all()
            }

            required_names = set()
            for poradi, (komponenta_nazev, nasobek) in enumerate(item["komponenty"], start=1):
                required_names.add(komponenta_nazev)
                JidloKomponenta.objects.update_or_create(
                    jidlo=jidlo,
                    komponenta=komponenty[komponenta_nazev],
                    defaults={
                        "mnozstvi_nasobek": Decimal(nasobek),
                        "poradi": poradi,
                        "povinna": True,
                    },
                )

            for komponenta_nazev, obj in existing.items():
                if komponenta_nazev not in required_names:
                    obj.delete()

        return result

    def seed_jidla_pres_starou_recepturu(self, druhy, suroviny):
        """
        Fallback pro případ, že ještě komponentové modely nejsou k dispozici.
        Seedne alespoň několik jídel přes starou přímou recepturu.
        """
        fallback_def = [
            {
                "nazev": "Bramboračka",
                "druh": "Polévka",
                "cena": "18.00",
                "suroviny": [
                    ("Brambory", "120"),
                    ("Mrkev", "20"),
                    ("Celer", "10"),
                    ("Cibule", "10"),
                    ("Řepkový olej", "5"),
                ],
            },
            {
                "nazev": "Zeleninové rizoto",
                "druh": "Hlavní jídlo",
                "cena": "76.00",
                "suroviny": [
                    ("Rýže", "80"),
                    ("Mražená zelenina mix", "100"),
                    ("Řepkový olej", "5"),
                ],
            },
            {
                "nazev": "Krupicová kaše",
                "druh": "Dezert",
                "cena": "35.00",
                "suroviny": [
                    ("Mléko", "250"),
                    ("Krupice", "30"),
                    ("Cukr", "10"),
                ],
            },
        ]

        result = {}

        for item in fallback_def:
            jidlo, _ = Jidlo.objects.update_or_create(
                nazev=item["nazev"],
                defaults={
                    "druh": druhy[item["druh"]],
                    "cena": Decimal(item["cena"]),
                    "ikona": "",
                },
            )
            result[jidlo.nazev] = jidlo

            existing = {
                rp.surovina.nazev: rp
                for rp in jidlo.receptura.select_related("surovina").all()
            }

            required_names = set()
            for surovina_nazev, mnozstvi in item["suroviny"]:
                required_names.add(surovina_nazev)
                RecepturaPolozka.objects.update_or_create(
                    jidlo=jidlo,
                    surovina=suroviny[surovina_nazev],
                    defaults={"mnozstvi_na_porci": Decimal(mnozstvi)},
                )

            for surovina_nazev, obj in existing.items():
                if surovina_nazev not in required_names:
                    obj.delete()

        return result

    def seed_jidelnicek(self, druhy, jidla, reset_menu=False):
        start = date.today()
        end = start + timedelta(days=4)

        if reset_menu:
            Jidelnicek.objects.filter(platnost_od=start, platnost_do=end).delete()

        jidelnicek, _ = Jidelnicek.objects.get_or_create(
            platnost_od=start,
            platnost_do=end,
            defaults={"ikona": "bi-calendar-week"},
        )

        if jidelnicek.ikona != "bi-calendar-week":
            jidelnicek.ikona = "bi-calendar-week"
            jidelnicek.save(update_fields=["ikona"])

        # smažeme položky jen pro tento seed týden a vytvoříme je znovu
        jidelnicek.polozky.all().delete()

        idx = 0
        for den_offset in range(5):
            for _ in range(3):  # polévka + hlavní + dezert
                druh_nazev, jidlo_nazev = JIDELNICEK_PLAN[idx]
                PolozkaJidelnicku.objects.create(
                    jidelnicek=jidelnicek,
                    druh_jidla=druhy[druh_nazev],
                    jidlo=jidla[jidlo_nazev],
                )
                idx += 1