from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pokladna", "0007_pokladna_hotovostni_zustatek_pokladnismazanapolozka"),
    ]

    operations = [
        migrations.AddField(
            model_name="pokladnidoklad",
            name="typ_dokladu",
            field=models.CharField(
                choices=[
                    ("PRODEJ", "Prodej"),
                    ("VKLAD_KONTA", "Vklad na konto"),
                ],
                db_index=True,
                default="PRODEJ",
                max_length=20,
                verbose_name="Typ dokladu",
            ),
        ),
    ]
