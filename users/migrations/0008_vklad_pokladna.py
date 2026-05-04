from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("pokladna", "0007_pokladna_hotovostni_zustatek_pokladnismazanapolozka"),
        ("users", "0007_vklad_zpusob_uhrady"),
    ]

    operations = [
        migrations.AddField(
            model_name="vklad",
            name="pokladna",
            field=models.ForeignKey(
                blank=True,
                help_text="Vyplňuje se u vkladů vytvořených přes pokladní modul.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="vklady_kont",
                to="pokladna.pokladna",
                verbose_name="Pokladna",
            ),
        ),
    ]
