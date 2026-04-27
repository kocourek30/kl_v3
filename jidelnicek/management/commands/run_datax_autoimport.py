from __future__ import annotations

import os
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Automatizační wrapper pro DATAx import jídelníčku. "
        "Importuje aktuální a následující měsíc bez mazání transakcí."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dbf-path",
            default=os.getenv("DATAX_JIDELNIK_DBF", r"E:\datax\JIDELNIK.DBF"),
            help="Cesta k DATAx JIDELNIK.DBF",
        )
        parser.add_argument(
            "--months-ahead",
            type=int,
            default=1,
            help="Kolik měsíců dopředu importovat navíc (default 1 = aktuální + další měsíc).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Spustí import jen nanečisto.",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        months_ahead = max(0, int(options["months_ahead"]))
        target_months = {(today.year, today.month)}

        cursor = today.replace(day=15)
        for _ in range(months_ahead):
            cursor = (cursor + timedelta(days=31)).replace(day=15)
            target_months.add((cursor.year, cursor.month))

        grouped_by_year: dict[int, set[int]] = {}
        for year, month in sorted(target_months):
            grouped_by_year.setdefault(year, set()).add(month)

        for year, months in grouped_by_year.items():
            months_raw = ",".join(str(month) for month in sorted(months))
            self.stdout.write(
                self.style.NOTICE(
                    f"Autoimport DATAx: rok {year}, měsíce {months_raw}, zdroj {options['dbf_path']}"
                )
            )
            call_command(
                "import_datax_jidelnik",
                dbf_path=options["dbf_path"],
                year=year,
                months=months_raw,
                dry_run=options["dry_run"],
                datax_merge_blocks=True,
            )

        self.stdout.write(self.style.SUCCESS("Autoimport DATAx dokončen."))
