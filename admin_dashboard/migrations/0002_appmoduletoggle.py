from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppModuleToggle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True, verbose_name="Slug modulu")),
                ("name", models.CharField(max_length=200, verbose_name="Název modulu")),
                ("description", models.TextField(blank=True, verbose_name="Popis")),
                ("app_labels", models.JSONField(blank=True, default=list, verbose_name="Admin app labels")),
                ("route_prefixes", models.JSONField(blank=True, default=list, verbose_name="Route prefixy")),
                ("enabled", models.BooleanField(default=True, verbose_name="Povoleno")),
                ("notes", models.TextField(blank=True, verbose_name="Poznámky")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Přepínač modulu",
                "verbose_name_plural": "Přepínače modulů",
                "ordering": ("name",),
            },
        ),
    ]
