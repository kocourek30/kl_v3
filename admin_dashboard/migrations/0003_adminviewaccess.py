from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin_dashboard", "0002_appmoduletoggle"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminViewAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True, verbose_name="Slug oblasti")),
                ("name", models.CharField(max_length=200, verbose_name="Název oblasti")),
                ("description", models.TextField(blank=True, verbose_name="Popis")),
                ("app_labels", models.JSONField(blank=True, default=list, verbose_name="Admin app labels")),
                ("route_prefixes", models.JSONField(blank=True, default=list, verbose_name="Route prefixy")),
                ("notes", models.TextField(blank=True, verbose_name="Poznámky")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("allowed_groups", models.ManyToManyField(blank=True, related_name="admin_view_accesses", to="auth.group", verbose_name="Povolené Django skupiny")),
            ],
            options={
                "verbose_name": "Přístup do admin oblasti",
                "verbose_name_plural": "Přístupy do admin oblastí",
                "ordering": ("name",),
            },
        ),
    ]
