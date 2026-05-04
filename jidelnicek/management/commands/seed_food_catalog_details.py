from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from jidelnicek.models import Alergen, Jidlo


COMMON_ALLERGENS = [
    ("1 Obiloviny obsahující lepek", "fa-solid fa-wheat-awn"),
    ("3 Vejce", "fa-solid fa-egg"),
    ("4 Ryby", "fa-solid fa-fish"),
    ("6 Sójové boby", "fa-solid fa-seedling"),
    ("7 Mléko", "fa-solid fa-cow"),
    ("8 Skořápkové plody", "fa-solid fa-seedling"),
    ("9 Celer", "fa-solid fa-carrot"),
    ("10 Hořčice", "fa-solid fa-jar"),
]

DEFAULT_NUTRITION_BY_TYPE = {
    "Snídaně": {"kcal": Decimal("365.00"), "b": Decimal("15.00"), "t": Decimal("13.00"), "s": Decimal("39.00")},
    "1. Svačina": {"kcal": Decimal("185.00"), "b": Decimal("5.00"), "t": Decimal("5.00"), "s": Decimal("27.00")},
    "Oběd": {"kcal": Decimal("670.00"), "b": Decimal("31.00"), "t": Decimal("24.00"), "s": Decimal("70.00")},
    "2. Svačina": {"kcal": Decimal("210.00"), "b": Decimal("7.00"), "t": Decimal("7.00"), "s": Decimal("28.00")},
    "Večeře": {"kcal": Decimal("520.00"), "b": Decimal("24.00"), "t": Decimal("17.00"), "s": Decimal("51.00")},
    "2. Večeře": {"kcal": Decimal("145.00"), "b": Decimal("7.00"), "t": Decimal("4.00"), "s": Decimal("17.00")},
}

KEYWORD_RULES = [
    {
        "keywords": ("guláš", "svíčková", "rajská", "řízek", "sekaná"),
        "nutrition": {"kcal": Decimal("710.00"), "b": Decimal("34.00"), "t": Decimal("28.00"), "s": Decimal("73.00")},
        "allergens": {"1 Obiloviny obsahující lepek", "7 Mléko", "9 Celer"},
    },
    {
        "keywords": ("rybí", "tuňáka", "tuňák"),
        "nutrition": {"kcal": Decimal("430.00"), "b": Decimal("27.00"), "t": Decimal("17.00"), "s": Decimal("36.00")},
        "allergens": {"4 Ryby"},
    },
    {
        "keywords": ("vejce", "vaječná", "míchaná vejce"),
        "nutrition": {"kcal": Decimal("330.00"), "b": Decimal("18.00"), "t": Decimal("19.00"), "s": Decimal("20.00")},
        "allergens": {"3 Vejce", "1 Obiloviny obsahující lepek"},
    },
    {
        "keywords": ("jogurt", "lučina", "sýr", "tvaroh", "pudink", "kefír", "mlé", "kaše"),
        "nutrition": {"kcal": Decimal("240.00"), "b": Decimal("10.00"), "t": Decimal("8.00"), "s": Decimal("28.00")},
        "allergens": {"7 Mléko"},
    },
    {
        "keywords": ("rohlík", "chléb", "bageta", "toast", "croissant", "pečivo", "toustový"),
        "nutrition": {"kcal": Decimal("290.00"), "b": Decimal("8.00"), "t": Decimal("7.00"), "s": Decimal("48.00")},
        "allergens": {"1 Obiloviny obsahující lepek"},
    },
    {
        "keywords": ("müsli", "oves", "tyčinka", "sušenka"),
        "nutrition": {"kcal": Decimal("220.00"), "b": Decimal("5.00"), "t": Decimal("7.00"), "s": Decimal("33.00")},
        "allergens": {"1 Obiloviny obsahující lepek", "8 Skořápkové plody"},
    },
    {
        "keywords": ("hořčice",),
        "nutrition": None,
        "allergens": {"10 Hořčice"},
    },
    {
        "keywords": ("tofu", "soja", "sój", "tempeh"),
        "nutrition": {"kcal": Decimal("250.00"), "b": Decimal("16.00"), "t": Decimal("12.00"), "s": Decimal("16.00")},
        "allergens": {"6 Sójové boby"},
    },
]


class Command(BaseCommand):
    help = "Doplní katalog jídel o běžné alergeny a orientační nutriční hodnoty."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-empty",
            action="store_true",
            help="Doplní jen chybějící nutriční hodnoty a ponechá existující čísla beze změny.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        only_empty = options["only_empty"]
        allergens = self._seed_allergens()

        updated_foods = 0
        updated_allergen_links = 0

        for food in Jidlo.objects.select_related("druh").all():
            nutrition = self._nutrition_for_food(food)
            allergen_names = self._allergens_for_food(food)
            selected_allergens = [allergens[name] for name in allergen_names if name in allergens]

            changed_fields = []
            for field_name, value in (
                ("kcal", nutrition["kcal"]),
                ("bílkoviny", nutrition["b"]),
                ("tuky", nutrition["t"]),
                ("sacharidy", nutrition["s"]),
            ):
                current = getattr(food, field_name)
                if only_empty and current is not None:
                    continue
                if current != value:
                    setattr(food, field_name, value)
                    changed_fields.append(field_name)

            if changed_fields:
                food.save(update_fields=changed_fields)
                updated_foods += 1

            current_ids = set(food.alergeny.values_list("id", flat=True))
            target_ids = {allergen.id for allergen in selected_allergens}
            if target_ids and current_ids != target_ids:
                food.alergeny.set(selected_allergens)
                updated_allergen_links += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Katalog jídel doplněn. Nutriční hodnoty upraveny u {updated_foods} jídel, alergeny aktualizovány u {updated_allergen_links} jídel."
            )
        )

    def _seed_allergens(self) -> dict[str, Alergen]:
        allergens = {}
        for name, icon in COMMON_ALLERGENS:
            allergen, _ = Alergen.objects.update_or_create(
                nazev=name,
                defaults={"ikona": icon},
            )
            allergens[name] = allergen
        return allergens

    def _nutrition_for_food(self, food: Jidlo) -> dict[str, Decimal]:
        meal_type_name = food.druh.nazev if food.druh_id else None
        nutrition = dict(DEFAULT_NUTRITION_BY_TYPE.get(meal_type_name, DEFAULT_NUTRITION_BY_TYPE["Oběd"]))
        lower_name = food.nazev.lower()

        for rule in KEYWORD_RULES:
            if any(keyword in lower_name for keyword in rule["keywords"]) and rule["nutrition"]:
                nutrition.update(rule["nutrition"])

        if any(keyword in lower_name for keyword in ("ovoce", "jablko", "hruška", "banán")):
            nutrition = {"kcal": Decimal("135.00"), "b": Decimal("2.00"), "t": Decimal("1.00"), "s": Decimal("30.00")}
        elif any(keyword in lower_name for keyword in ("salát", "kuskus", "rizoto")):
            nutrition = {"kcal": Decimal("460.00"), "b": Decimal("18.00"), "t": Decimal("13.00"), "s": Decimal("59.00")}

        return nutrition

    def _allergens_for_food(self, food: Jidlo) -> set[str]:
        lower_name = food.nazev.lower()
        allergen_names: set[str] = set()

        meal_type_name = food.druh.nazev if food.druh_id else ""
        if meal_type_name in {"Snídaně", "2. Svačina", "Večeře"}:
            if any(keyword in lower_name for keyword in ("rohlík", "chléb", "bageta", "toast", "croissant", "pečivo", "toustový")):
                allergen_names.add("1 Obiloviny obsahující lepek")

        for rule in KEYWORD_RULES:
            if any(keyword in lower_name for keyword in rule["keywords"]):
                allergen_names.update(rule["allergens"])

        if any(keyword in lower_name for keyword in ("kuřecí řízek", "smažený", "květák")):
            allergen_names.update({"1 Obiloviny obsahující lepek", "3 Vejce"})

        if any(keyword in lower_name for keyword in ("svíčková", "rajská", "hovězí na česneku")):
            allergen_names.add("9 Celer")

        if "máslem" in lower_name or "máslo" in lower_name:
            allergen_names.add("7 Mléko")

        if not allergen_names:
            if meal_type_name in {"Snídaně", "2. Svačina"}:
                allergen_names.add("1 Obiloviny obsahující lepek")
            elif meal_type_name == "2. Večeře" and any(keyword in lower_name for keyword in ("jogurt", "cottage", "pudink")):
                allergen_names.add("7 Mléko")

        return allergen_names
