# Generated manually for import audit tracking.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jidelnicek", "0010_doplnit_vychozi_ikony_jidel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MenuImportRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("txt", "Ruční TXT import"),
                            ("datax", "Datax DBF import"),
                            ("auto", "Automatický import"),
                        ],
                        default="txt",
                        max_length=20,
                        verbose_name="Zdroj importu",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("running", "Probíhá"), ("success", "Hotovo"), ("failed", "Chyba")],
                        db_index=True,
                        default="running",
                        max_length=20,
                        verbose_name="Stav",
                    ),
                ),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Spuštěno")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="Dokončeno")),
                ("dry_run", models.BooleanField(default=False, verbose_name="Dry-run")),
                ("rows_read", models.PositiveIntegerField(default=0, verbose_name="Načtených řádků")),
                ("rows_after_merge", models.PositiveIntegerField(default=0, verbose_name="Řádků po sloučení")),
                ("menu_days", models.PositiveIntegerField(default=0, verbose_name="Dnů v importu")),
                ("menus_created", models.PositiveIntegerField(default=0, verbose_name="Vytvořených jídelníčků")),
                ("foods_created", models.PositiveIntegerField(default=0, verbose_name="Nových jídel")),
                ("items_created", models.PositiveIntegerField(default=0, verbose_name="Vytvořených položek")),
                ("summary", models.CharField(blank=True, max_length=255, verbose_name="Souhrn")),
                ("error_message", models.TextField(blank=True, verbose_name="Chybová zpráva")),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="menu_import_runs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Spustil",
                    ),
                ),
            ],
            options={
                "verbose_name": "Běh importu jídelníčku",
                "verbose_name_plural": "Běhy importu jídelníčku",
                "ordering": ("-started_at",),
            },
        ),
    ]
