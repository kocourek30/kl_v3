import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LicenseConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("instance_id", models.UUIDField(default=uuid.uuid4, editable=False, verbose_name="ID instalace")),
                ("license_blob", models.TextField(blank=True, verbose_name="Podepsaná licence")),
                ("license_id", models.CharField(blank=True, max_length=120, verbose_name="ID licence")),
                ("customer_name", models.CharField(blank=True, max_length=255, verbose_name="Zákazník")),
                ("organization_name", models.CharField(blank=True, max_length=255, verbose_name="Organizace")),
                ("license_type", models.CharField(blank=True, max_length=60, verbose_name="Typ licence")),
                ("valid_from", models.DateField(blank=True, null=True, verbose_name="Platná od")),
                ("valid_until", models.DateField(blank=True, null=True, verbose_name="Platná do")),
                ("support_until", models.DateField(blank=True, null=True, verbose_name="Podpora do")),
                ("last_verified_at", models.DateTimeField(blank=True, null=True, verbose_name="Poslední ověření")),
                ("status", models.CharField(choices=[("missing", "Bez licence"), ("active", "Aktivní"), ("grace", "V ochranné lhůtě"), ("support", "Aktivní bez podpory"), ("invalid", "Neplatná"), ("expired", "Propadlá")], default="missing", max_length=20, verbose_name="Stav licence")),
                ("status_message", models.TextField(blank=True, verbose_name="Stavová zpráva")),
                ("cached_payload", models.JSONField(blank=True, default=dict, verbose_name="Obsah licence")),
                ("activated_at", models.DateTimeField(blank=True, null=True, verbose_name="Aktivováno")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("activated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Aktivoval")),
            ],
            options={"verbose_name": "Licence aplikace", "verbose_name_plural": "Licence aplikace"},
        ),
        migrations.CreateModel(
            name="LicenseEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("verify", "Ověření"), ("activate", "Aktivace"), ("reject", "Odmítnutí"), ("status", "Stav")], max_length=20, verbose_name="Typ")),
                ("status", models.CharField(blank=True, max_length=20, verbose_name="Stav")),
                ("message", models.CharField(blank=True, max_length=255, verbose_name="Zpráva")),
                ("details", models.JSONField(blank=True, default=dict, verbose_name="Detaily")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Vytvořeno")),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Uživatel")),
                ("config", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="licencovani.licenseconfig", verbose_name="Licence")),
            ],
            options={"verbose_name": "Událost licence", "verbose_name_plural": "Události licence", "ordering": ("-created_at",)},
        ),
    ]

