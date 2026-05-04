from django.conf import settings
from django.db import models
from django.utils import timezone

from objednavky.models import Order


class VydejOrder(Order):
    class Meta:
        proxy = True
        app_label = "vydej"
        verbose_name = "Výdej objednávky"
        verbose_name_plural = "Výdej objednávek"


class PrehledProKuchyni(Order):
    class Meta:
        proxy = True
        app_label = "vydej"
        verbose_name = "Přehled pro kuchyni"
        verbose_name_plural = "Přehled pro kuchyni"


class VydejSettings(models.Model):
    timeout_seconds = models.PositiveIntegerField(
        default=20,
        verbose_name="Timeout výdeje (sekundy)",
        help_text="Po kolika sekundách od nalezení objednávky se automaticky vydá.",
    )

    class Meta:
        verbose_name = "Nastavení výdeje"
        verbose_name_plural = "Nastavení výdeje"

    def __str__(self):
        return f"Timeout {self.timeout_seconds} s"


class VydejniUctenka(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="vydejni_uctenka",
        verbose_name="Objednávka",
    )
    datum_vydeje = models.DateTimeField(
        default=timezone.now,
        verbose_name="Datum a čas výdeje",
    )
    vydal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vydane_objednavky",
        verbose_name="Vydal",
    )
    celkova_cena = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name="Celková cena",
    )
    celkova_dotace = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name="Celková dotace",
    )
    poznamka = models.TextField(blank=True, null=True, verbose_name="Poznámka")

    class Meta:
        ordering = ["-datum_vydeje"]
        verbose_name = "Výdejní účtenka"
        verbose_name_plural = "Výdejní účtenky"

    def __str__(self):
        return f"Účtenka #{self.pk} k objednávce #{self.order_id}"


class PolozkaUctenky(models.Model):
    uctenka = models.ForeignKey(
        VydejniUctenka,
        on_delete=models.CASCADE,
        related_name="polozky",
        verbose_name="Účtenka",
    )
    nazev_jidla = models.CharField(max_length=255, verbose_name="Název jídla")
    druh_jidla = models.CharField(max_length=100, verbose_name="Druh jídla")
    mnozstvi = models.PositiveIntegerField(verbose_name="Množství")
    cena_za_kus = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        verbose_name="Cena za kus",
    )
    dotace_za_kus = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name="Dotace za kus",
    )

    class Meta:
        verbose_name = "Položka účtenky"
        verbose_name_plural = "Položky účtenky"

    def __str__(self):
        return f"{self.nazev_jidla} ({self.mnozstvi}×)"

    def celkova_cena(self):
        return self.mnozstvi * self.cena_za_kus


class StornovaneObjednavky(Order):
    class Meta:
        proxy = True
        app_label = "vydej"
        verbose_name = "Stornovaná objednávka"
        verbose_name_plural = "Stornované objednávky"
