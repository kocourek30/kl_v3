from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jidelnicek", "0011_menuimportrun"),
    ]

    operations = [
        migrations.AlterField(
            model_name="menuimportrun",
            name="source",
            field=models.CharField(
                choices=[
                    ("datax", "Datax DBF import"),
                    ("auto", "Automatický import"),
                ],
                default="datax",
                max_length=20,
                verbose_name="Zdroj importu",
            ),
        ),
    ]
