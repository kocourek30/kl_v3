from decimal import Decimal


from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


from sklad.models import Surovina
from jidelnicek.models import Jidlo


class DPHSkupina(models.Model):
    nazev = models.CharField(max_length=50)
    sazba = models.DecimalField(max_digits=4, decimal_places=2)

    class Meta:
        verbose_name = "DPH skupina"
        verbose_name_plural = "DPH skupiny"

    def __str__(self):
        return f"{self.nazev} ({self.sazba} %)"


class PLUKategorie(models.Model):
    nazev = models.CharField(max_length=50)

    class Meta:
        verbose_name = "Kategorie PLU"
        verbose_name_plural = "Kategorie PLU"

    def __str__(self):
        return self.nazev


class PLUPolozka(models.Model):
    TYP_RECEPTURA = "RE"
    TYP_VAZENE = "VA"
    TYP_KORUNOVE = "KO"

    TYPY = [
        (TYP_RECEPTURA, "Receptura (kusová)"),
        (TYP_VAZENE, "Vážené zboží"),
        (TYP_KORUNOVE, "Korunová položka"),
    ]

    nazev = models.CharField(max_length=100)
    cena = models.DecimalField(max_digits=8, decimal_places=2)
    dph_skupina = models.ForeignKey(DPHSkupina, on_delete=models.PROTECT)
    kategorie = models.ForeignKey(
        PLUKategorie, on_delete=models.SET_NULL, null=True, blank=True
    )
    typ = models.CharField(
        max_length=2,
        choices=TYPY,
        default=TYP_RECEPTURA,
    )
    aktivni = models.BooleanField(default=True)

    surovina = models.ForeignKey(
        Surovina, on_delete=models.SET_NULL, null=True, blank=True
    )
    jidlo = models.ForeignKey(
        Jidlo, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        verbose_name = "PLU položka"
        verbose_name_plural = "PLU položky"

    def __str__(self):
        return self.nazev

    def clean(self):
        super().clean()
        if self.cena < 0:
            raise ValidationError({"cena": "Cena PLU nesmí být záporná."})
        if self.typ == self.TYP_VAZENE and not self.surovina_id:
            raise ValidationError({"surovina": "U váženého zboží vyber skladovou surovinu."})
        if self.surovina_id and self.jidlo_id:
            raise ValidationError("PLU nemá být současně navázané na surovinu i jídlo.")


class Pokladna(models.Model):
    nazev = models.CharField(max_length=50)
    popis = models.CharField(max_length=200, blank=True)
    aktivni = models.BooleanField(default=True)
    hotovostni_zustatek = models.DecimalField(
        "Pokladní hotovost",
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Výchozí hotovost v pokladně. Používá se pro provozní přehledy a finanční reporty.",
    )
    qr_iban = models.CharField(
        "IBAN pro QR platby",
        max_length=34,
        blank=True,
        help_text="Např. CZ6508000000192000145399. Bez IBAN nelze vygenerovat QR platbu.",
    )
    qr_bic = models.CharField(
        "BIC/SWIFT pro QR platby",
        max_length=11,
        blank=True,
        help_text="Volitelné. Některé banky jej u QR platby nevyžadují.",
    )
    qr_prijemce = models.CharField(
        "Příjemce QR platby",
        max_length=80,
        blank=True,
        help_text="Volitelné jméno příjemce platby.",
    )
    qr_zprava = models.CharField(
        "Výchozí zpráva QR platby",
        max_length=60,
        blank=True,
        default="Pokladna",
    )

    class Meta:
        verbose_name = "Pokladna"
        verbose_name_plural = "Pokladny"

    def __str__(self):
        return self.nazev


class PokladniDoklad(models.Model):
    STAV_ROZPRACOVANO = "ROZPRACOVANO"
    STAV_CEKA_NA_QR = "CEKA_NA_QR"
    STAV_UZAVRENO = "UZAVRENO"
    STAV_STORNOVANO = "STORNOVANO"

    STAVY = [
        (STAV_ROZPRACOVANO, "Rozpracováno"),
        (STAV_CEKA_NA_QR, "Čeká na QR platbu"),
        (STAV_UZAVRENO, "Uzavřeno"),
        (STAV_STORNOVANO, "Stornováno"),
    ]

    PLATBA_HOTOVOST = "HOTOVOST"
    PLATBA_KARTA = "KARTA"
    PLATBA_KONTO = "KONTO"
    PLATBA_QR = "QR"

    ZPUSOBY_PLATBY = [
        (PLATBA_HOTOVOST, "Hotovost"),
        (PLATBA_KARTA, "Karta"),
        (PLATBA_KONTO, "Konto strávníka"),
        (PLATBA_QR, "QR platba"),
    ]

    pokladna = models.ForeignKey(Pokladna, on_delete=models.PROTECT)
    datum = models.DateTimeField(auto_now_add=True)
    cislo_dokladu = models.CharField(
        "Číslo dokladu", max_length=40, unique=True, null=True, blank=True
    )
    stav = models.CharField("Stav", max_length=20, choices=STAVY, default=STAV_ROZPRACOVANO, db_index=True)
    zpusob_platby = models.CharField(
        "Způsob platby", max_length=20, choices=ZPUSOBY_PLATBY, blank=True, default=""
    )
    obsluha = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pokladni_doklady_obsluha",
    )
    zakaznik = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pokladni_doklady_zakaznik",
    )
    uzavren_at = models.DateTimeField("Uzavřeno", null=True, blank=True)
    uzavrel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pokladni_doklady_uzavrel",
    )
    stornovano_at = models.DateTimeField("Stornováno", null=True, blank=True)
    stornoval = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pokladni_doklady_stornoval",
    )
    storno_duvod = models.TextField("Důvod storna", blank=True, default="")
    konto_pohyb = models.ForeignKey(
        "users.Vklad",
        verbose_name="Pohyb konta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pokladni_doklady",
    )
    uzaverka = models.ForeignKey(
        "PokladniUzaverka",
        verbose_name="Denní uzávěrka",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doklady",
    )
    qr_payload = models.TextField("QR platební data", blank=True, default="")
    qr_vytvoren_at = models.DateTimeField("QR vytvořeno", null=True, blank=True)
    qr_potvrzen_at = models.DateTimeField("QR potvrzeno", null=True, blank=True)
    qr_potvrdil = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pokladni_qr_potvrdil",
        verbose_name="QR potvrdil",
    )

    celkem_bez_dph = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    celkem_dph = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    celkem_s_dph = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Pokladní doklad"
        verbose_name_plural = "Pokladní doklady"
        ordering = ("-datum", "-id")

    def __str__(self):
        cislo = self.cislo_dokladu or f"#{self.id}"
        return f"Účtenka {cislo} ({self.datum:%d.%m.%Y %H:%M})"

    @property
    def je_rozpracovany(self):
        return self.stav == self.STAV_ROZPRACOVANO

    @property
    def je_uzavreny(self):
        return self.stav == self.STAV_UZAVRENO

    @property
    def ceka_na_qr(self):
        return self.stav == self.STAV_CEKA_NA_QR

    @property
    def je_stornovany(self):
        return self.stav == self.STAV_STORNOVANO

    def prepocitej_sumy(self):
        if not self.je_rozpracovany:
            raise ValidationError("Přepočítat lze pouze rozpracovaný doklad.")
        zaklad = Decimal("0")
        dph = Decimal("0")
        for pol in self.polozky.all():
            zaklad += pol.zaklad_dph
            dph += pol.castka_dph
        self.celkem_bez_dph = zaklad
        self.celkem_dph = dph
        self.celkem_s_dph = zaklad + dph
        self.save(update_fields=["celkem_bez_dph", "celkem_dph", "celkem_s_dph"])


class PokladniPolozka(models.Model):
    doklad = models.ForeignKey(
        PokladniDoklad, related_name="polozky", on_delete=models.CASCADE
    )
    plu = models.ForeignKey(PLUPolozka, on_delete=models.PROTECT)
    nazev_snapshot = models.CharField("Název při prodeji", max_length=150, blank=True, default="")
    mnozstvi = models.DecimalField(max_digits=8, decimal_places=3, default=1)
    jednotka_text = models.CharField("Jednotka", max_length=20, blank=True, default="")

    cena_jednotkova = models.DecimalField(max_digits=8, decimal_places=2)
    dph_sazba = models.DecimalField(max_digits=4, decimal_places=2)
    zaklad_dph = models.DecimalField(max_digits=10, decimal_places=2)
    castka_dph = models.DecimalField(max_digits=10, decimal_places=2)
    castka_celkem = models.DecimalField(max_digits=10, decimal_places=2)
    skladovy_pohyb = models.ForeignKey(
        "sklad.PohybSkladu",
        verbose_name="Skladový pohyb",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pokladni_polozky",
    )

    class Meta:
        verbose_name = "Pokladní položka"
        verbose_name_plural = "Pokladní položky"

    def __str__(self):
        return f"{self.nazev_snapshot or self.plu} x {self.mnozstvi}"

    def clean(self):
        super().clean()
        if self.mnozstvi <= 0:
            raise ValidationError({"mnozstvi": "Množství musí být větší než nula."})


class PokladniSmazanaPolozka(models.Model):
    doklad = models.ForeignKey(
        PokladniDoklad,
        related_name="smazane_polozky",
        on_delete=models.CASCADE,
    )
    plu = models.ForeignKey(PLUPolozka, on_delete=models.PROTECT, null=True, blank=True)
    nazev_snapshot = models.CharField("Název při smazání", max_length=150, blank=True, default="")
    mnozstvi = models.DecimalField(max_digits=8, decimal_places=3, default=1)
    jednotka_text = models.CharField("Jednotka", max_length=20, blank=True, default="")
    cena_jednotkova = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    dph_sazba = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    zaklad_dph = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    castka_dph = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    castka_celkem = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    smazano_at = models.DateTimeField("Smazáno", default=timezone.now)
    smazal = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pokladni_smazane_polozky",
    )
    duvod = models.CharField("Důvod", max_length=120, blank=True, default="")

    class Meta:
        verbose_name = "Smazaná pokladní položka"
        verbose_name_plural = "Smazané pokladní položky"
        ordering = ("-smazano_at", "-id")

    def __str__(self):
        return f"{self.nazev_snapshot or self.plu} x {self.mnozstvi}"


class PokladniUzaverka(models.Model):
    pokladna = models.ForeignKey(Pokladna, on_delete=models.PROTECT, related_name="uzaverky")
    datum = models.DateField("Datum", default=timezone.localdate, db_index=True)
    vytvoreno_at = models.DateTimeField("Vytvořeno", auto_now_add=True)
    uzavrel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pokladni_uzaverky",
    )
    pocet_dokladu = models.PositiveIntegerField("Počet dokladů", default=0)
    pocet_storen = models.PositiveIntegerField("Počet storen", default=0)
    hotovost = models.DecimalField("Hotovost", max_digits=12, decimal_places=2, default=0)
    karta = models.DecimalField("Karta", max_digits=12, decimal_places=2, default=0)
    konto = models.DecimalField("Konto strávníků", max_digits=12, decimal_places=2, default=0)
    qr = models.DecimalField("QR platby", max_digits=12, decimal_places=2, default=0)
    storna = models.DecimalField("Storna", max_digits=12, decimal_places=2, default=0)
    hotovost_spoctena = models.DecimalField(
        "Spočtená hotovost", max_digits=12, decimal_places=2, null=True, blank=True
    )
    rozdil_hotovosti = models.DecimalField("Rozdíl hotovosti", max_digits=12, decimal_places=2, default=0)
    poznamka = models.TextField("Poznámka", blank=True, default="")

    class Meta:
        verbose_name = "Denní uzávěrka pokladny"
        verbose_name_plural = "Denní uzávěrky pokladny"
        unique_together = [("pokladna", "datum")]
        ordering = ("-datum", "-id")

    def __str__(self):
        return f"Uzávěrka {self.pokladna} / {self.datum:%d.%m.%Y}"

    @property
    def celkem_trzba(self):
        return (
            (self.hotovost or Decimal("0"))
            + (self.karta or Decimal("0"))
            + (self.konto or Decimal("0"))
            + (self.qr or Decimal("0"))
        )


# models-3.py – jen část s PokladnaTile

class PokladnaTile(models.Model):
    BARVY_POZADI = [
        ("#54ae43", "Zelená (brand primary)"),
        ("#f28f28", "Oranžová (brand accent)"),
        ("#0d6efd", "Modrá (primary)"),
        ("#198754", "Zelená (success)"),
        ("#dc3545", "Červená (danger)"),
        ("#ffc107", "Žlutá (warning)"),
        ("#6c757d", "Šedá (secondary)"),
        ("#ffffff", "Bílá"),
        ("#000000", "Černá"),
    ]

    BARVY_TEXTU = [
        ("#ffffff", "Bílá"),
        ("#000000", "Černá"),
        ("#212529", "Tmavě šedá"),
    ]

    FONTY = [
        ("", "Výchozí (system)"),
        ("'Segoe UI', Tahoma, Geneva, Verdana, sans-serif", "Segoe UI"),
        ("Arial, Helvetica, sans-serif", "Arial"),
        ("'Times New Roman', Times, serif", "Times New Roman"),
        ("'Courier New', Courier, monospace", "Courier New"),
    ]

    ICON_CHOICES = [
        ("", "Bez ikony"),
        ("fa-solid fa-mug-hot", "Hrnek / nápoj"),
        ("fa-solid fa-utensils", "Příbor / jídlo"),
        ("fa-solid fa-burger", "Burger / svačina"),
        ("fa-solid fa-ice-cream", "Zmrzlina / dezert"),
        ("fa-solid fa-bottle-water", "Láhev / nápoj"),
        ("fa-solid fa-bread-slice", "Pečivo"),
        ("fa-solid fa-egg", "Vejce"),
        ("fa-solid fa-cheese", "Sýr"),
        ("fa-solid fa-bowl-food", "Miska"),
    ]

    pokladna = models.ForeignKey(
        Pokladna,
        on_delete=models.CASCADE,
        related_name="tiles",
        verbose_name="Pokladna",
    )
    plu = models.ForeignKey(
        PLUPolozka,
        on_delete=models.PROTECT,
        verbose_name="PLU položka",
    )
    nazev = models.CharField(
        max_length=100,
        blank=True,
        help_text="Pokud prázdné, použije se název z PLU.",
    )

    barva_pozadi = models.CharField(
        max_length=20,
        choices=BARVY_POZADI,
        default="#54ae43",
        help_text="Základní barva pozadí dlaždice.",
    )
    barva_pozadi_custom = models.CharField(
        max_length=20,
        blank=True,
        help_text="Volitelná vlastní barva (hex). Pokud vyplněno, má přednost.",
    )

    barva_textu = models.CharField(
        max_length=20,
        choices=BARVY_TEXTU,
        default="#ffffff",
        help_text="Barva textu dlaždice.",
    )

    font_bold = models.BooleanField(default=False)
    font_size_px = models.PositiveIntegerField(
        default=16,
        help_text="Velikost názvu v px."
    )
    font_family = models.CharField(
        max_length=100,
        blank=True,
        choices=FONTY,
        help_text="Volitelný font názvu.",
    )

    ikona = models.CharField(
        max_length=80,
        blank=True,
        choices=ICON_CHOICES,
        help_text="Font Awesome ikona (volitelné).",
    )

    poradi = models.PositiveIntegerField(
        default=0,
        help_text="Řazení dlaždic na pokladně (vzestupně).",
    )
    aktivni = models.BooleanField(
        default=True,
        help_text="Určuje, zda se dlaždice zobrazuje na pokladně.",
    )

    class Meta:
        verbose_name = "Dlaždice pokladny"
        verbose_name_plural = "Dlaždice pokladny"
        ordering = ("poradi", "id")

    def __str__(self):
        return self.nazev or f"{self.plu.nazev} – {self.pokladna}"

    @property
    def effective_bg_color(self):
        """Vrátí vlastní barvu, pokud je vyplněná, jinak vybranou z choices."""
        return self.barva_pozadi_custom or self.barva_pozadi

