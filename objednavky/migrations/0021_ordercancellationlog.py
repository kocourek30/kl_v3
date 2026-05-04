from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("objednavky", "0020_alter_orderitem_unique_together_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderCancellationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("datum_vydeje", models.DateField(verbose_name="Datum výdeje")),
                ("cancelled_at", models.DateTimeField(auto_now_add=True, verbose_name="Zrušeno")),
                ("cancelled_late", models.BooleanField(default=False, verbose_name="Po uzávěrce")),
                ("reason", models.CharField(blank=True, default="", max_length=255, verbose_name="Důvod")),
                ("items_count", models.PositiveIntegerField(default=0, verbose_name="Počet položek")),
                ("total_price", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Celková cena")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="order_cancellation_logs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Uživatel",
                    ),
                ),
            ],
            options={
                "verbose_name": "Log storna objednávky",
                "verbose_name_plural": "Logy storen objednávek",
                "ordering": ["-cancelled_at"],
            },
        ),
    ]
