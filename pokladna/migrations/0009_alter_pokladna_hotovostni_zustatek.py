from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pokladna", "0008_pokladnidoklad_typ_dokladu"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pokladna",
            name="hotovostni_zustatek",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Výchozí hotovost v pokladně. Používá se pro provozní a finanční přehledy.",
                max_digits=12,
                verbose_name="Pokladní hotovost",
            ),
        ),
    ]
