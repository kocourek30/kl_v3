from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fakturace", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fakturacnipolozka",
            name="username_snapshot",
            field=models.CharField(blank=True, default="", max_length=150, verbose_name="Přihlašovací jméno"),
        ),
    ]
