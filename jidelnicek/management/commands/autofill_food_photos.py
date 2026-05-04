from __future__ import annotations

from django.db.models import Q
from django.core.management.base import BaseCommand

from jidelnicek.ai_photos import generate_food_photo, generate_food_photo_proposal
from jidelnicek.models import Jidlo


class Command(BaseCommand):
    help = "Dávkově vytvoří AI fotky jídel přes OpenAI Images API (výchozí režim: návrhy ke schválení)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Kolik jídel maximálně zpracovat (0 = bez limitu).",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Přepíše i existující fotky jídel.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Jen vypíše, co by se zpracovalo, bez uložení fotek.",
        )
        parser.add_argument(
            "--apply-direct",
            action="store_true",
            help="Aplikuje fotky rovnou do jídla (bez schvalovací fronty).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=90,
            help="Timeout jednoho requestu na API v sekundách.",
        )

    def handle(self, *args, **options):
        overwrite = bool(options["overwrite"])
        dry_run = bool(options["dry_run"])
        timeout = int(options["timeout"])
        limit = int(options["limit"] or 0)
        apply_direct = bool(options["apply_direct"])

        queryset = Jidlo.objects.select_related("druh").order_by("nazev")
        if not overwrite:
            queryset = queryset.filter(Q(foto__isnull=True) | Q(foto=""))
        if limit > 0:
            queryset = queryset[:limit]

        foods = list(queryset)
        if not foods:
            self.stdout.write(self.style.WARNING("Nebyla nalezena žádná jídla ke zpracování."))
            return

        self.stdout.write(
            self.style.NOTICE(
                "Auto-foto start: "
                f"položek={len(foods)}, overwrite={overwrite}, dry_run={dry_run}, "
                f"mode={'direct' if apply_direct else 'proposal'}"
            )
        )

        updated = 0
        skipped = 0
        failed = 0

        for index, food in enumerate(foods, start=1):
            if apply_direct:
                result = generate_food_photo(
                    food,
                    overwrite=overwrite,
                    dry_run=dry_run,
                    timeout=timeout,
                )
            else:
                result = generate_food_photo_proposal(
                    food,
                    overwrite=overwrite,
                    dry_run=dry_run,
                    timeout=timeout,
                )
            if result.status == "updated":
                updated += 1
                style = self.style.SUCCESS
            elif result.status == "failed":
                failed += 1
                style = self.style.ERROR
            else:
                skipped += 1
                style = self.style.WARNING

            self.stdout.write(style(f"[{index}/{len(foods)}] {food.nazev}: {result.detail}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Hotovo. Updated={updated}, skipped={skipped}, failed={failed}."
            )
        )
