from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.hashers import check_password, make_password
from django.db import connection, transaction
from django.utils.text import slugify
from django.utils import timezone

from users.models import StravovaciSkupina


DBF_ENCODING = "cp852"
GROUP_TEACHERS_CODE = "DATAX-UCPERS"
GROUP_STUDENTS_CODE = "DATAX-STUD"


@dataclass(frozen=True)
class DbfField:
    name: str
    field_type: str
    length: int
    decimal_count: int
    offset: int


class Command(BaseCommand):
    help = "Importuje strávníky z Datax KCHSTRAV.DBF (upsert podle osobního čísla CZAKA)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dbf-path",
            default=r"E:\KCHSTRAV.DBF",
            help="Cesta k Datax KCHSTRAV.DBF",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Pouze vypíše změny bez zápisu do DB.",
        )

    def handle(self, *args, **options):
        dbf_path = Path(options["dbf_path"])
        dry_run = options["dry_run"]

        if not dbf_path.exists():
            raise CommandError(f"Soubor neexistuje: {dbf_path}")

        rows = self._read_dbf_rows(dbf_path)
        rows = [row for row in rows if (row.get("CZAKA") or "").strip()]
        if not rows:
            raise CommandError("V KCHSTRAV.DBF nejsou žádní strávníci s osobním číslem (CZAKA).")

        existing_columns = self._get_columns("users_customuser")
        if not existing_columns:
            raise CommandError("Tabulka users_customuser v DB neexistuje.")
        has_must_change_password = "must_change_password" in existing_columns
        has_group_fk = "stravovaci_skupina_id" in existing_columns and "users_stravovaciskupina" in connection.introspection.table_names()

        existing_by_personal = self._load_existing_users_by_personal()

        to_create = 0
        to_update = 0
        touched_groups = set()

        for row in rows:
            osobni_cislo = (row.get("CZAKA") or "").strip()
            if not osobni_cislo:
                continue

            if osobni_cislo in existing_by_personal:
                to_update += 1
            else:
                to_create += 1

            trida = (row.get("TRIDA") or "").strip()
            if trida:
                touched_groups.add(trida)

        self.stdout.write(
            self.style.NOTICE(
                f"Načteno {len(rows)} strávníků. Připraveno: vytvořit {to_create}, aktualizovat {to_update}."
            )
        )
        if touched_groups:
            self.stdout.write(self.style.NOTICE(f"Třídy/skupiny v datech: {', '.join(sorted(touched_groups))}"))

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: žádné změny se neuloží."))
            return

        created = 0
        updated = 0
        created_groups = 0

        with transaction.atomic():
            ucitele_personal = None
            studenti = None
            if has_group_fk:
                ucitele_personal, studenti, created_groups = self._get_or_create_datax_groups()

            for row in rows:
                osobni_cislo = (row.get("CZAKA") or "").strip()
                if not osobni_cislo:
                    continue

                first_name = (row.get("JMENO") or "").strip().title()
                last_name = (row.get("PRIJMENI") or "").strip().title()
                email = (row.get("EMAIL") or "").strip().lower()
                medium = self._pick_identifikacni_kod(row)
                is_active = self._to_is_active(row.get("PLATNY"))
                password_hash = make_password(osobni_cislo)

                skupina_id = (
                    ucitele_personal.id
                    if has_group_fk and osobni_cislo.startswith("9")
                    else studenti.id if has_group_fk else None
                )

                user = existing_by_personal.get(osobni_cislo)
                if user is None:
                    username = self._build_username(
                        first_name=first_name,
                        last_name=last_name,
                        osobni_cislo=osobni_cislo,
                    )
                user_id = self._create_user_sql(
                    username=username,
                    password_hash=password_hash,
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        osobni_cislo=osobni_cislo,
                    identifikacni_medium=medium or None,
                    is_active=is_active,
                    must_change_password=has_must_change_password,
                    stravovaci_skupina_id=skupina_id if has_group_fk else None,
                )
                    existing_by_personal[osobni_cislo] = {
                        "id": user_id,
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": email,
                        "identifikacni_medium": medium or "",
                        "is_active": is_active,
                        "must_change_password": has_must_change_password,
                        "stravovaci_skupina_id": skupina_id,
                    }
                    created += 1
                    continue

                changed_fields = {}
                changed = False
                username = self._build_username(
                    first_name=first_name or user.get("first_name", ""),
                    last_name=last_name or user.get("last_name", ""),
                    osobni_cislo=osobni_cislo,
                    current_user_id=user["id"],
                )
                if user["username"] != username:
                    changed_fields["username"] = username
                    changed = True
                if first_name and user["first_name"] != first_name:
                    changed_fields["first_name"] = first_name
                    changed = True
                if last_name and user["last_name"] != last_name:
                    changed_fields["last_name"] = last_name
                    changed = True
                if email and user["email"] != email:
                    changed_fields["email"] = email
                    changed = True
                if medium and (user.get("identifikacni_medium") or "") != medium:
                    changed_fields["identifikacni_medium"] = medium
                    changed = True
                if has_group_fk and skupina_id and user.get("stravovaci_skupina_id") != skupina_id:
                    changed_fields["stravovaci_skupina_id"] = skupina_id
                    changed = True
                if bool(user["is_active"]) != is_active:
                    changed_fields["is_active"] = is_active
                    changed = True
                if not check_password(osobni_cislo, user.get("password") or ""):
                    changed_fields["password"] = password_hash
                    if has_must_change_password:
                        changed_fields["must_change_password"] = True
                        changed_fields["password_changed_at"] = None
                    changed = True

                if changed:
                    self._update_user_sql(user["id"], changed_fields)
                    user.update(changed_fields)
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Import strávníků dokončen: vytvořeno {created}, aktualizováno {updated}, "
                f"nové skupiny {created_groups}."
            )
        )

    def _build_username(
        self,
        *,
        first_name: str,
        last_name: str,
        osobni_cislo: str,
        current_user_id: int | None = None,
    ) -> str:
        first = self._username_part(first_name)
        last = self._username_part(last_name)
        if first and last:
            base = f"{first}.{last}"
        elif first:
            base = f"{first}.{osobni_cislo}"
        elif last:
            base = f"{last}.{osobni_cislo}"
        else:
            base = f"uzivatel.{osobni_cislo}"

        username = base
        suffix = 1
        while self._username_exists(username, current_user_id=current_user_id):
            suffix += 1
            username = f"{base}_{suffix}"
        return username

    def _username_part(self, value: str) -> str:
        return slugify(value or "").replace("-", "")

    def _username_exists(self, username: str, current_user_id: int | None = None) -> bool:
        with connection.cursor() as cursor:
            if current_user_id:
                cursor.execute(
                    "SELECT 1 FROM users_customuser WHERE username = %s AND id <> %s LIMIT 1",
                    [username, current_user_id],
                )
            else:
                cursor.execute("SELECT 1 FROM users_customuser WHERE username = %s LIMIT 1", [username])
            return cursor.fetchone() is not None

    def _pick_identifikacni_kod(self, row: dict) -> str:
        for field in ("ID_MEDIUM", "CIP", "CKARTY", "PCKARTY", "EVID"):
            value = (row.get(field) or "").strip()
            if value and value != "0":
                return value
        return ""

    def _get_or_create_datax_groups(self):
        ucitele_personal, ucitele_created = StravovaciSkupina.objects.get_or_create(
            kod=GROUP_TEACHERS_CODE,
            defaults={
                "nazev": "Učitelé a personál",
                "typ_vzdelavani": "JINE",
            },
        )
        studenti, studenti_created = StravovaciSkupina.objects.get_or_create(
            kod=GROUP_STUDENTS_CODE,
            defaults={
                "nazev": "Studenti",
                "typ_vzdelavani": "SS",
            },
        )
        created_groups = int(ucitele_created) + int(studenti_created)
        return ucitele_personal, studenti, created_groups

    def _get_columns(self, table_name: str) -> set[str]:
        tables = set(connection.introspection.table_names())
        if table_name not in tables:
            return set()
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table_name)
        return {col.name for col in description}

    def _load_existing_users_by_personal(self) -> dict[str, dict]:
        columns = self._get_columns("users_customuser")
        select_cols = [
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "password",
            "identifikacni_medium",
            "osobni_cislo",
            "is_active",
        ]
        if "must_change_password" in columns:
            select_cols.append("must_change_password")
        if "stravovaci_skupina_id" in columns:
            select_cols.append("stravovaci_skupina_id")

        sql = (
            f"SELECT {', '.join(select_cols)} "
            "FROM users_customuser "
            "WHERE osobni_cislo IS NOT NULL AND TRIM(osobni_cislo) <> ''"
        )
        result: dict[str, dict] = {}
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            for row in rows:
                data = dict(zip(select_cols, row, strict=False))
                result[(data.get("osobni_cislo") or "").strip()] = data
        return result

    def _create_user_sql(
        self,
        *,
        username: str,
        password_hash: str,
        first_name: str,
        last_name: str,
        email: str,
        osobni_cislo: str,
        identifikacni_medium: str | None,
        is_active: bool,
        must_change_password: bool,
        stravovaci_skupina_id: int | None,
    ) -> int:
        columns = self._get_columns("users_customuser")
        insert_cols = [
            "password",
            "last_login",
            "is_superuser",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_active",
            "date_joined",
            "identifikacni_medium",
            "osobni_cislo",
        ]
        values = [
            password_hash,
            None,
            False,
            username,
            first_name,
            last_name,
            email,
            False,
            is_active,
            timezone.now(),
            identifikacni_medium,
            osobni_cislo,
        ]
        if "must_change_password" in columns:
            insert_cols.append("must_change_password")
            values.append(must_change_password)
        if "stravovaci_skupina_id" in columns:
            insert_cols.append("stravovaci_skupina_id")
            values.append(stravovaci_skupina_id)

        placeholders = ", ".join(["%s"] * len(insert_cols))
        sql = f"INSERT INTO users_customuser ({', '.join(insert_cols)}) VALUES ({placeholders}) RETURNING id"
        with connection.cursor() as cursor:
            cursor.execute(sql, values)
            return cursor.fetchone()[0]

    def _update_user_sql(self, user_id: int, changed_fields: dict) -> None:
        if not changed_fields:
            return
        set_parts = []
        values = []
        for key, value in changed_fields.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(user_id)
        sql = f"UPDATE users_customuser SET {', '.join(set_parts)} WHERE id = %s"
        with connection.cursor() as cursor:
            cursor.execute(sql, values)

    def _to_is_active(self, platny_raw) -> bool:
        value = (str(platny_raw or "")).strip().upper()
        if value in {"N", "F", "0"}:
            return False
        return True

    def _read_dbf_rows(self, dbf_path: Path) -> list[dict]:
        with dbf_path.open("rb") as fh:
            header = fh.read(32)
            if len(header) < 32:
                raise CommandError(f"Poškozená DBF hlavička: {dbf_path}")

            _, _, _, _, record_count, header_len, record_len = struct.unpack("<BBBBIHH20x", header)

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
        if field.field_type == "L":
            char = raw[:1].upper()
            return char in {b"T", b"Y", b"1"}
        if field.field_type == "D":
            return raw.decode("ascii", errors="ignore").strip()
        return raw.decode(DBF_ENCODING, errors="ignore").strip()
