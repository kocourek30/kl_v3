from django.db import models
from jidelnicek.models import Jidlo
from django.conf import settings
from django.utils import timezone


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


class PrijemSkladu(models.Model):
    """
    Hlavička příjmu zboží (jedna dodávka).
    """
    datum = models.DateTimeField(default=timezone.now, verbose_name="Datum příjmu")
    popis = models.CharField(max_length=255, blank=True, verbose_name="Poznámka")
    vytvoril = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Vytvořil"
    )
    uzavreny = models.BooleanField(default=False, verbose_name="Uzavřený (převod do skladu)")

    class Meta:
        verbose_name = "Příjem na sklad"
        verbose_name_plural = "Příjmy na sklad"

    def __str__(self):
        return f"Příjem #{self.id} – {self.datum.strftime('%d.%m.%Y')}"


class PolozkaPrijmu(models.Model):
    prijem = models.ForeignKey(
        PrijemSkladu,
        on_delete=models.CASCADE,
        related_name="polozky",
        verbose_name="Příjem"
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT, verbose_name="Surovina")
    mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství",
        help_text="Ve stejné jednotce jako surovina."
    )

    class Meta:
        verbose_name = "Položka příjmu"
        verbose_name_plural = "Položky příjmu"

    def __str__(self):
        return f"{self.mnozstvi} {self.surovina.jednotka} {self.surovina.nazev}"


class Inventura(models.Model):
    datum = models.DateTimeField(
        default=timezone.now,
        verbose_name="Datum inventury",
    )
    popis = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Poznámka",
    )
    vytvoril = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Vytvořil",
    )
    uzavrena = models.BooleanField(
        default=False,
        verbose_name="Uzavřená (promítnuto do skladu)",
    )

    class Meta:
        verbose_name = "Inventura"
        verbose_name_plural = "Inventury"
        ordering = ["-datum"]

    def __str__(self):
        return f"Inventura #{self.id} – {self.datum.strftime('%d.%m.%Y')}"

    @property
    def je_uzavrena(self):
        return self.uzavrena
from decimal import Decimal
from django.db import models

from decimal import Decimal
from django.db import models

class PolozkaInventury(models.Model):
    inventura = models.ForeignKey(
        Inventura,
        on_delete=models.CASCADE,
        related_name="polozky",
        verbose_name="Inventura",
    )
    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        verbose_name="Surovina",
    )
    stav_pred = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Stav před",
        null=True,
        blank=True,
    )
    fyzicky_stav = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Fyzický stav",
    )
    rozdil = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Rozdíl",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Položka inventury"
        verbose_name_plural = "Položky inventury"
        unique_together = ("inventura", "surovina")

    def __str__(self):
        return f"{self.surovina.nazev}: {self.fyzicky_stav} {self.surovina.jednotka}"

    def save(self, *args, **kwargs):
        # stav_pred už je předvyplněný při vytvoření inventury,
        # tady jen dopočítáme rozdíl
        fyz = self.fyzicky_stav or Decimal("0")
        pred = self.stav_pred or Decimal("0")
        self.rozdil = fyz - pred
        super().save(*args, **kwargs)


class InventurniDoklad(Inventura):
    class Meta:
        proxy = True
        verbose_name = "Inventurní doklad"
        verbose_name_plural = "Inventurní doklady"
