from django.db import models


class PrepoctyDummy(models.Model):
    """Prázdný model jen pro zobrazení sekce v admin menu."""

    class Meta:
        managed = False  # žádná tabulka v DB
        verbose_name = "Přepočet"
        verbose_name_plural = "Přepočty"
