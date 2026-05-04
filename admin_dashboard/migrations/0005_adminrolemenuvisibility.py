from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin_dashboard", "0004_adminviewaccess_levels"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminRoleMenuVisibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hidden_area_slugs", models.JSONField(blank=True, default=list, verbose_name="Skryté oblasti")),
                ("notes", models.TextField(blank=True, verbose_name="Poznámky")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role_group",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="admin_menu_visibility",
                        to="auth.group",
                        verbose_name="Role / Django skupina",
                    ),
                ),
            ],
            options={
                "verbose_name": "Viditelnost adminu pro roli",
                "verbose_name_plural": "Viditelnost adminu pro role",
                "ordering": ("role_group__name",),
            },
        ),
    ]
