from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from jidelnicek.models import Jidlo
from users.models import StravovaciSkupina


class Surovina(models.Model):
    JEDNOTKY = [
        ("kg", "Kilogram"),
        ("l", "Litr"),
        ("ks", "Kus"),
    ]

    # Skupiny spotřebního koše – podle vyhlášky (zjednodušeně)
    SKUPINA_SK = [
        ("NONE", "Nezapočítávat do spotřebního koše"),
        ("MASO", "Maso"),
        ("RYBY", "Ryby"),
        ("MLEX", "Mléko a mléčné výrobky"),
        ("OBIL", "Obiloviny"),
        ("LUST", "Luštěniny"),
        ("ZEL", "Zelenina"),
        ("OVO", "Ovoce"),
        ("BRAM", "Brambory"),
        ("TUKY", "Tuky"),
        ("CUKR", "Cukr"),
    ]

    nazev = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Název suroviny",
    )
    jednotka = models.CharField(
        max_length=10,
        choices=JEDNOTKY,
        verbose_name="Jednotka",
    )

    # Metadata pro spotřební koš
    skupina_sk = models.CharField(
        max_length=10,
        choices=SKUPINA_SK,
        default="NONE",
        verbose_name="Skupina spotřebního koše",
        help_text="Do jaké skupiny spotřebního koše se surovina započítává.",
    )
    koeficient_sk = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        default=Decimal("1.000"),
        verbose_name="Koeficient pro spotřební koš",
        help_text=(
            "1 = celé množství; <1 např. uzeniny dle % masa; >1 např. "
            "sušené/mléčné koncentráty dle metodiky."
        ),
    )

    class Meta:
        verbose_name = "Surovina"
        verbose_name_plural = "Suroviny"

    def __str__(self):
        return f"{self.nazev} ({self.jednotka})"


class StavSkladu(models.Model):
    surovina = models.OneToOneField(
        Surovina,
        on_delete=models.CASCADE,
        related_name="stav",
    )
    mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství na skladě",
    )
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


class PohybSkladu(models.Model):
    TYPY = [
        ("PRIJEM", "Příjem"),
        ("VYDEJ", "Výdej do výroby"),
        ("INVENTURA", "Inventura"),
    ]

    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        related_name="pohyby",
        verbose_name="Surovina",
    )
    datum = models.DateTimeField(
        default=timezone.now,
        verbose_name="Datum pohybu",
    )
    typ = models.CharField(
        max_length=20,
        choices=TYPY,
        verbose_name="Typ pohybu",
    )
    mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství (kladné číslo)",
    )
    vydejka = models.ForeignKey(
        "Vydejka",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pohyby",
        verbose_name="Výdejka",
    )
    prijem = models.ForeignKey(
        "PrijemSkladu",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pohyby",
        verbose_name="Příjem",
    )
    poznamka = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Poznámka",
    )

    class Meta:
        verbose_name = "Pohyb na skladu"
        verbose_name_plural = "Pohyby na skladu"
        ordering = ["-datum"]

    def __str__(self):
        return f"{self.get_typ_display()} – {self.mnozstvi} {self.surovina.jednotka} {self.surovina.nazev}"


class SkladDashboard(models.Model):
    """
    Pseudo-model jen pro admin dashboard (nebude se ukládat).
    """

    class Meta:
        managed = False
        verbose_name = "Skladový dashboard"
        verbose_name_plural = "Skladový dashboard"


class RecepturaPolozka(models.Model):
    jidlo = models.ForeignKey(
        Jidlo,
        on_delete=models.CASCADE,
        related_name="receptura",
    )
    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        related_name="v_receptech",
    )
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
        return (
            f"{self.jidlo} – "
            f"{self.mnozstvi_na_porci} {self.surovina.jednotka} "
            f"{self.surovina.nazev}/porce"
        )


class PrijemSkladu(models.Model):
    """
    Hlavička příjmu zboží (jedna dodávka).
    """

    datum = models.DateTimeField(
        default=timezone.now,
        verbose_name="Datum příjmu",
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
    uzavreny = models.BooleanField(
        default=False,
        verbose_name="Uzavřený (převod do skladu)",
    )

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
        verbose_name="Příjem",
    )
    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        verbose_name="Surovina",
    )
    mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství",
        help_text="Ve stejné jednotce jako surovina.",
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
        fyz = self.fyzicky_stav or Decimal("0")
        pred = self.stav_pred or Decimal("0")
        self.rozdil = fyz - pred
        super().save(*args, **kwargs)


class InventurniDoklad(Inventura):
    class Meta:
        proxy = True
        verbose_name = "Inventurní doklad"
        verbose_name_plural = "Inventurní doklady"


class Vydejka(models.Model):
    """
    Výdej surovin do výroby – zdroj pro sklad i spotřební koš.
    Typicky: 1 den + 1 stravovací skupina + 1 typ stravy.
    """

    TYP_STRAVY = [
        ("OBED", "Oběd"),
        ("CELD", "Celodenní strava"),
    ]

    stravovaci_skupina = models.ForeignKey(
        StravovaciSkupina,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Stravovací skupina",
        help_text="Např. SŠ žáci, ZŠ 1. stupeň…",
    )

    typ_stravy = models.CharField(
        max_length=10,
        choices=TYP_STRAVY,
        default="OBED",
        verbose_name="Typ stravy",
    )

    datum = models.DateField(
        verbose_name="Datum výdeje",
    )

    jidla = models.ManyToManyField(
        Jidlo,
        blank=True,
        verbose_name="Jídla v této výdejce",
        help_text="Pro přehled, k jakým jídlům se výdejka vztahuje.",
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
        verbose_name="Uzavřená (promítnuto do skladu a do výpočtu SK)",
    )

    class Meta:
        verbose_name = "Výdejka do výroby"
        verbose_name_plural = "Výdejky do výroby"
        ordering = ["-datum"]

    def __str__(self):
        sk = self.stravovaci_skupina.kod if self.stravovaci_skupina else "bez skupiny"
        return f"Výdejka #{self.id} – {self.datum.strftime('%d.%m.%Y')} ({sk})"


class PolozkaVydejky(models.Model):
    vydejka = models.ForeignKey(
        Vydejka,
        on_delete=models.CASCADE,
        related_name="polozky",
        verbose_name="Výdejka",
    )
    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        verbose_name="Surovina",
    )
    mnozstvi = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        verbose_name="Množství",
        help_text="Ve stejné jednotce jako surovina.",
    )

    class Meta:
        verbose_name = "Položka výdejky"
        verbose_name_plural = "Položky výdejky"
        unique_together = ("vydejka", "surovina")

    def __str__(self):
        return (
            f"{self.vydejka} – {self.mnozstvi} "
            f"{self.surovina.jednotka} {self.surovina.nazev}"
        )
class ReportSpotrebniKos(models.Model):
    """
    Pseudo-model pro měsíční report spotřebního koše v adminu.
    """
    class Meta:
        managed = False
        verbose_name = "Report spotřebního koše"
        verbose_name_plural = "Report spotřebního koše"

class NormaSpotrebnihoKose(models.Model):
    """
    Měsíční norma spotřebního koše na 1 strávníka
    pro danou skupinu SK a stravovací skupinu.
    """
    stravovaci_skupina = models.ForeignKey(
        StravovaciSkupina,
        on_delete=models.CASCADE,
        verbose_name="Stravovací skupina",
    )
    skupina_sk = models.CharField(
        max_length=10,
        choices=Surovina.SKUPINA_SK,
        verbose_name="Skupina spotřebního koše",
    )
    norma_kg_mesic = models.DecimalField(
        max_digits=6,
        decimal_places=3,
        verbose_name="Norma (kg na 1 strávníka za měsíc)",
    )

    class Meta:
        verbose_name = "Norma spotřebního koše"
        verbose_name_plural = "Normy spotřebního koše"
        unique_together = ("stravovaci_skupina", "skupina_sk")

    def __str__(self):
        return f"{self.stravovaci_skupina} – {self.get_skupina_sk_display()}: {self.norma_kg_mesic} kg"
