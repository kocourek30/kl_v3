from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from objednavky.models import OrderItem


class AnketniOtazka(models.Model):
    text = models.CharField(max_length=255, verbose_name="Text otázky")
    napoveda = models.CharField(max_length=255, blank=True, verbose_name="Nápověda")
    aktivni = models.BooleanField(default=True, verbose_name="Aktivní")
    povinna = models.BooleanField(default=True, verbose_name="Povinná")
    poradi = models.PositiveIntegerField(default=10, verbose_name="Pořadí")

    class Meta:
        verbose_name = "Anketní otázka"
        verbose_name_plural = "Anketní otázky"
        ordering = ("poradi", "id")

    def __str__(self):
        return self.text


class HodnoceniJidla(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hodnoceni_jidel",
        verbose_name="Strávník",
    )
    order_item = models.OneToOneField(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="hodnoceni",
        verbose_name="Vydaná položka objednávky",
    )
    datum_vydeje = models.DateField(verbose_name="Datum výdeje")
    jidlo_nazev = models.CharField(max_length=255, verbose_name="Jídlo")
    poznamka = models.TextField(blank=True, verbose_name="Poznámka strávníka")
    vytvoreno = models.DateTimeField(default=timezone.now, verbose_name="Vyplněno")

    class Meta:
        verbose_name = "Hodnocení jídla"
        verbose_name_plural = "Hodnocení jídel"
        ordering = ("-vytvoreno",)

    def __str__(self):
        return f"{self.jidlo_nazev} - {self.user} ({self.datum_vydeje:%d.%m.%Y})"

    @property
    def prumer(self):
        odpovedi = list(self.odpovedi.all())
        if not odpovedi:
            return None
        return sum(o.znamka for o in odpovedi) / len(odpovedi)


class OdpovedHodnoceni(models.Model):
    hodnoceni_jidla = models.ForeignKey(
        HodnoceniJidla,
        on_delete=models.CASCADE,
        related_name="odpovedi",
        verbose_name="Hodnocení",
    )
    otazka = models.ForeignKey(
        AnketniOtazka,
        on_delete=models.PROTECT,
        related_name="odpovedi",
        verbose_name="Otázka",
    )
    znamka = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name="Hodnocení 1-5",
    )

    class Meta:
        verbose_name = "Odpověď hodnocení"
        verbose_name_plural = "Odpovědi hodnocení"
        unique_together = ("hodnoceni_jidla", "otazka")
        ordering = ("otazka__poradi", "otazka_id")

    def __str__(self):
        return f"{self.otazka}: {self.znamka}/5"
