from django.db import models
from jidelnicek.models import Jidlo


class Surovina(models.Model):
    JEDNOTKY = [
        ('kg', 'Kilogram'),
        ('l', 'Litr'),
        ('ks', 'Kus'),
    ]

    nazev = models.CharField(max_length=100, unique=True, verbose_name="Název suroviny")
    jednotka = models.CharField(max_length=10, choices=JEDNOTKY, verbose_name="Jednotka")

    class Meta:
        verbose_name = "Surovina"
        verbose_name_plural = "Suroviny"

    def __str__(self):
        return f"{self.nazev} ({self.jednotka})"


class StavSkladu(models.Model):
    surovina = models.OneToOneField(Surovina, on_delete=models.CASCADE, related_name="stav")
    mnozstvi = models.DecimalField(max_digits=10, decimal_places=3, verbose_name="Množství na skladě")
    min_mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=0,
        verbose_name="Minimální množství (pro upozornění)",
    )

    class Meta:
        verbose_name = "Stav skladu"
        verbose_name_plural = "Stavy skladu"

    def __str__(self):
        return f"{self.surovina} – {self.mnozstvi} {self.surovina.jednotka}"

class SkladDashboard(models.Model):
    """
    Pseudo-model jen pro admin dashboard (nebude se ukládat).
    """
    class Meta:
        managed = False
        verbose_name = "Skladový dashboard"
        verbose_name_plural = "Skladový dashboard"


class RecepturaPolozka(models.Model):
    jidlo = models.ForeignKey(Jidlo, on_delete=models.CASCADE, related_name="receptura")
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT, related_name="v_receptech")
    mnozstvi_na_porci = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství na 1 porci",
        help_text="Ve stejné jednotce jako surovina (kg, l, ks).",
    )

    class Meta:
        verbose_name = "Položka receptury"
        verbose_name_plural = "Položky receptur"
        unique_together = ("jidlo", "surovina")

    def __str__(self):
        return f"{self.jidlo} – {self.mnozstvi_na_porci} {self.surovina.jednotka} {self.surovina.nazev}/porce"
