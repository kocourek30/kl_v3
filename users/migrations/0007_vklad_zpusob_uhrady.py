from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_stravovaciskupina_customuser_stravovaci_skupina"),
    ]

    operations = [
        migrations.AddField(
            model_name="vklad",
            name="zpusob_uhrady",
            field=models.CharField(
                blank=True,
                choices=[
                    ("HOTOVOST", "Hotově"),
                    ("KARTA", "Kartou"),
                    ("QR", "QR platbou"),
                ],
                default="",
                help_text="Vyplňuje se u běžných vkladů na konto. Systémové nulování a čerpání konta jej mít nemusí.",
                max_length=20,
                verbose_name="Způsob úhrady",
            ),
        ),
    ]
