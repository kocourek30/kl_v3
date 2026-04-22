from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0005_adminrolemenuvisibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="adminrolemenuvisibility",
            name="hidden_app_labels",
            field=models.JSONField(blank=True, default=list, verbose_name="Skryté appky"),
        ),
        migrations.AddField(
            model_name="adminrolemenuvisibility",
            name="hidden_menu_item_keys",
            field=models.JSONField(blank=True, default=list, verbose_name="Skryté admin položky"),
        ),
    ]
