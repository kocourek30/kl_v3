from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
import struct
import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from jidelnicek.models import Alergen, DruhJidla, Jidlo
from sklad.models import (
    Dodavatel,
    NormaSpotrebnihoKose,
    PohybSkladu,
    PolozkaPrijmu,
    PrijemSkladu,
    RecepturaPolozka,
    StavSkladu,
    Surovina,
    ToleranceSpotrebnihoKose,
)
from users.models import StravovaciSkupina


DBF_ENCODING = "cp852"
DATAX_RECEIPT_PREFIX = "DATAx import QHK01"
DATAX_MOVEMENT_PREFIX = "DATAx import QHK10"
DATAX_HISTORIC_SUPPLIER = "DATAx historický import"

DATAX_KOS_TO_SK = {
    "BR": Surovina.SK_BRAMBORY,
    "CU": Surovina.SK_CUKRY,
    "LU": Surovina.SK_LUSTENINY,
    "MA": Surovina.SK_MASO,
    "ML": Surovina.SK_MLEKO,
    "MV": Surovina.SK_MLEKO,
    "MR": Surovina.SK_LUSTENINY,
    "OV": Surovina.SK_ZELENINA_OVOCE,
    "RY": Surovina.SK_RYBY,
    "TU": Surovina.SK_TUKY,
    "VE": Surovina.SK_NEZAPOCITAVA_SE,
    "ZE": Surovina.SK_ZELENINA_OVOCE,
}

LEGISLATIVNI_TOLERANCE_SK_2025 = {
    Surovina.SK_MASO: (Decimal("75"), Decimal("125")),
    Surovina.SK_RYBY: (Decimal("75"), None),
    Surovina.SK_MLEKO: (Decimal("75"), Decimal("125")),
    Surovina.SK_TUKY: (Decimal("75"), Decimal("100")),
    Surovina.SK_CUKRY: (Decimal("0"), Decimal("100")),
    Surovina.SK_ZELENINA_OVOCE: (Decimal("75"), None),
    Surovina.SK_BRAMBORY: (Decimal("75"), Decimal("125")),
    Surovina.SK_CELOZRNNE: (Decimal("75"), None),
    Surovina.SK_LUSTENINY: (Decimal("75"), None),
}


@dataclass(frozen=True)
class DbfField:
    name: str
    field_type: str
    length: int
    decimal_count: int
    offset: int


class Command(BaseCommand):
    help = (
        "Importuje živá DATAx DBF data do Klikni jídlo z větve "
        "Datax\\2019_11_2019\\Kuch\\DBF. Zahrnuje katalog jídel, suroviny, "
        "stavy skladu, alergeny, receptury, historické příjemky/pohyby a "
        "normy spotřebního koše."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-dir",
            default=r"Datax\2019_11_2019\Kuch\DBF",
            help="Zdrojový adresář s živými DATAx DBF soubory.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Pouze vypíše validaci a plán změn bez zápisu do DB.",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source_dir"])
        dry_run = options["dry_run"]

        if not source_dir.exists():
            raise CommandError(f"Zdrojový adresář neexistuje: {source_dir}")

        required = {
            "JIDLA": source_dir / "JIDLA.DBF",
            "RECEPTY": source_dir / "RECEPTY.DBF",
            "ALERGEN": source_dir / "ALERGEN.DBF",
            "ALERJID": source_dir / "ALERJID.DBF",
            "QHK04": source_dir / "QHK04.DBF",
        }
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            raise CommandError(f"Chybí povinné DBF soubory: {', '.join(missing)}")

        jidla_rows = self._read_dbf_rows(required["JIDLA"])
        recepty_rows = self._read_dbf_rows(required["RECEPTY"])
        alergen_rows = self._read_dbf_rows(required["ALERGEN"])
        alerjid_rows = self._read_dbf_rows(required["ALERJID"])
        qhk04_rows = self._read_dbf_rows(required["QHK04"])

        alerskl_rows = self._read_optional_dbf(source_dir / "ALERSKL.DBF")
        nutrie_rows = self._read_optional_dbf(source_dir / "NUTRIE.DBF")
        porce_rows = self._read_optional_dbf(source_dir / "PORCE.DBF")
        mista_rows = self._read_optional_dbf(source_dir / "MISTA01.DBF")
        qhk01_rows = self._read_optional_dbf(source_dir / "QHK01.DBF")
        qhk10_rows = self._read_optional_dbf(source_dir / "QHK10.DBF")

        jidla_rows = [
            row for row in jidla_rows
            if (row.get("KOD") or "").strip() and not self._is_deleted(row.get("ZRUSEN"))
        ]
        recepty_rows = [
            row for row in recepty_rows
            if (row.get("KOD") or "").strip()
            and (row.get("CISLO") or "").strip()
            and not self._is_deleted(row.get("ZRUSEN"))
        ]
        qhk04_rows = [
            row for row in qhk04_rows
            if (row.get("CISLO") or "").strip() and (row.get("NAZEV") or "").strip()
        ]
        alergen_rows = [
            row for row in alergen_rows
            if (row.get("KOD") or "").strip() and (row.get("NAZEV") or "").strip()
        ]
        alerjid_rows = [
            row for row in alerjid_rows
            if (row.get("RECEPT") or "").strip() and (row.get("KOD") or "").strip()
        ]

        existing_foods = list(Jidlo.objects.select_related("druh").all())
        existing_foods_by_slug = {
            self._slugify(food.nazev): food
            for food in existing_foods
            if self._slugify(food.nazev)
        }
        existing_ingredients = list(Surovina.objects.all())
        existing_ingredients_by_name = {
            ingredient.nazev.strip().lower(): ingredient
            for ingredient in existing_ingredients
        }
        existing_allergens = list(Alergen.objects.all())
        existing_allergens_by_name = {
            allergen.nazev.strip().lower(): allergen
            for allergen in existing_allergens
        }

        nutrie_lookup = self._build_nutrie_lookup(nutrie_rows)
        ingredient_catalog = self._build_ingredient_catalog(qhk04_rows, nutrie_lookup)
        meal_catalog = self._build_meal_catalog(jidla_rows)
        allergen_catalog = self._build_allergen_catalog(alergen_rows)
        partner_catalog = self._build_partner_catalog(qhkc01_rows=mista_rows or self._read_optional_dbf(source_dir / "QHKC01.DBF"), mkodber_rows=self._read_optional_dbf(source_dir / "MKODBER.DBF"))
        receipt_plan = self._build_receipt_targets(qhk01_rows, ingredient_catalog)
        movement_plan = self._build_movement_targets(qhk10_rows, ingredient_catalog)
        norma_targets = self._build_norma_targets(nutrie_rows, porce_rows)

        recipe_targets, recipe_missing_meals, recipe_missing_ingredients = self._prepare_recipe_targets(
            recepty_rows=recepty_rows,
            meal_catalog=meal_catalog,
            ingredient_catalog=ingredient_catalog,
            existing_foods_by_slug=existing_foods_by_slug,
        )
        allergen_targets, allergen_missing_meals, allergen_missing_codes = self._prepare_allergen_targets(
            alerjid_rows=alerjid_rows,
            meal_catalog=meal_catalog,
            allergen_catalog=allergen_catalog,
            existing_foods_by_slug=existing_foods_by_slug,
        )

        suroviny_to_create, suroviny_to_update = self._plan_suroviny(
            ingredient_catalog,
            existing_ingredients_by_name,
        )
        jidla_to_create, jidla_to_update = self._plan_jidla(
            meal_catalog,
            existing_foods_by_slug,
        )
        alergeny_to_create = [
            data for key, data in allergen_catalog.items()
            if key not in existing_allergens_by_name
        ]

        self.stdout.write(self.style.NOTICE(f"Zdroj: {source_dir}"))
        self.stdout.write(
            self.style.NOTICE(
                "DATAx přehled: "
                f"jídla={len(jidla_rows)}, "
                f"receptury={len(recepty_rows)}, "
                f"suroviny={len(ingredient_catalog)}, "
                f"alergeny={len(alergen_rows)}, "
                f"vazby alergenů={len(alerjid_rows)}, "
                f"ALERSKL={len(alerskl_rows)}, "
                f"NUTRIE={len(nutrie_rows)}, "
                f"PORCE={len(porce_rows)}, "
                f"MISTA01={len(mista_rows)}, "
                f"QHK01={len(qhk01_rows)}, "
                f"QHK10={len(qhk10_rows)}"
            )
        )
        self.stdout.write(
            self.style.NOTICE(
                "Plán importu do Klikni jídlo: "
                f"nové suroviny={len(suroviny_to_create)}, aktualizace surovin={len(suroviny_to_update)}, "
                f"nová jídla={len(jidla_to_create)}, aktualizace jídel={len(jidla_to_update)}, "
                f"nové alergeny={len(alergeny_to_create)}, "
                f"recepturové vazby jídel={len(recipe_targets)}, "
                f"alergenové vazby jídel={len(allergen_targets)}, "
                f"historické příjemky={len(receipt_plan)}, "
                f"historické příjemkové položky={sum(len(item['lines']) for item in receipt_plan.values())}, "
                f"historické skladové pohyby={len(movement_plan)}, "
                f"partneři a střediska={len(partner_catalog)}, "
                f"normy spotřebního koše={len(norma_targets)}, "
                f"tolerance spotřebního koše={len(LEGISLATIVNI_TOLERANCE_SK_2025)}"
            )
        )
        self.stdout.write(
            self.style.WARNING(
                "Nezmapované/odložené vrstvy pro další iteraci: "
                f"chybějící jídla v recepturách={recipe_missing_meals}, "
                f"chybějící suroviny v recepturách={recipe_missing_ingredients}, "
                f"chybějící jídla v alergenech={allergen_missing_meals}, "
                f"chybějící kódy alergenů={allergen_missing_codes}. "
                "MISTA01 a jemnější cenové/finanční vazby ponechávám zatím jako další vrstvu."
            )
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: žádné změny se neuloží."))
            return

        with transaction.atomic():
            ingredient_objects_by_code = self._import_suroviny(
                ingredient_catalog,
                existing_ingredients_by_name,
            )
            partners_written = self._import_partners(partner_catalog)
            allergen_objects_by_code = self._import_alergeny(
                allergen_catalog,
                existing_allergens_by_name,
            )
            food_objects_by_code = self._import_jidla(
                meal_catalog,
                existing_foods_by_slug,
            )
            recipes_written = self._import_receptury(
                recipe_targets,
                food_objects_by_code,
                ingredient_objects_by_code,
            )
            allergen_links_written = self._import_vazby_alergenu(
                allergen_targets,
                food_objects_by_code,
                allergen_objects_by_code,
            )
            normy_written = self._import_normy(norma_targets)
            tolerance_written = self._import_tolerance()
            receipts_written, receipt_lines_written, receipt_movements_written = self._import_historicke_prijemky(
                receipt_plan,
                ingredient_objects_by_code,
            )
            movements_written = self._import_historicke_pohyby(
                movement_plan,
                ingredient_objects_by_code,
            )

        self.stdout.write(
            self.style.SUCCESS(
                "DATAx skladový import dokončen: "
                f"suroviny={len(ingredient_objects_by_code)}, "
                f"jídla={len(food_objects_by_code)}, "
                f"receptury_zapsáno={recipes_written}, "
                f"vazby_alergenů_zapsáno={allergen_links_written}, "
                f"normy_zapsáno={normy_written}, "
                f"tolerance_zapsáno={tolerance_written}, "
                f"historické_příjemky={receipts_written}, "
                f"historické_příjemkové_položky={receipt_lines_written}, "
                f"historické_příjmové_pohyby={receipt_movements_written}, "
                f"historické_výdejové_pohyby={movements_written}, "
                f"partneři_zapsáno={partners_written}"
            )
        )

    def _plan_suroviny(
        self,
        ingredient_catalog: dict[str, dict],
        existing_by_name: dict[str, Surovina],
    ) -> tuple[list[dict], list[dict]]:
        to_create: list[dict] = []
        to_update: list[dict] = []
        for item in ingredient_catalog.values():
            existing = existing_by_name.get(item["nazev"].strip().lower())
            if existing is None:
                to_create.append(item)
                continue
            if (
                existing.jednotka != item["jednotka"]
                or (item["prumerna_cena_za_jednotku"] is not None and existing.prumerna_cena_za_jednotku != item["prumerna_cena_za_jednotku"])
                or (item["hmotnost_ks_g"] is not None and existing.hmotnost_ks_g != item["hmotnost_ks_g"])
                or existing.skupina_sk != item["skupina_sk"]
                or existing.koeficient_sk != item["koeficient_sk"]
                or existing.koeficient_ciste_hmotnosti_sk != item["koeficient_ciste_hmotnosti_sk"]
                or existing.koeficient_zapoctu_sk != item["koeficient_zapoctu_sk"]
                or existing.je_sterilovana_nebo_kompot != item["je_sterilovana_nebo_kompot"]
            ):
                to_update.append(item)
        return to_create, to_update

    def _plan_jidla(
        self,
        meal_catalog: dict[str, dict],
        existing_by_slug: dict[str, Jidlo],
    ) -> tuple[list[dict], list[dict]]:
        to_create: list[dict] = []
        to_update: list[dict] = []
        for item in meal_catalog.values():
            existing = existing_by_slug.get(item["slug"])
            if existing is None:
                to_create.append(item)
            elif item["cena"] is not None and existing.cena != item["cena"]:
                to_update.append(item)
        return to_create, to_update

    def _build_nutrie_lookup(self, rows: list[dict]) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        for row in rows:
            code = (row.get("CISLO") or "").strip().upper()
            if code:
                lookup[code] = row
        return lookup

    def _build_ingredient_catalog(self, rows: list[dict], nutrie_lookup: dict[str, dict]) -> dict[str, dict]:
        catalog: dict[str, dict] = {}
        for row in rows:
            code = (row.get("CISLO") or "").strip()
            name = (row.get("NAZEV") or "").strip()
            if not code or not name:
                continue

            unit = self._map_unit((row.get("MJ") or "").strip())
            price = self._parse_decimal(row.get("CENA"))
            stock = self._parse_decimal(row.get("ZASOBA"))
            if stock is None:
                prijato = self._parse_decimal(row.get("PRIJATO")) or Decimal("0")
                vydano = self._parse_decimal(row.get("VYDANO")) or Decimal("0")
                spotreba = self._parse_decimal(row.get("SPOTREBA")) or Decimal("0")
                stock = max(Decimal("0"), prijato - max(vydano, spotreba))

            weight = self._parse_decimal(row.get("GRAMAZ"))
            hmotnost_ks = None
            if unit == Surovina.JEDNOTKA_KS and weight and weight > 0:
                hmotnost_ks = weight

            nutrie_code = (row.get("KOS") or "").strip().upper()
            nutrie_data = self._map_nutrie_to_surovina(nutrie_lookup.get(nutrie_code), nutrie_code)

            catalog[code] = {
                "code": code,
                "nazev": name.title() if name.isupper() else name,
                "jednotka": unit,
                "prumerna_cena_za_jednotku": price,
                "mnozstvi": stock or Decimal("0"),
                "hmotnost_ks_g": hmotnost_ks,
                "datax_kos_code": nutrie_code,
                "skupina_sk": nutrie_data["skupina_sk"],
                "koeficient_sk": nutrie_data["koeficient_sk"],
                "koeficient_ciste_hmotnosti_sk": nutrie_data["koeficient_ciste_hmotnosti_sk"],
                "koeficient_zapoctu_sk": nutrie_data["koeficient_zapoctu_sk"],
                "je_sterilovana_nebo_kompot": nutrie_data["je_sterilovana_nebo_kompot"],
            }
        return catalog

    def _map_nutrie_to_surovina(self, nutrie_row: dict | None, nutrie_code: str) -> dict:
        normalized_code = (nutrie_code or "").strip().upper()
        base_code = (nutrie_row.get("KOD") if nutrie_row else "") or normalized_code
        base_code = str(base_code).strip().upper()
        koef = self._parse_decimal(nutrie_row.get("KOEF") if nutrie_row else None) or Decimal("1.0")
        skupina = DATAX_KOS_TO_SK.get(base_code, Surovina.SK_NEZAPOCITAVA_SE)
        return {
            "skupina_sk": skupina,
            "koeficient_sk": koef,
            "koeficient_ciste_hmotnosti_sk": Decimal("1.0"),
            "koeficient_zapoctu_sk": koef,
            "je_sterilovana_nebo_kompot": normalized_code in {"ZEM"},
        }

    def _build_meal_catalog(self, rows: list[dict]) -> dict[str, dict]:
        catalog: dict[str, dict] = {}
        for row in rows:
            code = (row.get("KOD") or "").strip()
            name = (row.get("NAZEV") or "").strip()
            if not code or not name:
                continue
            price = self._parse_decimal(row.get("PRUMER")) or Decimal("0.00")
            catalog[code] = {
                "code": code,
                "nazev": name,
                "slug": self._slugify(name),
                "cena": price,
                "skupina": (row.get("SKUPINA") or "").strip(),
            }
        return catalog

    def _build_allergen_catalog(self, rows: list[dict]) -> dict[str, dict]:
        catalog: dict[str, dict] = {}
        for row in rows:
            code = (row.get("KOD") or "").strip()
            name = (row.get("NAZEV") or "").strip()
            if code and name:
                catalog[code] = {"code": code, "nazev": name}
        return catalog

    def _build_partner_catalog(self, qhkc01_rows: list[dict], mkodber_rows: list[dict]) -> dict[str, dict]:
        catalog: dict[str, dict] = {}

        for row in qhkc01_rows:
            raw_name = ((row.get("PNAZEV") or "").strip() or (row.get("NAZEV") or "").strip())
            name = self._clean_datax_label(raw_name)
            normalized = self._normalize(name)
            if not name or normalized in {"", "bez blizsiho urceni"}:
                continue
            key = f"QHKC01::{normalized}"
            kod = (row.get("KOD") or "").strip()
            kod2 = (row.get("KOD2") or "").strip()
            analytika = (row.get("ANALYTIKA") or "").strip()
            catalog[key] = {
                "nazev": name,
                "ico": (row.get("ICO") or "").strip(),
                "dic": (row.get("DIC") or "").strip(),
                "adresa": self._clean_datax_label(self._join_nonempty(
                    [
                        (row.get("ULICE") or "").strip(),
                        " ".join(filter(None, [(row.get("PSC") or "").strip(), (row.get("OBEC") or "").strip()])),
                    ]
                )),
                "typ_subjektu": self._classify_partner_type(
                    source="QHKC01",
                    name=name,
                    ico=(row.get("ICO") or "").strip(),
                    code=kod,
                    code2=kod2,
                    analytika=analytika,
                ),
                "datax_zdroj": "DATAx QHKC01",
                "datax_kod": kod,
                "datax_kod2": kod2,
                "datax_analytika": analytika,
                "poznamka": self._join_nonempty(
                    [
                        "DATAx QHKC01 odběrové místo / středisko.",
                        f"Kód: {kod}",
                        f"Kód2: {kod2}",
                        f"Analytika: {analytika}",
                    ]
                ),
            }

        for row in mkodber_rows:
            name = self._clean_datax_label((row.get("NAZEV") or "").strip())
            normalized = self._normalize(name)
            if not name or normalized in {"", "bez blizsiho urceni"}:
                continue
            key = f"MKODBER::{normalized}"
            existing = catalog.get(key)
            ico = (row.get("ICO") or "").strip()
            payload = {
                "nazev": name,
                "ico": ico,
                "dic": "",
                "adresa": "",
                "typ_subjektu": self._classify_partner_type(
                    source="MKODBER",
                    name=name,
                    ico=ico,
                    code=(row.get("SKUP_P") or "").strip(),
                    code2="",
                    analytika="",
                ),
                "datax_zdroj": "DATAx MKODBER",
                "datax_kod": (row.get("SKUP_P") or "").strip(),
                "datax_kod2": "",
                "datax_analytika": "",
                "poznamka": self._join_nonempty(
                    [
                        "DATAx MKODBER profil odběratele / režimu.",
                        f"Skupina: {(row.get('SKUP_P') or '').strip()}",
                        f"Platný profil: {(row.get('PLATNY') or '').strip()}",
                    ]
                ),
            }
            if existing:
                if not existing["ico"] and payload["ico"]:
                    existing["ico"] = payload["ico"]
                if existing.get("typ_subjektu") == Dodavatel.TYP_STREDISKO and payload["typ_subjektu"] != Dodavatel.TYP_STREDISKO:
                    existing["typ_subjektu"] = payload["typ_subjektu"]
                existing["poznamka"] = self._join_nonempty([existing["poznamka"], payload["poznamka"]])
            else:
                catalog[key] = payload

        return catalog

    def _prepare_recipe_targets(
        self,
        *,
        recepty_rows: list[dict],
        meal_catalog: dict[str, dict],
        ingredient_catalog: dict[str, dict],
        existing_foods_by_slug: dict[str, Jidlo],
    ) -> tuple[dict[str, dict[str, Decimal]], int, int]:
        targets: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        missing_meals = 0
        missing_ingredients = 0

        for row in recepty_rows:
            meal_code = (row.get("KOD") or "").strip()
            ingredient_code = (row.get("CISLO") or "").strip()
            meal_data = meal_catalog.get(meal_code)
            ingredient_data = ingredient_catalog.get(ingredient_code)
            if not meal_data:
                missing_meals += 1
                continue
            if not ingredient_data:
                missing_ingredients += 1
                continue
            if meal_data["slug"] not in existing_foods_by_slug and meal_code not in meal_catalog:
                missing_meals += 1
                continue

            amount = self._parse_decimal(row.get("HMOTNOST")) or Decimal("0")
            if amount <= 0:
                continue
            targets[meal_code][ingredient_code] += amount

        return targets, missing_meals, missing_ingredients

    def _prepare_allergen_targets(
        self,
        *,
        alerjid_rows: list[dict],
        meal_catalog: dict[str, dict],
        allergen_catalog: dict[str, dict],
        existing_foods_by_slug: dict[str, Jidlo],
    ) -> tuple[dict[str, set[str]], int, int]:
        targets: dict[str, set[str]] = defaultdict(set)
        missing_meals = 0
        missing_codes = 0

        for row in alerjid_rows:
            meal_code = (row.get("RECEPT") or "").strip()
            allergen_code = (row.get("KOD") or "").strip()
            meal_data = meal_catalog.get(meal_code)
            if not meal_data:
                missing_meals += 1
                continue
            if meal_data["slug"] not in existing_foods_by_slug and meal_code not in meal_catalog:
                missing_meals += 1
                continue
            if allergen_code not in allergen_catalog:
                missing_codes += 1
                continue
            targets[meal_code].add(allergen_code)

        return targets, missing_meals, missing_codes

    def _build_receipt_targets(self, qhk01_rows: list[dict], ingredient_catalog: dict[str, dict]) -> dict[date, dict]:
        targets: dict[date, dict] = {}
        for row in qhk01_rows:
            ingredient_code = (row.get("CISLO") or "").strip()
            receipt_date = row.get("DATUM")
            if ingredient_code not in ingredient_catalog or not receipt_date:
                continue
            bucket = targets.setdefault(
                receipt_date,
                {
                    "datum": receipt_date,
                    "cislo_faktury": f"DATAX-QHK01-{receipt_date.strftime('%Y%m%d')}",
                    "popis": f"{DATAX_RECEIPT_PREFIX} / {receipt_date.strftime('%d.%m.%Y')}",
                    "lines": [],
                },
            )
            bucket["lines"].append(
                {
                    "ingredient_code": ingredient_code,
                    "mnozstvi": self._parse_decimal(row.get("MNOZSTVI")) or Decimal("0"),
                    "jednotkova_cena": self._parse_decimal(row.get("CENA")) or Decimal("0"),
                    "sazba_dph": self._parse_decimal(row.get("DPH")) or Decimal("0"),
                    "datum_spotreby": row.get("EXPIRACE"),
                    "cena_celkem_s_dph": self._parse_decimal(row.get("SUM_KC"))
                    or self._parse_decimal(row.get("NAKLAD"))
                    or Decimal("0"),
                    "sarze": (row.get("C_PRIJ") or "").strip(),
                }
            )
        return targets

    def _build_movement_targets(self, qhk10_rows: list[dict], ingredient_catalog: dict[str, dict]) -> list[dict]:
        targets: list[dict] = []
        for row in qhk10_rows:
            ingredient_code = (row.get("CISLO") or "").strip()
            movement_date = row.get("DATUM")
            if ingredient_code not in ingredient_catalog or not movement_date:
                continue
            targets.append(
                {
                    "ingredient_code": ingredient_code,
                    "datum": movement_date,
                    "mnozstvi": self._parse_decimal(row.get("MNOZSTVI")) or Decimal("0"),
                    "cena_za_jednotku": self._parse_decimal(row.get("CENA")) or Decimal("0"),
                    "odber": (row.get("ODBER") or "").strip(),
                    "odbor": (row.get("ODBOR") or "").strip(),
                    "cz": (row.get("CZ") or "").strip(),
                    "druh": (row.get("DRUH") or "").strip(),
                    "jidlo": (row.get("JIDLO") or "").strip(),
                    "chod": (row.get("CHOD") or "").strip(),
                }
            )
        return targets

    def _build_norma_targets(self, nutrie_rows: list[dict], porce_rows: list[dict]) -> list[dict]:
        base_norms = self._aggregate_base_nutrie_norms(nutrie_rows)
        targets: list[dict] = []

        for vekova_kategorie, groups in base_norms.items():
            for skupina_sk, norma_g_den in groups.items():
                targets.append(
                    {
                        "stravovaci_skupina": None,
                        "vekova_kategorie": vekova_kategorie,
                        "typ_jidla": NormaSpotrebnihoKose.TYP_CELODENNI,
                        "skupina_sk": skupina_sk,
                        "norma_g_den": norma_g_den,
                    }
                )

        student_group = (
            StravovaciSkupina.objects.filter(nazev__icontains="student")
            .order_by("id")
            .first()
        )
        teacher_group = (
            StravovaciSkupina.objects.filter(nazev__icontains="učitel")
            .order_by("id")
            .first()
            or StravovaciSkupina.objects.filter(nazev__icontains="person")
            .order_by("id")
            .first()
        )

        student_portion = self._find_portion_profile(
            porce_rows,
            required_terms=("zaci", "celodenni"),
        )
        if student_group and student_portion:
            meal_shares = self._derive_meal_shares(student_portion)
            vek = self._vek_category_from_group(student_group.typ_vzdelavani)
            for skupina_sk, daily_norm in base_norms.get(vek, {}).items():
                for typ_jidla, share in meal_shares.items():
                    targets.append(
                        {
                            "stravovaci_skupina": student_group,
                            "vekova_kategorie": vek,
                            "typ_jidla": typ_jidla,
                            "skupina_sk": skupina_sk,
                            "norma_g_den": (daily_norm * share).quantize(Decimal("0.001")),
                        }
                    )

        if teacher_group:
            vek = self._vek_category_from_group(teacher_group.typ_vzdelavani)
            if vek not in base_norms:
                vek = NormaSpotrebnihoKose.VEK_15_PLUS
            for skupina_sk, daily_norm in base_norms.get(vek, {}).items():
                targets.append(
                    {
                        "stravovaci_skupina": teacher_group,
                        "vekova_kategorie": vek,
                        "typ_jidla": NormaSpotrebnihoKose.TYP_OBED,
                        "skupina_sk": skupina_sk,
                        "norma_g_den": daily_norm,
                    }
                )

        return targets

    def _aggregate_base_nutrie_norms(self, nutrie_rows: list[dict]) -> dict[str, dict[str, Decimal]]:
        aggregated: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
        age_fields = {
            NormaSpotrebnihoKose.VEK_2_3: "NOR1",
            NormaSpotrebnihoKose.VEK_4_6: "NOR2",
            NormaSpotrebnihoKose.VEK_7_10: "NOR3",
            NormaSpotrebnihoKose.VEK_11_14: "NOR4",
            NormaSpotrebnihoKose.VEK_15_PLUS: "NOR4",
        }

        for row in nutrie_rows:
            code = (row.get("CISLO") or "").strip().upper()
            base_code = (row.get("KOD") or "").strip().upper()
            if not code or not base_code or code != base_code:
                continue
            skupina_sk = DATAX_KOS_TO_SK.get(base_code)
            if not skupina_sk:
                continue
            for vekova_kategorie, field in age_fields.items():
                value = self._parse_decimal(row.get(field)) or Decimal("0")
                aggregated[vekova_kategorie][skupina_sk] += value

        return aggregated

    def _find_portion_profile(self, porce_rows: list[dict], required_terms: tuple[str, ...]) -> dict | None:
        for row in porce_rows:
            normalized = self._normalize(row.get("NAZEV"))
            if all(term in normalized for term in required_terms):
                return row
        return None

    def _derive_meal_shares(self, porce_row: dict) -> dict[str, Decimal]:
        values = {
            NormaSpotrebnihoKose.TYP_SNIDANE: self._parse_decimal(porce_row.get("NSN")) or Decimal("0"),
            NormaSpotrebnihoKose.TYP_PRESNIDAVKA: self._parse_decimal(porce_row.get("NSD")) or Decimal("0"),
            NormaSpotrebnihoKose.TYP_OBED: self._parse_decimal(porce_row.get("NOB")) or Decimal("0"),
            NormaSpotrebnihoKose.TYP_SVACINA: self._parse_decimal(porce_row.get("NSO")) or Decimal("0"),
            NormaSpotrebnihoKose.TYP_VECERE: (self._parse_decimal(porce_row.get("NVE")) or Decimal("0"))
            + (self._parse_decimal(porce_row.get("NDV")) or Decimal("0")),
        }
        total = sum(values.values(), Decimal("0"))
        if total <= 0:
            return {NormaSpotrebnihoKose.TYP_OBED: Decimal("1")}
        return {
            typ_jidla: (value / total)
            for typ_jidla, value in values.items()
            if value > 0
        }

    def _vek_category_from_group(self, typ_vzdelavani: str | None) -> str:
        mapping = {
            "MS": NormaSpotrebnihoKose.VEK_4_6,
            "ZS1": NormaSpotrebnihoKose.VEK_7_10,
            "ZS2": NormaSpotrebnihoKose.VEK_11_14,
            "SS": NormaSpotrebnihoKose.VEK_15_PLUS,
            "JINE": NormaSpotrebnihoKose.VEK_15_PLUS,
        }
        return mapping.get(typ_vzdelavani or "", NormaSpotrebnihoKose.VEK_15_PLUS)

    def _import_suroviny(
        self,
        ingredient_catalog: dict[str, dict],
        existing_by_name: dict[str, Surovina],
    ) -> dict[str, Surovina]:
        result: dict[str, Surovina] = {}
        for code, data in ingredient_catalog.items():
            key = data["nazev"].strip().lower()
            surovina = existing_by_name.get(key)
            if surovina is None:
                surovina = Surovina.objects.create(
                    nazev=data["nazev"],
                    jednotka=data["jednotka"],
                    prumerna_cena_za_jednotku=data["prumerna_cena_za_jednotku"],
                    hmotnost_ks_g=data["hmotnost_ks_g"],
                    skupina_sk=data["skupina_sk"],
                    koeficient_sk=data["koeficient_sk"],
                    koeficient_ciste_hmotnosti_sk=data["koeficient_ciste_hmotnosti_sk"],
                    koeficient_zapoctu_sk=data["koeficient_zapoctu_sk"],
                    je_sterilovana_nebo_kompot=data["je_sterilovana_nebo_kompot"],
                )
                existing_by_name[key] = surovina
            else:
                changed_fields = []
                if surovina.jednotka != data["jednotka"]:
                    surovina.jednotka = data["jednotka"]
                    changed_fields.append("jednotka")
                if data["prumerna_cena_za_jednotku"] is not None and surovina.prumerna_cena_za_jednotku != data["prumerna_cena_za_jednotku"]:
                    surovina.prumerna_cena_za_jednotku = data["prumerna_cena_za_jednotku"]
                    changed_fields.append("prumerna_cena_za_jednotku")
                if data["hmotnost_ks_g"] is not None and surovina.hmotnost_ks_g != data["hmotnost_ks_g"]:
                    surovina.hmotnost_ks_g = data["hmotnost_ks_g"]
                    changed_fields.append("hmotnost_ks_g")
                if surovina.skupina_sk != data["skupina_sk"]:
                    surovina.skupina_sk = data["skupina_sk"]
                    changed_fields.append("skupina_sk")
                if surovina.koeficient_sk != data["koeficient_sk"]:
                    surovina.koeficient_sk = data["koeficient_sk"]
                    changed_fields.append("koeficient_sk")
                if surovina.koeficient_ciste_hmotnosti_sk != data["koeficient_ciste_hmotnosti_sk"]:
                    surovina.koeficient_ciste_hmotnosti_sk = data["koeficient_ciste_hmotnosti_sk"]
                    changed_fields.append("koeficient_ciste_hmotnosti_sk")
                if surovina.koeficient_zapoctu_sk != data["koeficient_zapoctu_sk"]:
                    surovina.koeficient_zapoctu_sk = data["koeficient_zapoctu_sk"]
                    changed_fields.append("koeficient_zapoctu_sk")
                if surovina.je_sterilovana_nebo_kompot != data["je_sterilovana_nebo_kompot"]:
                    surovina.je_sterilovana_nebo_kompot = data["je_sterilovana_nebo_kompot"]
                    changed_fields.append("je_sterilovana_nebo_kompot")
                if changed_fields:
                    surovina.save(update_fields=changed_fields)

            stav, _ = StavSkladu.objects.get_or_create(surovina=surovina)
            if stav.mnozstvi != data["mnozstvi"]:
                stav.mnozstvi = data["mnozstvi"]
                stav.save(update_fields=["mnozstvi"])

            result[code] = surovina
        return result

    def _import_alergeny(
        self,
        allergen_catalog: dict[str, dict],
        existing_by_name: dict[str, Alergen],
    ) -> dict[str, Alergen]:
        result: dict[str, Alergen] = {}
        for code, data in allergen_catalog.items():
            key = data["nazev"].strip().lower()
            alergen = existing_by_name.get(key)
            if alergen is None:
                alergen = Alergen.objects.create(nazev=data["nazev"])
                existing_by_name[key] = alergen
            result[code] = alergen
        return result

    def _import_partners(self, partner_catalog: dict[str, dict]) -> int:
        existing = {
            self._normalize(item.nazev): item
            for item in Dodavatel.objects.all()
        }
        written = 0
        for data in partner_catalog.values():
            normalized = self._normalize(data["nazev"])
            partner = existing.get(normalized)
            if partner is None:
                partner = Dodavatel.objects.create(
                    nazev=data["nazev"],
                    typ_subjektu=data["typ_subjektu"],
                    ico=data["ico"],
                    dic=data["dic"],
                    adresa=data["adresa"],
                    datax_zdroj=data["datax_zdroj"],
                    datax_kod=data["datax_kod"],
                    datax_kod2=data["datax_kod2"],
                    datax_analytika=data["datax_analytika"],
                    poznamka=data["poznamka"],
                    aktivni=True,
                )
                existing[normalized] = partner
            else:
                changed_fields = []
                if partner.nazev != data["nazev"]:
                    partner.nazev = data["nazev"]
                    changed_fields.append("nazev")
                if partner.typ_subjektu != data["typ_subjektu"]:
                    partner.typ_subjektu = data["typ_subjektu"]
                    changed_fields.append("typ_subjektu")
                if data["ico"] and not partner.ico:
                    partner.ico = data["ico"]
                    changed_fields.append("ico")
                if data["dic"] and not partner.dic:
                    partner.dic = data["dic"]
                    changed_fields.append("dic")
                if data["adresa"] and not partner.adresa:
                    partner.adresa = data["adresa"]
                    changed_fields.append("adresa")
                if data["datax_zdroj"] and partner.datax_zdroj != data["datax_zdroj"]:
                    partner.datax_zdroj = data["datax_zdroj"]
                    changed_fields.append("datax_zdroj")
                if data["datax_kod"] and partner.datax_kod != data["datax_kod"]:
                    partner.datax_kod = data["datax_kod"]
                    changed_fields.append("datax_kod")
                if data["datax_kod2"] and partner.datax_kod2 != data["datax_kod2"]:
                    partner.datax_kod2 = data["datax_kod2"]
                    changed_fields.append("datax_kod2")
                if data["datax_analytika"] and partner.datax_analytika != data["datax_analytika"]:
                    partner.datax_analytika = data["datax_analytika"]
                    changed_fields.append("datax_analytika")
                if data["poznamka"] and data["poznamka"] not in (partner.poznamka or ""):
                    partner.poznamka = self._join_nonempty([partner.poznamka, data["poznamka"]])
                    changed_fields.append("poznamka")
                if changed_fields:
                    partner.save(update_fields=changed_fields)
            written += 1
        return written

    def _import_jidla(
        self,
        meal_catalog: dict[str, dict],
        existing_by_slug: dict[str, Jidlo],
    ) -> dict[str, Jidlo]:
        result: dict[str, Jidlo] = {}
        for code, data in meal_catalog.items():
            jidlo = existing_by_slug.get(data["slug"])
            if jidlo is None:
                druh = self._get_or_create_druh_for_datax_group(data["skupina"], data["nazev"])
                jidlo = Jidlo.objects.create(
                    nazev=data["nazev"],
                    cena=data["cena"] or Decimal("0.00"),
                    druh=druh,
                )
                existing_by_slug[data["slug"]] = jidlo
            else:
                changed_fields = []
                if data["cena"] is not None and jidlo.cena != data["cena"]:
                    jidlo.cena = data["cena"]
                    changed_fields.append("cena")
                if jidlo.druh_id is None:
                    jidlo.druh = self._get_or_create_druh_for_datax_group(data["skupina"], data["nazev"])
                    changed_fields.append("druh")
                if changed_fields:
                    jidlo.save(update_fields=changed_fields)
            result[code] = jidlo
        return result

    def _import_receptury(
        self,
        recipe_targets: dict[str, dict[str, Decimal]],
        food_objects_by_code: dict[str, Jidlo],
        ingredient_objects_by_code: dict[str, Surovina],
    ) -> int:
        target_food_ids = [
            food.id for code, food in food_objects_by_code.items()
            if code in recipe_targets
        ]
        if target_food_ids:
            RecepturaPolozka.objects.filter(jidlo_id__in=target_food_ids).delete()

        created = 0
        batch: list[RecepturaPolozka] = []
        for meal_code, ingredients in recipe_targets.items():
            jidlo = food_objects_by_code.get(meal_code)
            if jidlo is None:
                continue
            for ingredient_code, amount in ingredients.items():
                surovina = ingredient_objects_by_code.get(ingredient_code)
                if surovina is None:
                    continue
                batch.append(
                    RecepturaPolozka(
                        jidlo=jidlo,
                        surovina=surovina,
                        mnozstvi_na_porci=amount,
                    )
                )
                created += 1
        if batch:
            RecepturaPolozka.objects.bulk_create(batch, batch_size=1000)
        return created

    def _import_vazby_alergenu(
        self,
        allergen_targets: dict[str, set[str]],
        food_objects_by_code: dict[str, Jidlo],
        allergen_objects_by_code: dict[str, Alergen],
    ) -> int:
        linked = 0
        for meal_code, allergen_codes in allergen_targets.items():
            jidlo = food_objects_by_code.get(meal_code)
            if jidlo is None:
                continue
            allergens = [
                allergen_objects_by_code[code]
                for code in sorted(allergen_codes)
                if code in allergen_objects_by_code
            ]
            jidlo.alergeny.set(allergens)
            linked += len(allergens)
        return linked

    def _import_normy(self, norma_targets: list[dict]) -> int:
        written = 0
        for target in norma_targets:
            NormaSpotrebnihoKose.objects.update_or_create(
                stravovaci_skupina=target["stravovaci_skupina"],
                vekova_kategorie=target["vekova_kategorie"],
                typ_jidla=target["typ_jidla"],
                skupina_sk=target["skupina_sk"],
                defaults={
                    "norma_g_den": target["norma_g_den"],
                    "norma_g_mesic": Decimal("0"),
                },
            )
            written += 1
        return written

    def _import_tolerance(self) -> int:
        written = 0
        for skupina_sk, (min_pct, max_pct) in LEGISLATIVNI_TOLERANCE_SK_2025.items():
            ToleranceSpotrebnihoKose.objects.update_or_create(
                stravovaci_skupina=None,
                skupina_sk=skupina_sk,
                defaults={"min_pct": min_pct, "max_pct": max_pct},
            )
            written += 1
        return written

    def _clear_previous_datax_documents(self):
        PohybSkladu.objects.filter(poznamka__startswith=DATAX_MOVEMENT_PREFIX).delete()
        PohybSkladu.objects.filter(poznamka__startswith=DATAX_RECEIPT_PREFIX).delete()
        PrijemSkladu.objects.filter(popis__startswith=DATAX_RECEIPT_PREFIX).delete()

    def _import_historicke_prijemky(
        self,
        receipt_plan: dict[date, dict],
        ingredient_objects_by_code: dict[str, Surovina],
    ) -> tuple[int, int, int]:
        self._clear_previous_datax_documents()
        supplier, _ = Dodavatel.objects.get_or_create(
            nazev=DATAX_HISTORIC_SUPPLIER,
            defaults={
                "typ_subjektu": Dodavatel.TYP_TECHNICKY,
                "datax_zdroj": "DATAx QHK01",
                "poznamka": "Technický dodavatel pro historické DATAx příjemky.",
            },
        )
        supplier_updates = []
        if supplier.typ_subjektu != Dodavatel.TYP_TECHNICKY:
            supplier.typ_subjektu = Dodavatel.TYP_TECHNICKY
            supplier_updates.append("typ_subjektu")
        if supplier.datax_zdroj != "DATAx QHK01":
            supplier.datax_zdroj = "DATAx QHK01"
            supplier_updates.append("datax_zdroj")
        if supplier.nazev != DATAX_HISTORIC_SUPPLIER:
            supplier.nazev = DATAX_HISTORIC_SUPPLIER
            supplier_updates.append("nazev")
        if supplier_updates:
            supplier.save(update_fields=supplier_updates)

        receipts_written = 0
        lines_written = 0
        movements_written = 0

        for receipt_date in sorted(receipt_plan):
            target = receipt_plan[receipt_date]
            prijem = PrijemSkladu.objects.create(
                datum=receipt_date,
                popis=target["popis"],
                dodavatel=supplier,
                cislo_faktury=target["cislo_faktury"],
                datum_dodani=receipt_date,
                datum_vystaveni=receipt_date,
                uzavreny=True,
                uzavren_at=self._make_aware_datetime(receipt_date),
            )
            receipts_written += 1

            for line in target["lines"]:
                surovina = ingredient_objects_by_code.get(line["ingredient_code"])
                if surovina is None:
                    continue

                cena_s_dph = line["cena_celkem_s_dph"] or Decimal("0")
                dph_factor = Decimal("1") + ((line["sazba_dph"] or Decimal("0")) / Decimal("100"))
                cena_bez_dph = (cena_s_dph / dph_factor) if dph_factor and cena_s_dph else cena_s_dph
                datum_spotreby = line["datum_spotreby"] if self._is_valid_expiry(line["datum_spotreby"]) else None

                PolozkaPrijmu.objects.create(
                    prijem=prijem,
                    surovina=surovina,
                    mnozstvi=line["mnozstvi"],
                    jednotkova_cena=line["jednotkova_cena"],
                    sazba_dph=line["sazba_dph"],
                    cena_celkem_s_dph=cena_s_dph,
                    cena_celkem_bez_dph=cena_bez_dph,
                    datum_spotreby=datum_spotreby,
                    typ_data_spotreby="POUZITELNOST" if datum_spotreby else "NEUVADI_SE",
                    sarze=line["sarze"],
                )
                lines_written += 1

                PohybSkladu.objects.create(
                    datum=self._make_aware_datetime(receipt_date),
                    surovina=surovina,
                    typ=PohybSkladu.TYP_PRIJEM,
                    mnozstvi=line["mnozstvi"],
                    cena_za_jednotku=line["jednotkova_cena"],
                    prijem=prijem,
                    poznamka=f"{DATAX_RECEIPT_PREFIX} / {receipt_date.strftime('%d.%m.%Y')} / {surovina.nazev}",
                )
                movements_written += 1

        return receipts_written, lines_written, movements_written

    def _import_historicke_pohyby(
        self,
        movement_plan: list[dict],
        ingredient_objects_by_code: dict[str, Surovina],
    ) -> int:
        written = 0
        for line in movement_plan:
            surovina = ingredient_objects_by_code.get(line["ingredient_code"])
            if surovina is None:
                continue

            popis_parts = [DATAX_MOVEMENT_PREFIX]
            if line["odber"]:
                popis_parts.append(f"odběr {line['odber']}")
            if line["odbor"]:
                popis_parts.append(line["odbor"])
            if line["cz"]:
                popis_parts.append(f"CZ {line['cz']}")
            if line["jidlo"]:
                popis_parts.append(f"jídlo {line['jidlo']}")
            if line["chod"]:
                popis_parts.append(f"chod {line['chod']}")

            PohybSkladu.objects.create(
                datum=self._make_aware_datetime(line["datum"]),
                surovina=surovina,
                typ=PohybSkladu.TYP_VYDEJ,
                mnozstvi=line["mnozstvi"],
                cena_za_jednotku=line["cena_za_jednotku"],
                poznamka=" / ".join(popis_parts),
            )
            written += 1
        return written

    def _get_or_create_druh_for_datax_group(self, group_name: str, meal_name: str) -> DruhJidla:
        group_norm = self._normalize(group_name)
        meal_norm = self._normalize(meal_name)
        if "napoj" in group_norm:
            nazev, poradi = "Nápoj", 15
        elif "polevk" in group_norm or "polevk" in meal_norm:
            nazev, poradi = "Polévka", 25
        elif any(token in group_norm for token in ("dezert", "moucnik", "sladk")):
            nazev, poradi = "Dezert", 45
        elif "snidan" in group_norm:
            nazev, poradi = "Snídaně", 10
        else:
            nazev, poradi = "Hlavní chod", 30
        druh, _ = DruhJidla.objects.get_or_create(nazev=nazev, defaults={"poradi": poradi})
        if druh.poradi != poradi:
            druh.poradi = poradi
            druh.save(update_fields=["poradi"])
        return druh

    def _map_unit(self, raw_unit: str) -> str:
        unit = self._normalize(raw_unit)
        if unit in {"g", "gr"}:
            return Surovina.JEDNOTKA_G
        if unit == "kg":
            return Surovina.JEDNOTKA_KG
        if unit in {"l", "lt"}:
            return Surovina.JEDNOTKA_L
        if unit == "ml":
            return Surovina.JEDNOTKA_ML
        return Surovina.JEDNOTKA_KS

    def _parse_decimal(self, value) -> Decimal | None:
        if value in (None, ""):
            return None
        if isinstance(value, Decimal):
            return value
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            return None

    def _is_deleted(self, value) -> bool:
        return str(value or "").strip().upper() in {"A", "D", "X"}

    def _normalize(self, value: str | None) -> str:
        text = str(value or "").strip().lower().replace("ı", "i")
        return "".join(
            char for char in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(char)
        )

    def _clean_datax_label(self, value: str | None) -> str:
        text = " ".join(str(value or "").strip().split())
        if not text:
            return ""

        exact_map = {
            "do spotreby kuchyne": "Do spotřeby kuchyně",
            "sady bile podoli": "SADY Bílé Podolí",
            "lahudky": "Lahůdky",
            "lahùdky": "Lahůdky",
            "datax historickı import": "DATAx historický import",
            "datax historicky import": "DATAx historický import",
        }
        normalized = self._normalize(text)
        if normalized in exact_map:
            return exact_map[normalized]

        if text.isupper():
            text = text.title()

        generic_map = {
            " s.r.o.": " s.r.o.",
            " a.s.": " a.s.",
        }
        for source, target in generic_map.items():
            text = text.replace(source.title(), target)

        return text

    def _classify_partner_type(
        self,
        *,
        source: str,
        name: str,
        ico: str,
        code: str,
        code2: str,
        analytika: str,
    ) -> str:
        normalized = self._normalize(name)
        if "historick" in normalized and "datax" in normalized:
            return Dodavatel.TYP_TECHNICKY
        if source == "MKODBER":
            if any(token in normalized for token in ("restaurace", "lahudky", "degustace", "vzorky")):
                return Dodavatel.TYP_PROVOZ
            return Dodavatel.TYP_STREDISKO
        if ico:
            return Dodavatel.TYP_DODAVATEL
        if any(token in normalized for token in ("spotreby", "kuchyne", "stredisko", "provoz")):
            return Dodavatel.TYP_STREDISKO
        if code2 or analytika:
            return Dodavatel.TYP_STREDISKO
        return Dodavatel.TYP_DODAVATEL

    def _slugify(self, value: str | None) -> str:
        normalized = self._normalize(value)
        return "-".join(
            part for part in "".join(ch if ch.isalnum() else " " for ch in normalized).split()
            if part
        )

    def _is_valid_expiry(self, value) -> bool:
        return isinstance(value, date) and value.year < 9999

    def _make_aware_datetime(self, value: date) -> datetime:
        return timezone.make_aware(
            datetime.combine(value, time.min),
            timezone.get_current_timezone(),
        )

    def _join_nonempty(self, parts: list[str]) -> str:
        return "\n".join(part for part in parts if part)

    def _read_dbf_rows(self, dbf_path: Path) -> list[dict]:
        with dbf_path.open("rb") as fh:
            header = fh.read(32)
            if len(header) < 32:
                raise CommandError(f"Poškozená DBF hlavička: {dbf_path}")

            _, _, _, _, record_count, header_len, record_len = struct.unpack(
                "<BBBBIHH20x",
                header,
            )

            fields: list[DbfField] = []
            offset = 1
            while True:
                descriptor = fh.read(32)
                if len(descriptor) < 32:
                    raise CommandError(f"Poškozené field descriptors: {dbf_path}")
                if descriptor[0] == 0x0D:
                    break
                raw_name = descriptor[:11].split(b"\x00", 1)[0]
                name = raw_name.decode("ascii", errors="ignore").strip()
                field_type = chr(descriptor[11])
                length = descriptor[16]
                decimal_count = descriptor[17]
                fields.append(
                    DbfField(
                        name=name,
                        field_type=field_type,
                        length=length,
                        decimal_count=decimal_count,
                        offset=offset,
                    )
                )
                offset += length

            fh.seek(header_len)
            rows: list[dict] = []
            for _ in range(record_count):
                record = fh.read(record_len)
                if len(record) < record_len:
                    break
                if record[0] == 0x2A:
                    continue

                parsed: dict = {}
                for field in fields:
                    raw = record[field.offset : field.offset + field.length]
                    parsed[field.name] = self._decode_dbf_value(field, raw)
                rows.append(parsed)
            return rows

    def _decode_dbf_value(self, field: DbfField, raw: bytes):
        if field.field_type == "C":
            return raw.decode(DBF_ENCODING, errors="ignore").strip()
        if field.field_type == "N":
            return raw.decode("ascii", errors="ignore").strip()
        if field.field_type == "D":
            text = raw.decode("ascii", errors="ignore").strip()
            if len(text) == 8 and text != "00000000":
                try:
                    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
                except ValueError:
                    return None
            return None
        if field.field_type == "L":
            return raw[:1].upper() in {b"T", b"Y", b"1"}
        return raw.decode(DBF_ENCODING, errors="ignore").strip()

    def _read_optional_dbf(self, dbf_path: Path) -> list[dict]:
        if not dbf_path.exists():
            return []
        try:
            return self._read_dbf_rows(dbf_path)
        except CommandError as exc:
            self.stdout.write(self.style.WARNING(f"Přeskakuji {dbf_path.name}: {exc}"))
            return []
