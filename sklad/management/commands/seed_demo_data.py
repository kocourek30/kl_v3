from decimal import Decimal
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from jidelnicek.models import DruhJidla, Jidlo, Jidelnicek, PolozkaJidelnicku
from objednavky.models import OrderItem
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

SKUPINY_SK_2025_MAP = {
    "zelenina": Surovina.SK_ZELENINA_OVOCE,
    "ovoce": Surovina.SK_ZELENINA_OVOCE,
    "brambory": Surovina.SK_BRAMBORY,
    "maso": Surovina.SK_MASO,
    "ryby": Surovina.SK_RYBY,
    "mleko": Surovina.SK_MLEKO,
    "obiloviny": Surovina.SK_CELOZRNNE,
    "lusteniny": Surovina.SK_LUSTENINY,
    "tuky": Surovina.SK_TUKY,
    "cukr": Surovina.SK_CUKRY,
    "": Surovina.SK_NEZAPOCITAVA_SE,
}


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


SUROVINY += [
    {"nazev": "Čočka", "jednotka": "g", "skupina_sk": "lusteniny", "cena": "0.055", "stav": "8000"},
    {"nazev": "Fazole bílé", "jednotka": "g", "skupina_sk": "lusteniny", "cena": "0.060", "stav": "6000"},
    {"nazev": "Hrách žlutý", "jednotka": "g", "skupina_sk": "lusteniny", "cena": "0.045", "stav": "6000"},
    {"nazev": "Rybí filé", "jednotka": "g", "skupina_sk": "ryby", "cena": "0.180", "stav": "9000"},
    {"nazev": "Vejce", "jednotka": "ks", "skupina_sk": "mleko", "cena": "4.200", "stav": "360", "hmotnost_ks_g": "55.000"},
    {"nazev": "Tvaroh", "jednotka": "g", "skupina_sk": "mleko", "cena": "0.075", "stav": "5000"},
    {"nazev": "Eidam", "jednotka": "g", "skupina_sk": "mleko", "cena": "0.145", "stav": "4000"},
    {"nazev": "Máslo", "jednotka": "g", "skupina_sk": "tuky", "cena": "0.165", "stav": "3000"},
    {"nazev": "Špenát", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.055", "stav": "5000"},
    {"nazev": "Brokolice", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.060", "stav": "5000"},
    {"nazev": "Květák", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.055", "stav": "5000"},
    {"nazev": "Zelí kysané", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.035", "stav": "6000"},
    {"nazev": "Okurka salátová", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.050", "stav": "4000"},
    {"nazev": "Rajče", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.065", "stav": "4000"},
    {"nazev": "Hlávkový salát", "jednotka": "g", "skupina_sk": "zelenina", "cena": "0.060", "stav": "2500"},
    {"nazev": "Jablka strouhaná", "jednotka": "g", "skupina_sk": "ovoce", "cena": "0.032", "stav": "5000"},
    {"nazev": "Ovesné vločky", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.028", "stav": "5000"},
    {"nazev": "Kuskus", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.045", "stav": "5000"},
    {"nazev": "Bulgur", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.042", "stav": "5000"},
    {"nazev": "Kroupy", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.026", "stav": "5000"},
    {"nazev": "Strouhanka", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.030", "stav": "3000"},
    {"nazev": "Rohlíky", "jednotka": "g", "skupina_sk": "obiloviny", "cena": "0.040", "stav": "5000"},
    {"nazev": "Kakao", "jednotka": "g", "skupina_sk": "", "cena": "0.190", "stav": "800"},
    {"nazev": "Skořice", "jednotka": "g", "skupina_sk": "", "cena": "0.180", "stav": "300"},
]


KOMPONENTY += [
    {
        "nazev": "Zeleninová polévka s kapáním",
        "typ": "POLEVKA",
        "porce_text": "300 ml",
        "suroviny": [
            ("Kořenová zelenina mix", "60"),
            ("Vejce", "0.15"),
            ("Hladká mouka", "12"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Čočková polévka",
        "typ": "POLEVKA",
        "porce_text": "300 ml",
        "suroviny": [
            ("Čočka", "45"),
            ("Mrkev", "25"),
            ("Cibule", "15"),
            ("Česnek", "2"),
            ("Majoránka", "1"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Hráškový krém",
        "typ": "POLEVKA",
        "porce_text": "300 ml",
        "suroviny": [
            ("Hrách žlutý", "45"),
            ("Smetana", "30"),
            ("Cibule", "15"),
            ("Řepkový olej", "4"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Svíčková omáčka",
        "typ": "OMACKA",
        "porce_text": "180 ml",
        "suroviny": [
            ("Kořenová zelenina mix", "90"),
            ("Smetana", "45"),
            ("Hladká mouka", "12"),
            ("Cukr", "4"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Koprová omáčka",
        "typ": "OMACKA",
        "porce_text": "180 ml",
        "suroviny": [
            ("Mléko", "120"),
            ("Smetana", "35"),
            ("Hladká mouka", "14"),
            ("Cukr", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Špenát dušený",
        "typ": "PRILOHA",
        "porce_text": "160 g",
        "suroviny": [
            ("Špenát", "160"),
            ("Česnek", "2"),
            ("Cibule", "15"),
            ("Řepkový olej", "4"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Bramborová kaše",
        "typ": "PRILOHA",
        "porce_text": "220 g",
        "suroviny": [
            ("Brambory", "220"),
            ("Mléko", "40"),
            ("Máslo", "8"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Vařené brambory",
        "typ": "PRILOHA",
        "porce_text": "220 g",
        "suroviny": [
            ("Brambory", "220"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Pečené kuřecí stehno",
        "typ": "MASO",
        "porce_text": "140 g",
        "suroviny": [
            ("Kuřecí maso", "160"),
            ("Řepkový olej", "5"),
            ("Paprika mletá", "2"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Rybí filé pečené",
        "typ": "MASO",
        "porce_text": "120 g",
        "suroviny": [
            ("Rybí filé", "140"),
            ("Máslo", "8"),
            ("Hladká mouka", "8"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Čočka na kyselo",
        "typ": "OSTATNI",
        "porce_text": "250 g",
        "suroviny": [
            ("Čočka", "90"),
            ("Cibule", "25"),
            ("Hladká mouka", "8"),
            ("Řepkový olej", "6"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Vařené vejce",
        "typ": "MASO",
        "porce_text": "1 ks",
        "suroviny": [
            ("Vejce", "1"),
        ],
    },
    {
        "nazev": "Zapečené těstoviny se sýrem",
        "typ": "OSTATNI",
        "porce_text": "280 g",
        "suroviny": [
            ("Těstoviny", "110"),
            ("Eidam", "35"),
            ("Vejce", "0.3"),
            ("Mléko", "60"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Bulgur se zeleninou",
        "typ": "OSTATNI",
        "porce_text": "280 g",
        "suroviny": [
            ("Bulgur", "85"),
            ("Mražená zelenina mix", "120"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Kuskus se zeleninou a sýrem",
        "typ": "OSTATNI",
        "porce_text": "280 g",
        "suroviny": [
            ("Kuskus", "85"),
            ("Mražená zelenina mix", "110"),
            ("Eidam", "30"),
            ("Řepkový olej", "5"),
            ("Sůl", "2"),
        ],
    },
    {
        "nazev": "Okurkový salát",
        "typ": "SALAT",
        "porce_text": "100 g",
        "suroviny": [
            ("Okurka salátová", "90"),
            ("Cukr", "3"),
            ("Sůl", "1"),
        ],
    },
    {
        "nazev": "Rajčatový salát",
        "typ": "SALAT",
        "porce_text": "100 g",
        "suroviny": [
            ("Rajče", "90"),
            ("Cibule", "8"),
            ("Řepkový olej", "2"),
            ("Sůl", "1"),
        ],
    },
    {
        "nazev": "Tvarohový krém s jablky",
        "typ": "DEZERT",
        "porce_text": "180 g",
        "suroviny": [
            ("Tvaroh", "120"),
            ("Jablka strouhaná", "50"),
            ("Cukr", "8"),
        ],
    },
    {
        "nazev": "Žemlovka s jablky",
        "typ": "DEZERT",
        "porce_text": "250 g",
        "suroviny": [
            ("Rohlíky", "90"),
            ("Jablka strouhaná", "120"),
            ("Mléko", "80"),
            ("Vejce", "0.3"),
            ("Cukr", "12"),
            ("Skořice", "1"),
        ],
    },
    {
        "nazev": "Buchtičky s vanilkovým krémem",
        "typ": "DEZERT",
        "porce_text": "250 g",
        "suroviny": [
            ("Hladká mouka", "90"),
            ("Mléko", "160"),
            ("Vejce", "0.2"),
            ("Cukr", "18"),
            ("Máslo", "8"),
        ],
    },
]


JIDLA += [
    {
        "nazev": "Zeleninová polévka s kapáním",
        "druh": "Polévka",
        "cena": "18.00",
        "komponenty": [("Zeleninová polévka s kapáním", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Čočková polévka",
        "druh": "Polévka",
        "cena": "18.00",
        "komponenty": [("Čočková polévka", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Hráškový krém",
        "druh": "Polévka",
        "cena": "18.00",
        "komponenty": [("Hráškový krém", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Svíčková na smetaně s houskovým knedlíkem",
        "druh": "Hlavní jídlo",
        "cena": "98.00",
        "komponenty": [
            ("Svíčková omáčka", "1.0"),
            ("Hovězí vařené", "1.0"),
            ("Houskový knedlík - porce", "1.0"),
        ],
        "sk_cervene_maso": True,
    },
    {
        "nazev": "Koprová omáčka s vejcem a bramborem",
        "druh": "Hlavní jídlo",
        "cena": "78.00",
        "komponenty": [
            ("Koprová omáčka", "1.0"),
            ("Vařené vejce", "1.0"),
            ("Vařené brambory", "1.0"),
        ],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Pečené kuřecí stehno s bramborovou kaší",
        "druh": "Hlavní jídlo",
        "cena": "92.00",
        "komponenty": [
            ("Pečené kuřecí stehno", "1.0"),
            ("Bramborová kaše", "1.0"),
        ],
        "sk_bile_maso": True,
    },
    {
        "nazev": "Rybí filé s vařenými bramborami",
        "druh": "Hlavní jídlo",
        "cena": "92.00",
        "komponenty": [
            ("Rybí filé pečené", "1.0"),
            ("Vařené brambory", "1.0"),
        ],
        "sk_rybi_pokrm": True,
    },
    {
        "nazev": "Čočka na kyselo s vejcem",
        "druh": "Hlavní jídlo",
        "cena": "78.00",
        "komponenty": [
            ("Čočka na kyselo", "1.0"),
            ("Vařené vejce", "1.0"),
        ],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Zapečené těstoviny se sýrem",
        "druh": "Hlavní jídlo",
        "cena": "82.00",
        "komponenty": [("Zapečené těstoviny se sýrem", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Bulgur se zeleninou",
        "druh": "Hlavní jídlo",
        "cena": "78.00",
        "komponenty": [("Bulgur se zeleninou", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Kuskus se zeleninou a sýrem",
        "druh": "Hlavní jídlo",
        "cena": "82.00",
        "komponenty": [("Kuskus se zeleninou a sýrem", "1.0")],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Špenát s bramborem a vejcem",
        "druh": "Hlavní jídlo",
        "cena": "78.00",
        "komponenty": [
            ("Špenát dušený", "1.0"),
            ("Vařené brambory", "1.0"),
            ("Vařené vejce", "1.0"),
        ],
        "sk_bezmasy_pokrm": True,
    },
    {
        "nazev": "Žemlovka s jablky",
        "druh": "Hlavní jídlo",
        "cena": "72.00",
        "komponenty": [("Žemlovka s jablky", "1.0")],
        "sk_bezmasy_pokrm": True,
        "sk_sladky_pokrm": True,
        "sk_dezert_s_volnym_cukrem": True,
    },
    {
        "nazev": "Buchtičky s vanilkovým krémem",
        "druh": "Hlavní jídlo",
        "cena": "72.00",
        "komponenty": [("Buchtičky s vanilkovým krémem", "1.0")],
        "sk_bezmasy_pokrm": True,
        "sk_sladky_pokrm": True,
        "sk_dezert_s_volnym_cukrem": True,
    },
    {
        "nazev": "Tvarohový krém s jablky",
        "druh": "Dezert",
        "cena": "34.00",
        "komponenty": [("Tvarohový krém s jablky", "1.0")],
        "sk_dezert_s_volnym_cukrem": True,
    },
]


JIDELNICEK_PLAN = [
    ("Polévka", "Bramboračka"),
    ("Hlavní jídlo", "Svíčková na smetaně s houskovým knedlíkem"),
    ("Dezert", "Jogurt s jablkem"),

    ("Polévka", "Čočková polévka"),
    ("Hlavní jídlo", "Rybí filé s vařenými bramborami"),
    ("Dezert", "Tvarohový krém s jablky"),

    ("Polévka", "Zeleninová polévka s kapáním"),
    ("Hlavní jídlo", "Pečené kuřecí stehno s bramborovou kaší"),
    ("Dezert", "Krupicová kaše"),

    ("Polévka", "Hráškový krém"),
    ("Hlavní jídlo", "Čočka na kyselo s vejcem"),
    ("Dezert", "Jogurt s jablkem"),

    ("Polévka", "Kuřecí vývar s nudlemi"),
    ("Hlavní jídlo", "Buchtičky s vanilkovým krémem"),
    ("Dezert", "Tvarohový krém s jablky"),
]


def _komponenty(*nazvy):
    return [(nazev, "1.0") for nazev in nazvy]


def _pridej_generovana_jidla():
    existujici = {jidlo["nazev"] for jidlo in JIDLA}

    def add_jidlo(nazev, cena, komponenty, **flags):
        if nazev in existujici:
            return
        data = {
            "nazev": nazev,
            "druh": "Hlavní jídlo",
            "cena": cena,
            "komponenty": komponenty,
        }
        data.update(flags)
        JIDLA.append(data)
        existujici.add(nazev)

    omacky = [
        ("Rajská omáčka", "rajské omáčce", "90.00"),
        ("Svíčková omáčka", "smetanové omáčce", "96.00"),
        ("Koprová omáčka", "koprové omáčce", "86.00"),
    ]
    proteiny = [
        ("Hovězí vařené", "hovězím masem", {"sk_cervene_maso": True}),
        ("Masové kuličky", "masovými kuličkami", {"sk_cervene_maso": True}),
        ("Pečené kuřecí stehno", "kuřecím masem", {"sk_bile_maso": True}),
        ("Vařené vejce", "vejcem", {"sk_bezmasy_pokrm": True}),
    ]
    prilohy = [
        ("Houskový knedlík - porce", "houskovým knedlíkem"),
        ("Karlovarský knedlík - porce", "karlovarským knedlíkem"),
        ("Těstoviny vařené - porce", "těstovinami"),
        ("Rýže vařená - porce", "rýží"),
        ("Vařené brambory", "vařenými bramborami"),
        ("Bramborová kaše", "bramborovou kaší"),
    ]
    salaty = [
        (None, ""),
        ("Okurkový salát", " a okurkovým salátem"),
        ("Rajčatový salát", " a rajčatovým salátem"),
    ]

    for omacka_komp, omacka_text, cena in omacky:
        for protein_komp, protein_text, flags in proteiny:
            for priloha_komp, priloha_text in prilohy:
                for salat_komp, salat_text in salaty:
                    komponenty = [omacka_komp, protein_komp, priloha_komp]
                    if salat_komp:
                        komponenty.append(salat_komp)
                    add_jidlo(
                        f"{protein_text.capitalize()} v {omacka_text} s {priloha_text}{salat_text}",
                        cena,
                        _komponenty(*komponenty),
                        **flags,
                    )

    hlavni_kombinace = [
        ("Kuře na paprice", "Kuře na paprice", {"sk_bile_maso": True}, "89.00"),
        ("Pečené kuřecí stehno", "Pečené kuřecí stehno", {"sk_bile_maso": True}, "92.00"),
        ("Rybí filé pečené", "Rybí filé", {"sk_rybi_pokrm": True}, "92.00"),
        ("Špenát dušený", "Špenát s vejcem", {"sk_bezmasy_pokrm": True}, "78.00"),
        ("Čočka na kyselo", "Čočka na kyselo", {"sk_bezmasy_pokrm": True}, "78.00"),
        ("Bulgur se zeleninou", "Bulgur se zeleninou", {"sk_bezmasy_pokrm": True}, "78.00"),
        ("Kuskus se zeleninou a sýrem", "Kuskus se zeleninou a sýrem", {"sk_bezmasy_pokrm": True}, "82.00"),
        ("Zapečené těstoviny se sýrem", "Zapečené těstoviny se sýrem", {"sk_bezmasy_pokrm": True}, "82.00"),
        ("Zeleninové rizoto", "Zeleninové rizoto", {"sk_bezmasy_pokrm": True}, "76.00"),
    ]

    prilohy_navic = [
        ("Těstoviny vařené - porce", "těstovinami"),
        ("Rýže vařená - porce", "rýží"),
        ("Vařené brambory", "vařenými bramborami"),
        ("Bramborová kaše", "bramborovou kaší"),
        ("Houskový knedlík - porce", "houskovým knedlíkem"),
        ("Karlovarský knedlík - porce", "karlovarským knedlíkem"),
    ]
    oblohy = [
        (None, ""),
        ("Okurkový salát", " s okurkovým salátem"),
        ("Rajčatový salát", " s rajčatovým salátem"),
        ("Špenát dušený", " se špenátem"),
    ]

    for zaklad_komp, zaklad_text, flags, cena in hlavni_kombinace:
        for priloha_komp, priloha_text in prilohy_navic:
            for obloha_komp, obloha_text in oblohy:
                komponenty = [zaklad_komp, priloha_komp]
                if obloha_komp and obloha_komp != zaklad_komp:
                    komponenty.append(obloha_komp)
                add_jidlo(
                    f"{zaklad_text} s {priloha_text}{obloha_text}",
                    cena,
                    _komponenty(*komponenty),
                    **flags,
                )

    sladke_zaklady = [
        ("Žemlovka s jablky", "Žemlovka s jablky", "72.00"),
        ("Buchtičky s vanilkovým krémem", "Buchtičky s vanilkovým krémem", "72.00"),
        ("Krupicová kaše", "Krupicová kaše", "64.00"),
        ("Tvarohový krém s jablky", "Tvarohový nákyp s jablky", "70.00"),
    ]
    polevky = [
        "Bramboračka",
        "Kuřecí vývar",
        "Zeleninová polévka s kapáním",
        "Čočková polévka",
        "Hráškový krém",
    ]
    dezerty = [
        None,
        "Jogurt s jablkem",
        "Tvarohový krém s jablky",
    ]

    for sladky_komp, sladky_text, cena in sladke_zaklady:
        for polevka in polevky:
            for dezert in dezerty:
                komponenty = [polevka, sladky_komp]
                suffix = f" po {polevka.lower()}"
                if dezert:
                    komponenty.append(dezert)
                    suffix += f" a {dezert.lower()}"
                add_jidlo(
                    f"{sladky_text}{suffix}",
                    cena,
                    _komponenty(*komponenty),
                    sk_bezmasy_pokrm=True,
                    sk_sladky_pokrm=True,
                    sk_dezert_s_volnym_cukrem=True,
                )

    katalogove_pridavky = [
        ("se zeleninovou oblohou", "Okurkový salát"),
        ("s rajčatovou oblohou", "Rajčatový salát"),
        ("se špenátovou oblohou", "Špenát dušený"),
    ]
    zakladni_jidla = list(JIDLA)
    for jidlo in zakladni_jidla:
        if jidlo["druh"] != "Hlavní jídlo":
            continue
        for suffix, komponenta in katalogove_pridavky:
            if len(JIDLA) >= 540:
                break
            komponenty = list(jidlo["komponenty"])
            if komponenta not in {nazev for nazev, _ in komponenty}:
                komponenty.append((komponenta, "1.0"))
            flags = {
                key: value
                for key, value in jidlo.items()
                if key.startswith("sk_")
            }
            add_jidlo(
                f"{jidlo['nazev']} {suffix}",
                jidlo["cena"],
                komponenty,
                **flags,
            )
        if len(JIDLA) >= 540:
            break


_pridej_generovana_jidla()


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
                "skupina_sk": SKUPINY_SK_2025_MAP.get(
                    item["skupina_sk"],
                    Surovina.SK_NEZAPOCITAVA_SE,
                ),
                "koeficient_sk": Decimal("1.0000"),
                "koeficient_ciste_hmotnosti_sk": Decimal("1.0000"),
                "koeficient_zapoctu_sk": Decimal("1.0000"),
                "prumerna_cena_za_jednotku": Decimal(item["cena"]),
            }
            if item.get("hmotnost_ks_g"):
                defaults["hmotnost_ks_g"] = Decimal(item["hmotnost_ks_g"])
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
            defaults = {
                "druh": druhy[item["druh"]],
                "cena": Decimal(item["cena"]),
                "ikona": "",
            }
            for flag in (
                "sk_rybi_pokrm",
                "sk_bezmasy_pokrm",
                "sk_bile_maso",
                "sk_cervene_maso",
                "sk_sladky_pokrm",
                "sk_jemne_pecivo",
                "sk_dezert_s_volnym_cukrem",
                "sk_slazeny_napoj",
            ):
                defaults[flag] = bool(item.get(flag, False))
            jidlo, _ = Jidlo.objects.update_or_create(
                nazev=item["nazev"],
                defaults=defaults,
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
            protected_menu_item_ids = set(
                OrderItem.objects
                .filter(menu_item__jidelnicek__platnost_od=start, menu_item__jidelnicek__platnost_do=end)
                .values_list("menu_item_id", flat=True)
            )
            for menu in Jidelnicek.objects.filter(platnost_od=start, platnost_do=end):
                menu.polozky.exclude(id__in=protected_menu_item_ids).delete()
                if not menu.polozky.exists():
                    menu.delete()

        jidelnicek, _ = Jidelnicek.objects.get_or_create(
            platnost_od=start,
            platnost_do=end,
            defaults={"ikona": "bi-calendar-week"},
        )

        if jidelnicek.ikona != "bi-calendar-week":
            jidelnicek.ikona = "bi-calendar-week"
            jidelnicek.save(update_fields=["ikona"])

        idx = 0
        for den_offset in range(5):
            for _ in range(3):  # polévka + hlavní + dezert
                druh_nazev, jidlo_nazev = JIDELNICEK_PLAN[idx]
                PolozkaJidelnicku.objects.get_or_create(
                    jidelnicek=jidelnicek,
                    druh_jidla=druhy[druh_nazev],
                    jidlo=jidla[jidlo_nazev],
                )
                idx += 1
