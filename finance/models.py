from django.db import models


class FinancniDashboard(models.Model):
    class Meta:
        managed = False
        verbose_name = "Finanční dashboard"
        verbose_name_plural = "Finanční dashboard"

    def __str__(self):
        return "Finanční dashboard"
