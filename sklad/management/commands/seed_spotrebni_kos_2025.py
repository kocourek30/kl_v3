from decimal import Decimal

from django.core.management.base import BaseCommand

from sklad.models import NormaSpotrebnihoKose, Surovina, ToleranceSpotrebnihoKose
from users.models import StravovaciSkupina


SKUPINY = [
    Surovina.SK_MASO,
    Surovina.SK_RYBY,
    Surovina.SK_MLEKO,
    Surovina.SK_TUKY,
    Surovina.SK_CUKRY,
    Surovina.SK_ZELENINA_OVOCE,
    Surovina.SK_BRAMBORY,
    Surovina.SK_CELOZRNNE,
    Surovina.SK_LUSTENINY,
]

NORMY_BEZNA_VYZIVA_2025 = {
    NormaSpotrebnihoKose.VEK_2_3: {
        NormaSpotrebnihoKose.TYP_SNIDANE: [5, 0, 98, 3, 3, 48, 0, 5, 0],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA: [3, 2, 59, 3, 2, 40, 0, 4, 1],
        NormaSpotrebnihoKose.TYP_OBED: [26, 6, 44, 7, 6, 94, 53, 11, 7],
        NormaSpotrebnihoKose.TYP_SVACINA: [3, 2, 30, 2, 1, 27, 0, 3, 1],
        NormaSpotrebnihoKose.TYP_VECERE: [15, 3, 65, 4, 4, 58, 43, 6, 4],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA_OBED_SVACINA: [32, 10, 133, 12, 9, 161, 53, 18, 9],
        NormaSpotrebnihoKose.TYP_CELODENNI: [52, 13, 296, 19, 16, 267, 96, 29, 13],
    },
    NormaSpotrebnihoKose.VEK_4_6: {
        NormaSpotrebnihoKose.TYP_SNIDANE: [8, 0, 147, 5, 5, 72, 0, 8, 0],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA: [4, 3, 89, 4, 4, 60, 0, 7, 2],
        NormaSpotrebnihoKose.TYP_OBED: [39, 9, 67, 10, 8, 140, 79, 14, 9],
        NormaSpotrebnihoKose.TYP_SVACINA: [4, 2, 44, 4, 2, 41, 0, 4, 2],
        NormaSpotrebnihoKose.TYP_VECERE: [23, 5, 98, 6, 5, 87, 65, 10, 6],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA_OBED_SVACINA: [47, 14, 200, 18, 14, 241, 79, 25, 13],
        NormaSpotrebnihoKose.TYP_CELODENNI: [78, 19, 445, 29, 24, 400, 144, 43, 19],
    },
    NormaSpotrebnihoKose.VEK_7_10: {
        NormaSpotrebnihoKose.TYP_SNIDANE: [8, 0, 171, 6, 5, 84, 0, 9, 0],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA: [5, 3, 104, 5, 4, 69, 0, 8, 2],
        NormaSpotrebnihoKose.TYP_OBED: [46, 11, 78, 12, 10, 162, 92, 17, 11],
        NormaSpotrebnihoKose.TYP_SVACINA: [5, 2, 52, 4, 3, 48, 0, 5, 2],
        NormaSpotrebnihoKose.TYP_VECERE: [27, 6, 114, 7, 6, 102, 76, 11, 7],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA_OBED_SVACINA: [56, 16, 234, 21, 17, 279, 92, 30, 15],
        NormaSpotrebnihoKose.TYP_CELODENNI: [91, 22, 519, 34, 28, 465, 168, 50, 22],
    },
    NormaSpotrebnihoKose.VEK_11_14: {
        NormaSpotrebnihoKose.TYP_SNIDANE: [10, 0, 196, 7, 6, 96, 0, 10, 0],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA: [6, 4, 119, 6, 5, 80, 0, 9, 2],
        NormaSpotrebnihoKose.TYP_OBED: [52, 13, 89, 13, 11, 187, 106, 20, 13],
        NormaSpotrebnihoKose.TYP_SVACINA: [6, 3, 59, 4, 3, 54, 0, 6, 2],
        NormaSpotrebnihoKose.TYP_VECERE: [30, 6, 130, 8, 7, 117, 86, 13, 9],
        NormaSpotrebnihoKose.TYP_CELODENNI: [104, 26, 593, 38, 32, 534, 192, 58, 26],
    },
    NormaSpotrebnihoKose.VEK_15_PLUS: {
        NormaSpotrebnihoKose.TYP_SNIDANE: [12, 0, 245, 9, 7, 120, 0, 13, 0],
        NormaSpotrebnihoKose.TYP_PRESNIDAVKA: [7, 5, 148, 6, 6, 100, 0, 11, 3],
        NormaSpotrebnihoKose.TYP_OBED: [65, 16, 111, 17, 14, 233, 132, 25, 15],
        NormaSpotrebnihoKose.TYP_SVACINA: [7, 3, 74, 5, 4, 67, 0, 7, 3],
        NormaSpotrebnihoKose.TYP_VECERE: [39, 8, 163, 11, 9, 147, 108, 16, 11],
        NormaSpotrebnihoKose.TYP_CELODENNI: [130, 32, 741, 48, 40, 667, 240, 72, 32],
    },
}

TOLERANCE_2025 = {
    Surovina.SK_MASO: (75, 125),
    Surovina.SK_RYBY: (75, None),
    Surovina.SK_MLEKO: (75, 125),
    Surovina.SK_TUKY: (75, 100),
    Surovina.SK_CUKRY: (0, 100),
    Surovina.SK_ZELENINA_OVOCE: (75, None),
    Surovina.SK_BRAMBORY: (75, 125),
    Surovina.SK_CELOZRNNE: (75, None),
    Surovina.SK_LUSTENINY: (75, None),
}

LEGACY_SKUPINY_MAP = {
    "brambory": Surovina.SK_BRAMBORY,
    "cukr": Surovina.SK_CUKRY,
    "maso": Surovina.SK_MASO,
    "mleko": Surovina.SK_MLEKO,
    "obiloviny": Surovina.SK_CELOZRNNE,
    "ovoce": Surovina.SK_ZELENINA_OVOCE,
    "tuky": Surovina.SK_TUKY,
    "zelenina": Surovina.SK_ZELENINA_OVOCE,
}


class Command(BaseCommand):
    help = "Naplní legislativní normy a tolerance spotřebního koše podle nové vyhlášky 2025."

    def handle(self, *args, **options):
        normy = 0
        for vek, typy in NORMY_BEZNA_VYZIVA_2025.items():
            for typ_jidla, hodnoty in typy.items():
                for skupina, hodnota in zip(SKUPINY, hodnoty):
                    NormaSpotrebnihoKose.objects.update_or_create(
                        stravovaci_skupina=None,
                        vekova_kategorie=vek,
                        typ_jidla=typ_jidla,
                        skupina_sk=skupina,
                        defaults={
                            "norma_g_den": Decimal(str(hodnota)),
                            "norma_g_mesic": Decimal("0"),
                        },
                    )
                    normy += 1

        tolerance = 0
        for skupina, (minimum, maximum) in TOLERANCE_2025.items():
            ToleranceSpotrebnihoKose.objects.update_or_create(
                stravovaci_skupina=None,
                skupina_sk=skupina,
                defaults={
                    "min_pct": Decimal(str(minimum)),
                    "max_pct": Decimal(str(maximum)) if maximum is not None else None,
                },
            )
            tolerance += 1

        tolerance_skupiny = 0
        for stravovaci_skupina in StravovaciSkupina.objects.all():
            for skupina, (minimum, maximum) in TOLERANCE_2025.items():
                ToleranceSpotrebnihoKose.objects.update_or_create(
                    stravovaci_skupina=stravovaci_skupina,
                    skupina_sk=skupina,
                    defaults={
                        "min_pct": Decimal(str(minimum)),
                        "max_pct": Decimal(str(maximum)) if maximum is not None else None,
                    },
                )
                tolerance_skupiny += 1

        opravene_suroviny = 0
        for legacy_skupina, nova_skupina in LEGACY_SKUPINY_MAP.items():
            opravene_suroviny += Surovina.objects.filter(skupina_sk=legacy_skupina).update(
                skupina_sk=nova_skupina,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Spotřební koš 2025 naplněn: "
                f"{normy} norem, {tolerance} globálních tolerancí, "
                f"{tolerance_skupiny} tolerancí pro stravovací skupiny, "
                f"{opravene_suroviny} surovin převedeno ze starých skupin."
            )
        )
