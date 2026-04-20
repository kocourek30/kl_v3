from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone


class FakturacniNastaveni(models.Model):
    nazev = models.CharField(max_length=80, default="Výchozí nastavení")
    zamestnanecke_skupiny = models.ManyToManyField(
        Group,
        blank=True,
        related_name="fakturacni_nastaveni_zamestnanci",
        verbose_name="Skupiny zaměstnanců pro srážku ze mzdy",
    )
    zahrnout_nevyzvednute = models.BooleanField(
        default=True,
        verbose_name="Zahrnout nevyzvednuté objednávky",
        help_text="Pokud je zapnuto, do srážek ze mzdy se počítají i nevyzvednuté objednávky.",
    )
    fakturovat_dotace = models.BooleanField(default=True, verbose_name="Fakturovat dotace")

    class Meta:
        verbose_name = "Nastavení fakturace"
        verbose_name_plural = "Nastavení fakturace"

    def __str__(self):
        return self.nazev

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"nazev": "Výchozí nastavení"})
        return obj


class FakturacniDavka(models.Model):
    STAV_NAVRH = "NAVRH"
    STAV_UZAVRENO = "UZAVRENO"

    STAVY = [
        (STAV_NAVRH, "Návrh"),
        (STAV_UZAVRENO, "Uzavřeno"),
    ]

    rok = models.PositiveIntegerField(verbose_name="Rok")
    mesic = models.PositiveSmallIntegerField(verbose_name="Měsíc")
    datum_od = models.DateField(verbose_name="Od")
    datum_do = models.DateField(verbose_name="Do")
    stav = models.CharField(max_length=20, choices=STAVY, default=STAV_NAVRH, verbose_name="Stav")
    vytvoreno = models.DateTimeField(default=timezone.now, verbose_name="Vytvořeno")
    vytvoril = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fakturacni_davky",
        verbose_name="Vytvořil",
    )
    dotace_celkem = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Dotace celkem")
    srazky_celkem = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Srážky celkem")
    polozek = models.PositiveIntegerField(default=0, verbose_name="Počet položek")
    poznamka = models.TextField(blank=True, default="", verbose_name="Poznámka")

    class Meta:
        verbose_name = "Fakturační dávka"
        verbose_name_plural = "Fakturační dávky"
        unique_together = ("rok", "mesic")
        ordering = ("-rok", "-mesic", "-id")

    def __str__(self):
        return f"Fakturace {self.mesic:02d}/{self.rok}"

    @property
    def celkem(self):
        return (self.dotace_celkem or Decimal("0")) + (self.srazky_celkem or Decimal("0"))


class FakturacniPolozka(models.Model):
    TYP_DOTACE = "DOTACE"
    TYP_SRAZKA = "SRAZKA"

    TYPY = [
        (TYP_DOTACE, "Dotace"),
        (TYP_SRAZKA, "Srážka ze mzdy"),
    ]

    davka = models.ForeignKey(FakturacniDavka, on_delete=models.CASCADE, related_name="polozky")
    typ = models.CharField(max_length=20, choices=TYPY, db_index=True, verbose_name="Typ")
    uzivatel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fakturacni_polozky",
        verbose_name="Uživatel",
    )
    username_snapshot = models.CharField(max_length=150, blank=True, default="", verbose_name="Přihlašovací jméno")
    jmeno_snapshot = models.CharField(max_length=255, blank=True, default="", verbose_name="Jméno")
    osobni_cislo_snapshot = models.CharField(max_length=100, blank=True, default="", verbose_name="Osobní číslo")
    skupina_snapshot = models.CharField(max_length=150, blank=True, default="", verbose_name="Skupina")
    pocet_porci = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Počet porcí")
    castka = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Částka")
    detail = models.TextField(blank=True, default="", verbose_name="Detail")

    class Meta:
        verbose_name = "Fakturační položka"
        verbose_name_plural = "Fakturační položky"
        ordering = ("typ", "skupina_snapshot", "jmeno_snapshot", "username_snapshot")

    def __str__(self):
        return f"{self.get_typ_display()} - {self.jmeno_snapshot or self.username_snapshot}: {self.castka} Kč"
