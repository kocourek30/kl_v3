from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.exceptions import ValidationError
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


class MesicniAnketa(models.Model):
    MESIC_CHOICES = (
        (1, "Leden"),
        (2, "Únor"),
        (3, "Březen"),
        (4, "Duben"),
        (5, "Květen"),
        (6, "Červen"),
        (7, "Červenec"),
        (8, "Srpen"),
        (9, "Září"),
        (10, "Říjen"),
        (11, "Listopad"),
        (12, "Prosinec"),
    )

    nazev = models.CharField(max_length=255, verbose_name="Název hlasování")
    popis = models.TextField(blank=True, verbose_name="Popis")
    rok = models.PositiveSmallIntegerField(verbose_name="Rok")
    mesic = models.PositiveSmallIntegerField(choices=MESIC_CHOICES, verbose_name="Měsíc")
    navrhujici_trida = models.CharField(max_length=120, blank=True, verbose_name="Navrhující třída")
    hlasovani_od = models.DateField(verbose_name="Hlasování od")
    hlasovani_do = models.DateField(verbose_name="Hlasování do")
    aktivni = models.BooleanField(default=True, verbose_name="Aktivní")
    vytvoreno = models.DateTimeField(default=timezone.now, verbose_name="Vytvořeno")

    class Meta:
        verbose_name = "Měsíční anketa menu"
        verbose_name_plural = "Měsíční ankety menu"
        ordering = ("-rok", "-mesic", "-vytvoreno")
        constraints = [
            models.UniqueConstraint(
                fields=("rok", "mesic"),
                name="ankety_mesicnianketa_unique_obdobi",
            )
        ]

    def __str__(self):
        return f"{self.nazev} ({self.get_mesic_display()} {self.rok})"

    def clean(self):
        super().clean()
        if self.hlasovani_do and self.hlasovani_od and self.hlasovani_do < self.hlasovani_od:
            raise ValidationError({"hlasovani_do": "Datum do musí být stejné nebo pozdější než datum od."})

    def is_open(self, target_date=None):
        target_date = target_date or timezone.localdate()
        return self.aktivni and self.hlasovani_od <= target_date <= self.hlasovani_do


class MesicniAnketaVarianta(models.Model):
    anketa = models.ForeignKey(
        MesicniAnketa,
        on_delete=models.CASCADE,
        related_name="varianty",
        verbose_name="Měsíční anketa",
    )
    nazev = models.CharField(max_length=255, verbose_name="Název varianty")
    popis = models.TextField(blank=True, verbose_name="Popis varianty")
    poradi = models.PositiveIntegerField(default=10, verbose_name="Pořadí")

    class Meta:
        verbose_name = "Varianta měsíční ankety"
        verbose_name_plural = "Varianty měsíční ankety"
        ordering = ("poradi", "id")

    def __str__(self):
        return self.nazev


class MesicniAnketaHlas(models.Model):
    anketa = models.ForeignKey(
        MesicniAnketa,
        on_delete=models.CASCADE,
        related_name="hlasy",
        verbose_name="Měsíční anketa",
    )
    varianta = models.ForeignKey(
        MesicniAnketaVarianta,
        on_delete=models.PROTECT,
        related_name="hlasy",
        verbose_name="Zvolená varianta",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="anketni_hlasy",
        verbose_name="Strávník",
    )
    hlasovano = models.DateTimeField(default=timezone.now, verbose_name="Hlasováno")

    class Meta:
        verbose_name = "Hlas v měsíční anketě"
        verbose_name_plural = "Hlasy v měsíční anketě"
        ordering = ("-hlasovano",)
        constraints = [
            models.UniqueConstraint(
                fields=("anketa", "user"),
                name="ankety_mesicnianketahlas_unique_anketa_user",
            )
        ]

    def __str__(self):
        return f"{self.user} -> {self.varianta}"

    def clean(self):
        super().clean()
        if self.varianta_id and self.anketa_id and self.varianta.anketa_id != self.anketa_id:
            raise ValidationError({"varianta": "Vybraná varianta nepatří do zvolené ankety."})
