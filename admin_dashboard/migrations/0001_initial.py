from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True, verbose_name="Slug úlohy")),
                ("name", models.CharField(max_length=200, verbose_name="Název")),
                ("category", models.CharField(choices=[("users", "Uživatelé"), ("orders", "Objednávky"), ("menu", "Jídelníček"), ("billing", "Fakturace"), ("warehouse", "Sklad"), ("system", "Systém"), ("demo", "Demo / seed")], default="system", max_length=40, verbose_name="Kategorie")),
                ("command_name", models.CharField(blank=True, max_length=200, verbose_name="Management command")),
                ("description", models.TextField(blank=True, verbose_name="Popis")),
                ("expected_interval_hours", models.PositiveIntegerField(blank=True, null=True, verbose_name="Doporučený interval (h)")),
                ("default_options", models.JSONField(blank=True, default=dict, verbose_name="Výchozí parametry")),
                ("is_enabled", models.BooleanField(default=True, verbose_name="Aktivní")),
                ("allow_manual_run", models.BooleanField(default=True, verbose_name="Lze spustit ručně")),
                ("is_quick_link", models.BooleanField(default=False, verbose_name="Pouze rychlý odkaz")),
                ("target_url_name", models.CharField(blank=True, max_length=200, verbose_name="Cílový URL name")),
                ("target_url", models.CharField(blank=True, max_length=255, verbose_name="Cílová URL")),
                ("notes", models.TextField(blank=True, verbose_name="Poznámky")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Úloha dashboardu",
                "verbose_name_plural": "Úlohy dashboardu",
                "ordering": ("category", "name"),
            },
        ),
        migrations.CreateModel(
            name="TaskRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("command_name", models.CharField(blank=True, max_length=200, verbose_name="Command")),
                ("status", models.CharField(choices=[("running", "Spuštěno"), ("success", "Úspěch"), ("failed", "Chyba"), ("skipped", "Přeskočeno")], default="running", max_length=20)),
                ("trigger_source", models.CharField(choices=[("manual", "Ruční"), ("system", "Systémová")], default="manual", max_length=20, verbose_name="Zdroj spuštění")),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="Začátek")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="Konec")),
                ("duration_seconds", models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name="Doba běhu (s)")),
                ("summary", models.CharField(blank=True, max_length=255, verbose_name="Shrnutí")),
                ("stdout", models.TextField(blank=True, verbose_name="Standardní výstup")),
                ("stderr", models.TextField(blank=True, verbose_name="Chyby / stderr")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="Metadata")),
                ("task", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="admin_dashboard.dashboardtask", verbose_name="Úloha")),
                ("triggered_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Spustil")),
            ],
            options={
                "verbose_name": "Běh úlohy",
                "verbose_name_plural": "Běhy úloh",
                "ordering": ("-started_at",),
            },
        ),
    ]
