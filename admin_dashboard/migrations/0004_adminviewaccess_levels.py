from django.db import migrations, models


def copy_allowed_groups_to_control(apps, schema_editor):
    AdminViewAccess = apps.get_model("admin_dashboard", "AdminViewAccess")

    for access in AdminViewAccess.objects.all():
        access.control_groups.set(access.allowed_groups.all())


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("admin_dashboard", "0003_adminviewaccess"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminviewaccess",
            name="control_groups",
            field=models.ManyToManyField(
                blank=True,
                related_name="admin_view_accesses_control",
                to="auth.group",
                verbose_name="Skupiny pro plnou kontrolu",
            ),
        ),
        migrations.AddField(
            model_name="adminviewaccess",
            name="view_groups",
            field=models.ManyToManyField(
                blank=True,
                related_name="admin_view_accesses_view",
                to="auth.group",
                verbose_name="Skupiny pro náhled",
            ),
        ),
        migrations.AddField(
            model_name="adminviewaccess",
            name="write_groups",
            field=models.ManyToManyField(
                blank=True,
                related_name="admin_view_accesses_write",
                to="auth.group",
                verbose_name="Skupiny pro správu",
            ),
        ),
        migrations.RunPython(copy_allowed_groups_to_control, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="adminviewaccess",
            name="allowed_groups",
        ),
    ]
