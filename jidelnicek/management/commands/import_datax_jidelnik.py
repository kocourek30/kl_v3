from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import struct
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db import connection
from django.db.utils import OperationalError, ProgrammingError
from django.utils.text import slugify
from django.utils import timezone

from jidelnicek.models import DruhJidla, Jidelnicek, Jidlo, MenuImportRun, PolozkaJidelnicku
from objednavky.models import Order, OrderItem, PriceRecalculationDetail, PriceRecalculationLog
from vydej.models import PolozkaUctenky, VydejniUctenka


DBF_ENCODING = "cp852"


@dataclass(frozen=True)
class DbfField:
    name: str
    field_type: str
    length: int
    decimal_count: int
    offset: int


class Command(BaseCommand):
    help = (
        "Importuje jídelníček z Datax DBF (JIDELNIK.DBF) pro vybrané měsíce "
        "a volitelně předtím smaže navázané testovací transakce."
    )

    DEFAULT_TYPE_MAP = {
        "SN": ("Snídaně", 10),
        "SD": ("1. Svačina", 20),
        "OB": ("Oběd", 30),
        "SO": ("2. Svačina", 40),
        "VE": ("Večeře", 50),
        "DV": ("2. Večeře", 60),
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--dbf-path",
            default=r"E:\datax\JIDELNIK.DBF",
            help="Cesta k Datax JIDELNIK.DBF",
        )
        parser.add_argument(
            "--year",
            type=int,
            default=2026,
            help="Rok pro import (default 2026).",
        )
        parser.add_argument(
            "--months",
            default="4,5",
            help="Měsíce oddělené čárkou, např. 4,5",
        )
        parser.add_argument(
            "--purge-related",
            action="store_true",
            help=(
                "Smaže objednávky/výdej/přepočty a jídelníčky+jídla před importem. "
                "Nesahá na superusera ani systémová nastavení."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Pouze vypíše, co by se importovalo. Bez zápisu do DB.",
        )
        parser.add_argument(
            "--datax-merge-blocks",
            action="store_true",
            default=True,
            help=(
                "Skládá položky do bloků podle TYP+PORADI (podobně jako DATAX sestava "
                "s řádky (1), (2), ...)."
            ),
        )

    def handle(self, *args, **options):
        dbf_path = Path(options["dbf_path"])
        if not dbf_path.exists():
            raise CommandError(f"Soubor neexistuje: {dbf_path}")

        year = options["year"]
        months = self._parse_months(options["months"])
        dry_run = options["dry_run"]
        purge_related = options["purge_related"]
        datax_merge_blocks = options["datax_merge_blocks"]

        import_run = None
        try:
            import_run = MenuImportRun.objects.create(
                source=MenuImportRun.SOURCE_DATAX,
                status=MenuImportRun.STATUS_RUNNING,
                dry_run=dry_run,
            )
        except (ProgrammingError, OperationalError):
            import_run = None

        try:
            rows = self._read_dbf_rows(dbf_path)
            raw_selected_rows = [
                row
                for row in rows
                if row.get("DATUM")
                and row["DATUM"].year == year
                and row["DATUM"].month in months
                and row.get("NAZEV")
            ]

            if not raw_selected_rows:
                raise CommandError(
                    f"V souboru {dbf_path} nejsou žádné řádky pro rok {year} a měsíce {months}."
                )

            grouped_by_day_raw = defaultdict(list)
            for row in raw_selected_rows:
                grouped_by_day_raw[row["DATUM"]].append(row)

            if datax_merge_blocks:
                grouped_by_day_source = {
                    day: self._compose_meal_blocks(day_rows)
                    for day, day_rows in grouped_by_day_raw.items()
                }
            else:
                grouped_by_day_source = dict(grouped_by_day_raw)

            slug_catalog = self._build_slug_catalog(
                [row for day_rows in grouped_by_day_source.values() for row in day_rows]
            )

            grouped_by_day = {}
            duplicates_merged = 0
            for day, day_rows in grouped_by_day_source.items():
                deduped_rows, merged_count = self._dedupe_day_rows(day_rows, slug_catalog)
                grouped_by_day[day] = deduped_rows
                duplicates_merged += merged_count

            total_days = len(grouped_by_day)
            total_rows_raw = len(raw_selected_rows)
            total_rows = sum(len(rows_for_day) for rows_for_day in grouped_by_day.values())
            date_min = min(grouped_by_day.keys())
            date_max = max(grouped_by_day.keys())

            self.stdout.write(
                self.style.NOTICE(
                    f"Načteno {total_rows_raw} řádků, po sloučení duplicit {total_rows} řádků "
                    f"({duplicates_merged} sloučeno) pro {total_days} dnů ({date_min} až {date_max})."
                )
            )

            if import_run:
                import_run.rows_read = total_rows_raw
                import_run.rows_after_merge = total_rows
                import_run.menu_days = total_days
                import_run.save(update_fields=["rows_read", "rows_after_merge", "menu_days"])

            if dry_run:
                if import_run:
                    import_run.status = MenuImportRun.STATUS_SUCCESS
                    import_run.finished_at = timezone.now()
                    import_run.summary = (
                        f"Dry-run importu DATAx: {total_days} dnů, {total_rows} řádků po sloučení."
                    )
                    import_run.save(update_fields=["status", "finished_at", "summary"])
                self.stdout.write(self.style.WARNING("DRY RUN: žádné změny se neuloží."))
                return

            with transaction.atomic():
                legacy_mode = self._is_legacy_schema()
                if legacy_mode:
                    self.stdout.write(
                        self.style.WARNING(
                            "Detekována starší DB struktura (bez všech nových sloupců). "
                            "Používám kompatibilní SQL režim importu."
                        )
                    )

                if purge_related:
                    self._purge_related_data()

                created_menu_days = 0
                created_items = 0
                created_foods = 0

                if legacy_mode:
                    self._delete_target_period_legacy(year, months)
                    created_menu_days, created_items, created_foods = self._import_legacy(grouped_by_day)
                else:
                    # Vždy čistíme cílové období, ať je import opakovatelný.
                    Jidelnicek.objects.filter(platnost_od__year=year, platnost_od__month__in=months).delete()

                    type_cache: dict[str, DruhJidla] = {}
                    food_cache = self._load_food_cache()

                    for day, day_rows in sorted(grouped_by_day.items()):
                        jidelnicek = Jidelnicek.objects.create(platnost_od=day, platnost_do=day)
                        created_menu_days += 1

                        # Stabilní pořadí importu v rámci dne.
                        day_rows.sort(key=lambda r: (self._order_for_type(r.get("TYP", "")), r.get("NAZEV", "")))

                        for row in day_rows:
                            slug = self._slug_from_name(row.get("NAZEV"))
                            canonical = slug_catalog.get(slug or "")
                            typ = (row.get("TYP") or "").strip().upper()
                            druh = type_cache.get(typ)
                            if druh is None:
                                druh = self._get_or_create_druh(typ)
                                type_cache[typ] = druh

                            nazev = canonical["nazev"] if canonical else (row.get("NAZEV") or "").strip()
                            cena = canonical["cena"] if canonical else self._parse_decimal(row.get("CENA_CELK"))

                            jidlo = food_cache.get(slug or "")
                            was_created = False
                            if jidlo is None:
                                jidlo = Jidlo.objects.create(
                                    nazev=nazev,
                                    druh=druh,
                                    cena=cena or Decimal("0.00"),
                                )
                                food_cache[slug or ""] = jidlo
                                was_created = True

                            changed_fields = []
                            if jidlo.druh_id is None:
                                jidlo.druh = druh
                                changed_fields.append("druh")
                            if cena is not None and jidlo.cena != cena:
                                jidlo.cena = cena
                                changed_fields.append("cena")
                            if nazev and self._slug_from_name(jidlo.nazev) == (slug or "") and jidlo.nazev != nazev:
                                # Udržujeme názvy konzistentní podle kanonického názvu.
                                jidlo.nazev = nazev
                                changed_fields.append("nazev")

                            if was_created:
                                created_foods += 1
                            elif changed_fields:
                                jidlo.save(update_fields=changed_fields)

                            _, was_polozka_created = PolozkaJidelnicku.objects.get_or_create(
                                jidelnicek=jidelnicek,
                                jidlo=jidlo,
                                druh_jidla=druh,
                            )
                            if was_polozka_created:
                                created_items += 1

            if import_run:
                import_run.status = MenuImportRun.STATUS_SUCCESS
                import_run.finished_at = timezone.now()
                import_run.menus_created = created_menu_days
                import_run.items_created = created_items
                import_run.foods_created = created_foods
                import_run.summary = (
                    f"Import dokončen: jídelníčky={created_menu_days}, položky={created_items}, nová jídla={created_foods}."
                )
                import_run.save(
                    update_fields=[
                        "status",
                        "finished_at",
                        "menus_created",
                        "items_created",
                        "foods_created",
                        "summary",
                    ]
                )

            self.stdout.write(
                self.style.SUCCESS(
                    "Import dokončen: "
                    f"jídelníčky={created_menu_days}, "
                    f"položky={created_items}, "
                    f"nová jídla={created_foods}"
                )
            )
        except Exception as exc:
            if import_run:
                import_run.status = MenuImportRun.STATUS_FAILED
                import_run.finished_at = timezone.now()
                import_run.error_message = str(exc)
                import_run.summary = "Import DATAx skončil chybou."
                import_run.save(
                    update_fields=["status", "finished_at", "error_message", "summary"]
                )
            raise

    def _parse_months(self, raw_months: str) -> list[int]:
        months: list[int] = []
        for chunk in raw_months.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                month = int(chunk)
            except ValueError as exc:
                raise CommandError(f"Neplatný měsíc: {chunk}") from exc
            if month < 1 or month > 12:
                raise CommandError(f"Měsíc musí být 1-12, dostal jsem: {month}")
            months.append(month)
        if not months:
            raise CommandError("Nebyl zadán žádný měsíc.")
        return sorted(set(months))

    def _purge_related_data(self) -> None:
        self.stdout.write(self.style.WARNING("Mažu navázané testovací transakce a historické jídelníčky/jídla..."))
        existing_tables = set(connection.introspection.table_names())

        def safe_delete(model):
            if model._meta.db_table not in existing_tables:
                self.stdout.write(
                    self.style.WARNING(
                        f"Přeskakuji {model._meta.label}: tabulka '{model._meta.db_table}' v DB není."
                    )
                )
                return
            model.objects.all().delete()

        safe_delete(PriceRecalculationDetail)
        safe_delete(PriceRecalculationLog)
        safe_delete(PolozkaUctenky)
        safe_delete(VydejniUctenka)
        safe_delete(OrderItem)
        safe_delete(Order)
        safe_delete(PolozkaJidelnicku)
        safe_delete(Jidelnicek)
        if self._is_legacy_schema() and Jidlo._meta.db_table in existing_tables:
            # Staré schema může mít model pole, která ve fyzické tabulce nejsou.
            # ORM delete by pak spadlo na SELECTu. Tady je bezpečnější přímé SQL.
            with connection.cursor() as cursor:
                cursor.execute(f'DELETE FROM "{Jidlo._meta.db_table}"')
        else:
            safe_delete(Jidlo)

    def _is_legacy_schema(self) -> bool:
        existing_tables = set(connection.introspection.table_names())
        if "jidelnicek_jidlo" not in existing_tables:
            return False
        existing_columns = {
            col.name for col in connection.introspection.get_table_description(connection.cursor(), "jidelnicek_jidlo")
        }
        # V novém modelu máme spotřební koš pole; ve staré DB často chybí.
        return "sk_rybi_pokrm" not in existing_columns

    def _delete_target_period_legacy(self, year: int, months: list[int]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM jidelnicek_polozkajidelnicku p
                USING jidelnicek_jidelnicek j
                WHERE p.jidelnicek_id = j.id
                  AND EXTRACT(YEAR FROM j.platnost_od) = %s
                  AND EXTRACT(MONTH FROM j.platnost_od) = ANY(%s)
                """,
                [year, months],
            )
            cursor.execute(
                """
                DELETE FROM jidelnicek_jidelnicek
                WHERE EXTRACT(YEAR FROM platnost_od) = %s
                  AND EXTRACT(MONTH FROM platnost_od) = ANY(%s)
                """,
                [year, months],
            )

    def _import_legacy(self, grouped_by_day: dict[date, list[dict]]) -> tuple[int, int, int]:
        created_menu_days = 0
        created_items = 0
        created_foods = 0
        type_cache: dict[str, int] = {}
        food_cache: dict[str, dict] = {}

        all_rows = [row for rows in grouped_by_day.values() for row in rows]
        slug_catalog = self._build_slug_catalog(all_rows)

        with connection.cursor() as cursor:
            food_cache = self._load_food_cache_legacy(cursor)
            for day, day_rows in sorted(grouped_by_day.items()):
                cursor.execute(
                    "INSERT INTO jidelnicek_jidelnicek (platnost_od, platnost_do, ikona) VALUES (%s, %s, '') RETURNING id",
                    [day, day],
                )
                jidelnicek_id = cursor.fetchone()[0]
                created_menu_days += 1

                day_rows.sort(key=lambda r: (self._order_for_type(r.get("TYP", "")), r.get("NAZEV", "")))

                for row in day_rows:
                    slug = self._slug_from_name(row.get("NAZEV"))
                    canonical = slug_catalog.get(slug or "")
                    typ = (row.get("TYP") or "").strip().upper()
                    druh_id = type_cache.get(typ)
                    if druh_id is None:
                        druh_id = self._get_or_create_druh_legacy(cursor, typ)
                        type_cache[typ] = druh_id

                    nazev = canonical["nazev"] if canonical else (row.get("NAZEV") or "").strip()
                    cena = canonical["cena"] if canonical else (self._parse_decimal(row.get("CENA_CELK")) or Decimal("0.00"))
                    food_data = food_cache.get(slug or "")
                    if food_data is None:
                        cursor.execute(
                            """
                            INSERT INTO jidelnicek_jidlo (nazev, cena, ikona, druh_id, kcal, "bílkoviny", tuky, sacharidy)
                            VALUES (%s, %s, '', %s, NULL, NULL, NULL, NULL)
                            RETURNING id
                            """,
                            [nazev, cena, druh_id],
                        )
                        jidlo_id = cursor.fetchone()[0]
                        food_cache[slug or ""] = {
                            "id": jidlo_id,
                            "nazev": nazev,
                            "cena": cena,
                            "druh_id": druh_id,
                        }
                        created_foods += 1
                    else:
                        jidlo_id = food_data["id"]
                        update_parts = []
                        update_vals = []
                        if food_data.get("cena") != cena:
                            update_parts.append("cena = %s")
                            update_vals.append(cena)
                            food_data["cena"] = cena
                        if food_data.get("druh_id") is None:
                            update_parts.append("druh_id = %s")
                            update_vals.append(druh_id)
                            food_data["druh_id"] = druh_id
                        if food_data.get("nazev") != nazev:
                            update_parts.append("nazev = %s")
                            update_vals.append(nazev)
                            food_data["nazev"] = nazev
                        if update_parts:
                            update_vals.append(jidlo_id)
                            cursor.execute(
                                f"UPDATE jidelnicek_jidlo SET {', '.join(update_parts)} WHERE id = %s",
                                update_vals,
                            )

                    cursor.execute(
                        """
                        SELECT id
                        FROM jidelnicek_polozkajidelnicku
                        WHERE jidelnicek_id = %s AND jidlo_id = %s AND druh_jidla_id = %s
                        LIMIT 1
                        """,
                        [jidelnicek_id, jidlo_id, druh_id],
                    )
                    existing_item = cursor.fetchone()
                    if not existing_item:
                        cursor.execute(
                            """
                            INSERT INTO jidelnicek_polozkajidelnicku (jidelnicek_id, jidlo_id, druh_jidla_id)
                            VALUES (%s, %s, %s)
                            """,
                            [jidelnicek_id, jidlo_id, druh_id],
                        )
                        created_items += 1

        return created_menu_days, created_items, created_foods

    def _compose_meal_blocks(self, day_rows: list[dict]) -> list[dict]:
        grouped: dict[tuple[str, str], list[tuple[int, dict]]] = defaultdict(list)
        for idx, row in enumerate(day_rows):
            typ = (row.get("TYP") or "").strip().upper()
            poradi = (str(row.get("PORADI") or "").strip() or "1")
            grouped[(typ, poradi)].append((idx, row))

        composed: list[dict] = []
        for (typ, poradi), indexed_rows in grouped.items():
            # Držíme původní pořadí z DBF (odpovídá DATAX sestavě).
            indexed_rows = sorted(indexed_rows, key=lambda item: item[0])
            rows = [item[1] for item in indexed_rows]

            components: list[str] = []
            seen = set()
            for r in rows:
                name = (r.get("NAZEV") or "").strip()
                if not name:
                    continue
                key = self._normalize_name_for_compare(name)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                components.append(name)

            if not components:
                continue

            combined_name = ", ".join(components)
            combined_name = self._apply_variant_label(typ, poradi, combined_name)
            composed_row = dict(rows[0])
            composed_row["NAZEV"] = combined_name
            composed_row["TYP"] = typ
            composed_row["PORADI"] = poradi
            composed.append(composed_row)

        return sorted(
            composed,
            key=lambda r: (
                self._order_for_type((r.get("TYP") or "").strip().upper()),
                int(str(r.get("PORADI") or "999") or "999"),
                (r.get("NAZEV") or ""),
            ),
        )

    def _apply_variant_label(self, typ: str, poradi: str, combined_name: str) -> str:
        """
        Pro snídaně přidá variantní štítek A/B/C..., aby bylo jasné,
        že jde o odhlásitelný celek (např. "Snídaně A: ...", "Snídaně B: ...").
        """
        if typ != "SN":
            return combined_name
        try:
            idx = int(str(poradi).strip())
        except ValueError:
            idx = 1
        if idx < 1:
            idx = 1
        label = self._index_to_letters(idx)
        return f"Snídaně {label}: {combined_name}"

    def _index_to_letters(self, idx: int) -> str:
        """
        1 -> A, 2 -> B, ... 26 -> Z, 27 -> AA ...
        """
        result = ""
        n = idx
        while n > 0:
            n, rem = divmod(n - 1, 26)
            result = chr(ord("A") + rem) + result
        return result or "A"

    def _slug_from_name(self, nazev: str | None) -> str:
        return slugify((nazev or "").strip())

    def _build_slug_catalog(self, rows: list[dict]) -> dict[str, dict]:
        aggregated: dict[str, dict] = {}
        for row in rows:
            nazev = (row.get("NAZEV") or "").strip()
            slug = self._slug_from_name(nazev)
            if not slug:
                continue

            typ = (row.get("TYP") or "").strip().upper()
            cena = self._parse_decimal(row.get("CENA_CELK")) or Decimal("0.00")
            bucket = aggregated.setdefault(
                slug,
                {
                    "typ_counts": defaultdict(int),
                    "name_counts": defaultdict(int),
                    "prices": [],
                },
            )
            bucket["typ_counts"][typ] += 1
            bucket["name_counts"][nazev] += 1
            bucket["prices"].append(cena)

        catalog: dict[str, dict] = {}
        for slug, bucket in aggregated.items():
            typ = sorted(
                bucket["typ_counts"].items(),
                key=lambda item: (-item[1], self._order_for_type(item[0]), item[0]),
            )[0][0]
            nazev = sorted(
                bucket["name_counts"].items(),
                key=lambda item: (-item[1], -len(item[0]), item[0]),
            )[0][0]
            ceny = [c for c in bucket["prices"] if c is not None]
            cena = max(ceny) if ceny else Decimal("0.00")
            catalog[slug] = {
                "typ": typ,
                "nazev": nazev,
                "cena": cena,
            }
        return catalog

    def _dedupe_day_rows(self, day_rows: list[dict], slug_catalog: dict[str, dict]) -> tuple[list[dict], int]:
        selected_by_type_slug: dict[tuple[str, str], dict] = {}
        merged = 0

        for row in day_rows:
            nazev = (row.get("NAZEV") or "").strip()
            slug = self._slug_from_name(nazev)
            if not slug:
                continue

            typ = (row.get("TYP") or "").strip().upper()
            key = (typ, slug)

            current = selected_by_type_slug.get(key)
            if current is None:
                selected_by_type_slug[key] = row
                continue

            merged += 1
            canonical = slug_catalog.get(slug, {})
            target_typ = canonical.get("typ")
            current_typ = (current.get("TYP") or "").strip().upper()
            new_typ = (row.get("TYP") or "").strip().upper()
            current_price = self._parse_decimal(current.get("CENA_CELK")) or Decimal("0.00")
            new_price = self._parse_decimal(row.get("CENA_CELK")) or Decimal("0.00")

            # Inteligentní výběr z duplicity: preferujeme řádek s kanonickým typem
            # a při shodě vyšší cenu.
            current_score = (
                1 if current_typ == target_typ else 0,
                current_price,
                -self._order_for_type(current_typ),
            )
            new_score = (
                1 if new_typ == target_typ else 0,
                new_price,
                -self._order_for_type(new_typ),
            )
            if new_score > current_score:
                selected_by_type_slug[key] = row

        deduped_rows = list(selected_by_type_slug.values())

        # 2) "Lidské" sloučení v rámci stejného druhu jídla:
        # pokud existuje kratší položka, která je obsažená v delší smysluplné položce,
        # kratší vyřadíme (např. "Čaj" + "Čaj s citrónem" -> ponecháme delší).
        by_type: dict[str, list[dict]] = defaultdict(list)
        for row in deduped_rows:
            typ = (row.get("TYP") or "").strip().upper()
            by_type[typ].append(row)

        final_rows: list[dict] = []
        for _typ, type_rows in by_type.items():
            keep_flags = [True] * len(type_rows)
            for i in range(len(type_rows)):
                if not keep_flags[i]:
                    continue
                for j in range(len(type_rows)):
                    if i == j or not keep_flags[j]:
                        continue
                    if self._should_merge_human(type_rows[i], type_rows[j]):
                        keep_flags[i] = False
                        merged += 1
                        break
            for idx, row in enumerate(type_rows):
                if keep_flags[idx]:
                    final_rows.append(row)

        return final_rows, merged

    def _normalize_name_for_compare(self, name: str) -> str:
        slug = self._slug_from_name(name)
        # odstraňujeme časté "spojky" a drobná slova
        tokens = [t for t in slug.split("-") if t and t not in {"s", "se", "a", "na", "v", "z"}]
        return " ".join(tokens)

    def _tokenize(self, text: str) -> list[str]:
        return [t for t in re.split(r"\s+", text.strip()) if t]

    def _should_merge_human(self, shorter_row: dict, longer_row: dict) -> bool:
        shorter_name = (shorter_row.get("NAZEV") or "").strip()
        longer_name = (longer_row.get("NAZEV") or "").strip()
        if not shorter_name or not longer_name or shorter_name == longer_name:
            return False

        short_norm = self._normalize_name_for_compare(shorter_name)
        long_norm = self._normalize_name_for_compare(longer_name)
        if not short_norm or not long_norm:
            return False

        short_tokens = self._tokenize(short_norm)
        long_tokens = self._tokenize(long_norm)
        if not short_tokens or not long_tokens:
            return False

        # merge řešíme jen tehdy, když kandidát "delší" je opravdu informativnější
        if len(long_tokens) <= len(short_tokens):
            return False

        short_set = set(short_tokens)
        long_set = set(long_tokens)
        subset = short_set.issubset(long_set)
        contained = f" {short_norm} " in f" {long_norm} "

        if not (subset or contained):
            return False

        # Opatrnost: neslučovat obecné přílohy, které mají být samostatně (např. chléb, rohlíky, zelenina).
        protected = {
            "chleb",
            "rohliky",
            "rohlik",
            "zelenina",
            "ovoce",
            "kakao",
            "mleko",
            "maslo",
            "vajicka",
            "vanočka",
            "vanocka",
        }
        if short_set & protected:
            return False

        # Naopak chceme sloučit obecné nápoje, pokud je vedle nich detailnější varianta.
        beverage_like = {"caj", "napoj", "piticko", "stava"}
        if short_set & beverage_like:
            return True

        # Obecně: velmi krátký popis, který je podmnožina delšího.
        if len(short_tokens) <= 2:
            return True

        return False

    def _load_food_cache(self) -> dict[str, Jidlo]:
        cache: dict[str, Jidlo] = {}
        for jidlo in Jidlo.objects.all().order_by("id"):
            slug = self._slug_from_name(jidlo.nazev)
            if slug and slug not in cache:
                cache[slug] = jidlo
        return cache

    def _load_food_cache_legacy(self, cursor) -> dict[str, dict]:
        cache: dict[str, dict] = {}
        cursor.execute("SELECT id, nazev, cena, druh_id FROM jidelnicek_jidlo ORDER BY id")
        for jidlo_id, nazev, cena, druh_id in cursor.fetchall():
            slug = self._slug_from_name(nazev)
            if slug and slug not in cache:
                cache[slug] = {
                    "id": jidlo_id,
                    "nazev": nazev,
                    "cena": cena,
                    "druh_id": druh_id,
                }
        return cache

    def _get_or_create_druh_legacy(self, cursor, typ: str) -> int:
        definition = self.DEFAULT_TYPE_MAP.get(typ)
        nazev = definition[0] if definition else f"TYP {typ}"
        if not definition:
            self.stdout.write(self.style.WARNING(f"Neznámý TYP '{typ}', zakládám jako '{nazev}'"))

        cursor.execute(
            "SELECT id FROM jidelnicek_druhjidla WHERE nazev = %s LIMIT 1",
            [nazev],
        )
        row = cursor.fetchone()
        if row:
            return row[0]

        cursor.execute(
            "INSERT INTO jidelnicek_druhjidla (nazev, ikona) VALUES (%s, '') RETURNING id",
            [nazev],
        )
        new_id = cursor.fetchone()[0]
        self.stdout.write(self.style.NOTICE(f"Založen druh jídla: {nazev}"))
        return new_id

    def _order_for_type(self, typ: str) -> int:
        definition = self.DEFAULT_TYPE_MAP.get(typ)
        if definition:
            return definition[1]
        return 999

    def _get_or_create_druh(self, typ: str) -> DruhJidla:
        definition = self.DEFAULT_TYPE_MAP.get(typ)
        if definition:
            nazev, poradi = definition
        else:
            nazev, poradi = f"TYP {typ}", 999
            self.stdout.write(self.style.WARNING(f"Neznámý TYP '{typ}', zakládám jako '{nazev}'"))

        druh, created = DruhJidla.objects.get_or_create(
            nazev=nazev,
            defaults={"poradi": poradi},
        )
        if created:
            self.stdout.write(self.style.NOTICE(f"Založen druh jídla: {nazev}"))
        elif druh.poradi != poradi and poradi != 999:
            druh.poradi = poradi
            druh.save(update_fields=["poradi"])
        return druh

    def _parse_decimal(self, value: str | Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        text = str(value).strip().replace(",", ".")
        if not text:
            return Decimal("0.00")
        try:
            return Decimal(text)
        except InvalidOperation:
            return Decimal("0.00")

    def _read_dbf_rows(self, dbf_path: Path) -> list[dict]:
        with dbf_path.open("rb") as fh:
            header = fh.read(32)
            if len(header) < 32:
                raise CommandError(f"Poškozená DBF hlavička: {dbf_path}")

            _, _, _, _, record_count, header_len, record_len = struct.unpack("<BBBBIHH20x", header)

            fields: list[DbfField] = []
            offset = 1  # první byte je deletion flag

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
                if record[0] == 0x2A:  # deleted
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
            txt = raw.decode("ascii", errors="ignore").strip()
            if not txt:
                return ""
            return txt
        if field.field_type == "D":
            txt = raw.decode("ascii", errors="ignore").strip()
            if len(txt) == 8 and txt != "00000000":
                try:
                    return date(int(txt[:4]), int(txt[4:6]), int(txt[6:8]))
                except ValueError:
                    return None
            return None
        if field.field_type == "L":
            char = raw[:1].upper()
            return char in {b"T", b"Y", b"1"}
        return raw.decode(DBF_ENCODING, errors="ignore").strip()
