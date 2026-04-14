from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class DokladBase(models.Model):
    """
    Společný základ pro skladové doklady.
    """
    datum = models.DateField(db_index=True)
    popis = models.TextField(blank=True, default="")

    vytvoril = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_vytvoril_set",
    )

    uzavreny = models.BooleanField(default=False, db_index=True)
    uzavren_at = models.DateTimeField(null=True, blank=True)
    uzavrel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_uzavrel_set",
    )

    stornovano = models.BooleanField(default=False, db_index=True)
    stornovano_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ("-datum", "-id")

    @property
    def je_editovatelny(self) -> bool:
        return not self.uzavreny and not self.stornovano

    def uzavri_meta(self, user=None):
        self.uzavreny = True
        self.uzavren_at = timezone.now()
        self.uzavrel = user

    def storno_meta(self):
        self.stornovano = True
        self.stornovano_at = timezone.now()


class SkladDashboard(models.Model):
    """
    Pseudo-model pro admin dashboard.
    """
    class Meta:
        managed = False
        verbose_name = "Skladový dashboard"
        verbose_name_plural = "Skladový dashboard"


class ReportNakladySkladu(models.Model):
    """
    Pseudo-model pro admin report.
    """
    class Meta:
        managed = False
        verbose_name = "Report nákladů skladu"
        verbose_name_plural = "Report nákladů skladu"


class Surovina(models.Model):
    JEDNOTKA_G = "g"
    JEDNOTKA_KG = "kg"
    JEDNOTKA_L = "l"
    JEDNOTKA_ML = "ml"
    JEDNOTKA_KS = "ks"

    JEDNOTKY = [
        (JEDNOTKA_G, "g"),
        (JEDNOTKA_KG, "kg"),
        (JEDNOTKA_L, "l"),
        (JEDNOTKA_ML, "ml"),
        (JEDNOTKA_KS, "ks"),
    ]

    nazev = models.CharField(max_length=255, unique=True)
    jednotka = models.CharField(max_length=10, choices=JEDNOTKY)

    # Spotřební koš
    skupina_sk = models.CharField(max_length=50, blank=True, default="")
    koeficient_sk = models.DecimalField(
        max_digits=8, decimal_places=4, default=Decimal("1.0000")
    )
    je_masny_vyrobek = models.BooleanField(default=False)
    je_bio = models.BooleanField(default=False)
    podil_celozrnne_slozky = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    volny_cukr_na_100g = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )

    # Hmotnost/cena
    hmotnost_ks_g = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    prumerna_cena_za_jednotku = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )

    def __str__(self):
        return self.nazev

    def clean(self):
        super().clean()

        if self.jednotka == self.JEDNOTKA_KS and not self.hmotnost_ks_g:
            raise ValidationError({
                "hmotnost_ks_g": "U suroviny vedené v ks musí být vyplněna hmotnost 1 ks v gramech."
            })

        if self.skupina_sk and self.koeficient_sk is None:
            raise ValidationError({
                "koeficient_sk": "Při vyplněné skupině spotřebního koše musí být vyplněn koeficient."
            })


class StavSkladu(models.Model):
    surovina = models.OneToOneField(
        Surovina,
        on_delete=models.CASCADE,
        related_name="stav",
    )
    mnozstvi = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    min_mnozstvi = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "Stav skladu"
        verbose_name_plural = "Stavy skladu"
        ordering = ("surovina__nazev",)

    def __str__(self):
        return f"{self.surovina} – {self.mnozstvi} {self.surovina.jednotka}"


class PrijemSkladu(DokladBase):
    class Meta:
        verbose_name = "Příjemka"
        verbose_name_plural = "Příjemky"


class Inventura(DokladBase):
    class Meta:
        verbose_name = "Inventura"
        verbose_name_plural = "Inventury"


class InventurniDoklad(Inventura):
    """
    Pokud to chceš zachovat kvůli adminu/historii.
    Doporučení: spíš sjednotit s Inventura a nepoužívat duplicitu modelu.
    """
    class Meta:
        proxy = True
        verbose_name = "Inventurní doklad"
        verbose_name_plural = "Inventurní doklady"


class Vydejka(DokladBase):
    TYP_STRAVY_OBED = "OBED"
    TYP_STRAVY_SVACINA = "SVACINA"
    TYP_STRAVY_VECERE = "VECERE"

    TYPY_STRAVY = [
        (TYP_STRAVY_OBED, "Oběd"),
        (TYP_STRAVY_SVACINA, "Svačina"),
        (TYP_STRAVY_VECERE, "Večeře"),
    ]

    stravovaci_skupina = models.ForeignKey(
        "users.StravovaciSkupina",
        on_delete=models.PROTECT,
        related_name="vydejky",
    )
    typ_stravy = models.CharField(max_length=20, choices=TYPY_STRAVY, db_index=True)

    # Nepovinné: pokud to chceš ukazovat jako rekapitulaci navázaných jídel
    jidla = models.ManyToManyField(
        "jidelnicek.Jidlo",
        blank=True,
        related_name="vydejky",
    )

    class Meta:
        verbose_name = "Výdejka"
        verbose_name_plural = "Výdejky"
        constraints = [
            models.UniqueConstraint(
                fields=["datum", "stravovaci_skupina", "typ_stravy"],
                name="uniq_vydejka_per_den_skupina_typ",
            )
        ]

    def __str__(self):
        return f"Výdejka {self.datum} / {self.stravovaci_skupina} / {self.typ_stravy}"


class PolozkaPrijmu(models.Model):
    prijem = models.ForeignKey(
        PrijemSkladu,
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT)
    mnozstvi = models.DecimalField(max_digits=12, decimal_places=3)
    jednotkova_cena = models.DecimalField(max_digits=12, decimal_places=4)

    class Meta:
        verbose_name = "Položka příjmu"
        verbose_name_plural = "Položky příjmu"

    def __str__(self):
        return f"{self.surovina} / {self.mnozstvi}"


class PolozkaVydejky(models.Model):
    vydejka = models.ForeignKey(
        Vydejka,
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT)
    mnozstvi = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Položka výdejky"
        verbose_name_plural = "Položky výdejky"

    def __str__(self):
        return f"{self.surovina} / {self.mnozstvi}"


class PolozkaInventury(models.Model):
    inventura = models.ForeignKey(
        Inventura,
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT)
    stav_pred = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    fyzicky_stav = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))
    rozdil = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "Položka inventury"
        verbose_name_plural = "Položky inventury"

    def save(self, *args, **kwargs):
        self.rozdil = (self.fyzicky_stav or Decimal("0")) - (self.stav_pred or Decimal("0"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.surovina} / rozdíl {self.rozdil}"


class PohybSkladu(models.Model):
    TYP_PRIJEM = "PRIJEM"
    TYP_VYDEJ = "VYDEJ"
    TYP_INVENTURA_PLUS = "INVENTURA_PLUS"
    TYP_INVENTURA_MINUS = "INVENTURA_MINUS"

    TYPY = [
        (TYP_PRIJEM, "Příjem"),
        (TYP_VYDEJ, "Výdej"),
        (TYP_INVENTURA_PLUS, "Inventura +"),
        (TYP_INVENTURA_MINUS, "Inventura -"),
    ]

    datum = models.DateTimeField(default=timezone.now, db_index=True)
    surovina = models.ForeignKey(
        Surovina,
        on_delete=models.PROTECT,
        related_name="pohyby",
    )
    typ = models.CharField(max_length=20, choices=TYPY, db_index=True)
    mnozstvi = models.DecimalField(max_digits=12, decimal_places=3)
    cena_za_jednotku = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    prijem = models.ForeignKey(
        PrijemSkladu,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )
    vydejka = models.ForeignKey(
        Vydejka,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )
    inventura = models.ForeignKey(
        Inventura,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )

    poznamka = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Pohyb skladu"
        verbose_name_plural = "Pohyby skladu"
        ordering = ("-datum", "-id")

    def __str__(self):
        return f"{self.datum} / {self.surovina} / {self.typ} / {self.mnozstvi}"


class RecepturaPolozka(models.Model):
    """
    Ponecháno jako předpoklad podle tvého současného projektu.
    """
    jidlo = models.ForeignKey(
        "jidelnicek.Jidlo",
        on_delete=models.CASCADE,
        related_name="receptura",
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT)
    mnozstvi_na_porci = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Položka receptury"
        verbose_name_plural = "Položky receptury"
class KomponentaJidla(models.Model):
    TYP_POLEVKA = "POLEVKA"
    TYP_OMACKA = "OMACKA"
    TYP_MASO = "MASO"
    TYP_PRILOHA = "PRILOHA"
    TYP_SALAT = "SALAT"
    TYP_DEZERT = "DEZERT"
    TYP_NAPOJ = "NAPOJ"
    TYP_OSTATNI = "OSTATNI"

    TYPY = [
        (TYP_POLEVKA, "Polévka"),
        (TYP_OMACKA, "Omáčka / základ"),
        (TYP_MASO, "Maso / protein"),
        (TYP_PRILOHA, "Příloha"),
        (TYP_SALAT, "Salát"),
        (TYP_DEZERT, "Dezert"),
        (TYP_NAPOJ, "Nápoj"),
        (TYP_OSTATNI, "Ostatní"),
    ]

    nazev = models.CharField(max_length=255, unique=True)
    typ = models.CharField(max_length=20, choices=TYPY, default=TYP_OSTATNI, db_index=True)
    aktivni = models.BooleanField(default=True)
    poznamka = models.TextField(blank=True, default="")
    porce_text = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Např. 150 ml, 2 ks, 180 g",
    )

    class Meta:
        verbose_name = "Komponenta jídla"
        verbose_name_plural = "Komponenty jídel"
        ordering = ("typ", "nazev")

    def __str__(self):
        return self.nazev


class KomponentaSurovina(models.Model):
    komponenta = models.ForeignKey(
        KomponentaJidla,
        on_delete=models.CASCADE,
        related_name="suroviny",
    )
    surovina = models.ForeignKey(Surovina, on_delete=models.PROTECT)
    mnozstvi_na_porci = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Surovina komponenty"
        verbose_name_plural = "Suroviny komponenty"
        unique_together = [("komponenta", "surovina")]
        ordering = ("komponenta__nazev", "surovina__nazev")

    def __str__(self):
        return f"{self.komponenta} / {self.surovina} / {self.mnozstvi_na_porci}"


class JidloKomponenta(models.Model):
    jidlo = models.ForeignKey(
        "jidelnicek.Jidlo",
        on_delete=models.CASCADE,
        related_name="komponenty_jidla",
    )
    komponenta = models.ForeignKey(
        KomponentaJidla,
        on_delete=models.PROTECT,
        related_name="jidla",
    )
    mnozstvi_nasobek = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        default=Decimal("1.000"),
        help_text="1.0 = standardní porce komponenty, 0.5 = půl porce, 2.0 = dvojnásobek",
    )
    poradi = models.PositiveIntegerField(default=0)
    povinna = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Komponenta v jídle"
        verbose_name_plural = "Komponenty v jídlech"
        unique_together = [("jidlo", "komponenta")]
        ordering = ("poradi", "id")

    def __str__(self):
        return f"{self.jidlo} -> {self.komponenta}"

class NormaSpotrebnihoKose(models.Model):
    stravovaci_skupina = models.ForeignKey(
        "users.StravovaciSkupina",
        on_delete=models.CASCADE,
        related_name="normy_sk",
    )
    skupina_sk = models.CharField(max_length=50)
    norma_g_mesic = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        unique_together = [("stravovaci_skupina", "skupina_sk")]
        verbose_name = "Norma spotřebního koše"
        verbose_name_plural = "Normy spotřebního koše"

    def __str__(self):
        return f"{self.stravovaci_skupina} / {self.skupina_sk}"


class ToleranceSpotrebnihoKose(models.Model):
    stravovaci_skupina = models.ForeignKey(
        "users.StravovaciSkupina",
        on_delete=models.CASCADE,
        related_name="tolerance_sk",
    )
    skupina_sk = models.CharField(max_length=50)
    min_pct = models.DecimalField(max_digits=8, decimal_places=2)
    max_pct = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        unique_together = [("stravovaci_skupina", "skupina_sk")]
        verbose_name = "Tolerance spotřebního koše"
        verbose_name_plural = "Tolerance spotřebního koše"

    def __str__(self):
        return f"{self.stravovaci_skupina} / {self.skupina_sk}"