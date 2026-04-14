from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class DokladBase(models.Model):
    """
    Společný základ pro skladové doklady.
    """
    datum = models.DateField("Datum", db_index=True)
    popis = models.TextField("Popis", blank=True, default="")

    vytvoril = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Vytvořil",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_vytvoril_set",
    )

    uzavreny = models.BooleanField("Uzavřený", default=False, db_index=True)
    uzavren_at = models.DateTimeField("Uzavřeno", null=True, blank=True)
    uzavrel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Uzavřel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_uzavrel_set",
    )

    stornovano = models.BooleanField("Stornováno", default=False, db_index=True)
    stornovano_at = models.DateTimeField("Stornováno dne", null=True, blank=True)

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

    SK_MASO = "MASO"
    SK_RYBY = "RYBY"
    SK_MLEKO = "MLEKO"
    SK_TUKY = "TUKY"
    SK_CUKRY = "CUKRY"
    SK_ZELENINA_OVOCE = "ZELENINA_OVOCE"
    SK_BRAMBORY = "BRAMBORY"
    SK_CELOZRNNE = "CELOZRNNE"
    SK_LUSTENINY = "LUSTENINY"
    SK_NEZAPOCITAVA_SE = "NEZAPOCITAVA_SE"

    SKUPINY_SPOTREBNIHO_KOSE_2025 = [
        (SK_MASO, "Maso"),
        (SK_RYBY, "Ryby, korýši, měkkýši"),
        (SK_MLEKO, "Mléčné výrobky, mléko"),
        (SK_TUKY, "Tuky volné"),
        (SK_CUKRY, "Cukry volné"),
        (SK_ZELENINA_OVOCE, "Zelenina, ovoce"),
        (SK_BRAMBORY, "Brambory a ostatní hlízy"),
        (SK_CELOZRNNE, "Celozrnné obiloviny, pseudoobiloviny"),
        (SK_LUSTENINY, "Luštěniny"),
        (SK_NEZAPOCITAVA_SE, "Nezapočítává se"),
    ]

    nazev = models.CharField("Název", max_length=255, unique=True)
    jednotka = models.CharField("Jednotka", max_length=10, choices=JEDNOTKY)

    # Spotřební koš
    skupina_sk = models.CharField(
        "Skupina spotřebního koše",
        max_length=50,
        choices=SKUPINY_SPOTREBNIHO_KOSE_2025,
        blank=True,
        default="",
    )
    koeficient_sk = models.DecimalField(
        "Koeficient spotřebního koše",
        max_digits=8, decimal_places=4, default=Decimal("1.0000")
    )
    koeficient_ciste_hmotnosti_sk = models.DecimalField(
        "Koeficient čisté hmotnosti",
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Násobek hrubé hmotnosti pro přepočet na čistou hmotnost podle vyhlášky.",
    )
    koeficient_zapoctu_sk = models.DecimalField(
        "Započítávací koeficient",
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Koeficient započtení čisté hmotnosti podle tabulky potravin ve vyhlášce.",
    )
    je_masny_vyrobek = models.BooleanField("Masný výrobek", default=False)
    je_bio = models.BooleanField("Bio", default=False)
    je_sezonni = models.BooleanField("Sezónní ovoce, zelenina nebo brambory", default=False)
    je_sterilovana_nebo_kompot = models.BooleanField(
        "Sterilovaná zelenina / kompot",
        default=False,
        help_text="Sleduje limit 15 % ve skupině Zelenina, ovoce.",
    )
    je_rostlinny_tuk = models.BooleanField("Rostlinný volný tuk", default=False)
    je_zivocisny_tuk = models.BooleanField("Živočišný volný tuk", default=False)
    je_zakazano_pro_skolni_stravovani = models.BooleanField(
        "Zakázáno pro školní stravování",
        default=False,
    )
    podil_celozrnne_slozky = models.DecimalField(
        "Podíl celozrnné složky [%]",
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    volny_cukr_na_100g = models.DecimalField(
        "Volný cukr na 100 g",
        max_digits=8, decimal_places=3, null=True, blank=True
    )

    # Hmotnost/cena
    hmotnost_ks_g = models.DecimalField(
        "Hmotnost 1 ks [g]",
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    prumerna_cena_za_jednotku = models.DecimalField(
        "Průměrná cena za jednotku",
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
        verbose_name="Surovina",
        on_delete=models.CASCADE,
        related_name="stav",
    )
    mnozstvi = models.DecimalField("Množství", max_digits=12, decimal_places=3, default=Decimal("0"))
    min_mnozstvi = models.DecimalField("Minimální množství", max_digits=12, decimal_places=3, default=Decimal("0"))

    class Meta:
        verbose_name = "Stav skladu"
        verbose_name_plural = "Stavy skladu"
        ordering = ("surovina__nazev",)

    def __str__(self):
        return f"{self.surovina} – {self.mnozstvi} {self.surovina.jednotka}"


class Dodavatel(models.Model):
    nazev = models.CharField("Název", max_length=255, unique=True)
    ico = models.CharField("IČO", max_length=20, blank=True, default="")
    dic = models.CharField("DIČ", max_length=20, blank=True, default="")
    adresa = models.TextField("Adresa", blank=True, default="")
    kontaktni_osoba = models.CharField("Kontaktní osoba", max_length=255, blank=True, default="")
    email = models.EmailField("E-mail", blank=True, default="")
    telefon = models.CharField("Telefon", max_length=50, blank=True, default="")
    aktivni = models.BooleanField("Aktivní", default=True)
    poznamka = models.TextField("Poznámka", blank=True, default="")

    class Meta:
        verbose_name = "Dodavatel"
        verbose_name_plural = "Dodavatelé"
        ordering = ("nazev",)

    def __str__(self):
        return self.nazev


class PrijemSkladu(DokladBase):
    dodavatel = models.ForeignKey(
        Dodavatel,
        verbose_name="Dodavatel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="prijemky",
    )
    cislo_faktury = models.CharField("Číslo faktury", max_length=100, blank=True, default="")
    cislo_dodaciho_listu = models.CharField("Číslo dodacího listu", max_length=100, blank=True, default="")
    datum_dodani = models.DateField("Datum dodání", null=True, blank=True)
    datum_vystaveni = models.DateField("Datum vystavení", null=True, blank=True)
    datum_splatnosti = models.DateField("Datum splatnosti", null=True, blank=True)
    castka_faktury_celkem = models.DecimalField(
        "Částka faktury celkem",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    priloha = models.FileField("Příloha", upload_to="sklad/prijemky/", null=True, blank=True)

    class Meta:
        verbose_name = "Příjemka"
        verbose_name_plural = "Příjemky"

    @property
    def soucet_polozek_bez_dph(self):
        return sum((p.cena_celkem_bez_dph or Decimal("0")) for p in self.polozky.all())

    @property
    def soucet_polozek_s_dph(self):
        return sum((p.cena_celkem_s_dph or Decimal("0")) for p in self.polozky.all())

    @property
    def rozdil_faktury(self):
        if self.castka_faktury_celkem is None:
            return None
        return self.castka_faktury_celkem - self.soucet_polozek_s_dph


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
        verbose_name="Stravovací skupina",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="vydejky",
    )
    typ_stravy = models.CharField(
        "Typ stravy",
        max_length=20,
        choices=TYPY_STRAVY,
        blank=True,
        default="",
        db_index=True,
    )

    # Nepovinné: pokud to chceš ukazovat jako rekapitulaci navázaných jídel
    jidla = models.ManyToManyField(
        "jidelnicek.Jidlo",
        verbose_name="Jídla",
        blank=True,
        related_name="vydejky",
    )

    class Meta:
        verbose_name = "Výdejka"
        verbose_name_plural = "Výdejky"
        constraints = [
            models.UniqueConstraint(
                fields=["datum", "stravovaci_skupina", "typ_stravy"],
                condition=models.Q(stornovano=False),
                name="uniq_vydejka_per_den_skupina_typ",
            )
        ]

    def __str__(self):
        return f"Výdejka {self.datum} / {self.stravovaci_skupina} / {self.typ_stravy}"


class PolozkaPrijmu(models.Model):
    prijem = models.ForeignKey(
        PrijemSkladu,
        verbose_name="Příjemka",
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, verbose_name="Surovina", on_delete=models.PROTECT)
    mnozstvi = models.DecimalField("Množství", max_digits=12, decimal_places=3, default=Decimal("0"))
    jednotkova_cena = models.DecimalField("Jednotková cena", max_digits=12, decimal_places=4, default=Decimal("0"))

    pocet_baleni = models.DecimalField("Počet balení", max_digits=12, decimal_places=3, default=Decimal("1.000"))
    mnozstvi_v_baleni = models.DecimalField(
        "Množství v balení",
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Množství v jednom dodaném balení.",
    )
    jednotka_baleni = models.CharField(
        "Jednotka balení",
        max_length=10,
        choices=Surovina.JEDNOTKY,
        blank=True,
        default="",
        help_text="Pokud zůstane prázdná, použije se skladová jednotka suroviny.",
    )
    cena_za_baleni_bez_dph = models.DecimalField(
        "Cena za balení bez DPH",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    sazba_dph = models.DecimalField("Sazba DPH [%]", max_digits=5, decimal_places=2, default=Decimal("0.00"))
    cena_za_baleni_s_dph = models.DecimalField(
        "Cena za balení s DPH",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    cena_celkem_bez_dph = models.DecimalField(
        "Cena celkem bez DPH",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    cena_celkem_s_dph = models.DecimalField(
        "Cena celkem s DPH",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    sarze = models.CharField("Šarže", max_length=100, blank=True, default="")
    datum_spotreby = models.DateField("Datum spotřeby", null=True, blank=True)

    class Meta:
        verbose_name = "Položka příjmu"
        verbose_name_plural = "Položky příjmu"

    def _preved_baleni_na_skladove_mnozstvi(self, mnozstvi, jednotka_baleni):
        skladova_jednotka = self.surovina.jednotka

        if jednotka_baleni == skladova_jednotka:
            return mnozstvi
        if jednotka_baleni == Surovina.JEDNOTKA_G and skladova_jednotka == Surovina.JEDNOTKA_KG:
            return mnozstvi / Decimal("1000")
        if jednotka_baleni == Surovina.JEDNOTKA_KG and skladova_jednotka == Surovina.JEDNOTKA_G:
            return mnozstvi * Decimal("1000")
        if jednotka_baleni == Surovina.JEDNOTKA_ML and skladova_jednotka == Surovina.JEDNOTKA_L:
            return mnozstvi / Decimal("1000")
        if jednotka_baleni == Surovina.JEDNOTKA_L and skladova_jednotka == Surovina.JEDNOTKA_ML:
            return mnozstvi * Decimal("1000")

        raise ValidationError({
            "jednotka_baleni": "Jednotku balení nelze převést na skladovou jednotku suroviny."
        })

    def prepocitej_z_baleni(self):
        if self.mnozstvi_v_baleni is None:
            return

        pocet_baleni = self.pocet_baleni or Decimal("0")
        jednotka_baleni = self.jednotka_baleni or self.surovina.jednotka
        mnozstvi_v_baleni = self.mnozstvi_v_baleni or Decimal("0")
        mnozstvi_v_skladove_jednotce = self._preved_baleni_na_skladove_mnozstvi(
            mnozstvi_v_baleni,
            jednotka_baleni,
        )

        self.mnozstvi = pocet_baleni * mnozstvi_v_skladove_jednotce

        if self.cena_za_baleni_bez_dph is not None:
            self.cena_celkem_bez_dph = pocet_baleni * self.cena_za_baleni_bez_dph
            if self.mnozstvi:
                self.jednotkova_cena = self.cena_celkem_bez_dph / self.mnozstvi

            dph_nasobek = Decimal("1") + ((self.sazba_dph or Decimal("0")) / Decimal("100"))
            self.cena_za_baleni_s_dph = self.cena_za_baleni_bez_dph * dph_nasobek
            self.cena_celkem_s_dph = self.cena_celkem_bez_dph * dph_nasobek
        elif self.cena_za_baleni_s_dph is not None:
            dph_nasobek = Decimal("1") + ((self.sazba_dph or Decimal("0")) / Decimal("100"))
            if dph_nasobek:
                self.cena_za_baleni_bez_dph = self.cena_za_baleni_s_dph / dph_nasobek
                self.cena_celkem_bez_dph = pocet_baleni * self.cena_za_baleni_bez_dph
                self.cena_celkem_s_dph = pocet_baleni * self.cena_za_baleni_s_dph
                if self.mnozstvi:
                    self.jednotkova_cena = self.cena_celkem_bez_dph / self.mnozstvi

    def save(self, *args, **kwargs):
        self.prepocitej_z_baleni()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.surovina} / {self.mnozstvi}"


class PolozkaVydejky(models.Model):
    vydejka = models.ForeignKey(
        Vydejka,
        verbose_name="Výdejka",
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, verbose_name="Surovina", on_delete=models.PROTECT)
    mnozstvi = models.DecimalField("Množství", max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = "Položka výdejky"
        verbose_name_plural = "Položky výdejky"

    def __str__(self):
        return f"{self.surovina} / {self.mnozstvi}"


class PolozkaInventury(models.Model):
    inventura = models.ForeignKey(
        Inventura,
        verbose_name="Inventura",
        on_delete=models.CASCADE,
        related_name="polozky",
    )
    surovina = models.ForeignKey(Surovina, verbose_name="Surovina", on_delete=models.PROTECT)
    stav_pred = models.DecimalField("Stav před inventurou", max_digits=12, decimal_places=3, default=Decimal("0"))
    fyzicky_stav = models.DecimalField("Fyzický stav", max_digits=12, decimal_places=3, default=Decimal("0"))
    rozdil = models.DecimalField("Rozdíl", max_digits=12, decimal_places=3, default=Decimal("0"))

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

    datum = models.DateTimeField("Datum", default=timezone.now, db_index=True)
    surovina = models.ForeignKey(
        Surovina,
        verbose_name="Surovina",
        on_delete=models.PROTECT,
        related_name="pohyby",
    )
    typ = models.CharField("Typ pohybu", max_length=20, choices=TYPY, db_index=True)
    mnozstvi = models.DecimalField("Množství", max_digits=12, decimal_places=3)
    cena_za_jednotku = models.DecimalField(
        "Cena za jednotku",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )

    prijem = models.ForeignKey(
        PrijemSkladu,
        verbose_name="Příjemka",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )
    vydejka = models.ForeignKey(
        Vydejka,
        verbose_name="Výdejka",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )
    inventura = models.ForeignKey(
        Inventura,
        verbose_name="Inventura",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pohyby",
    )

    poznamka = models.CharField("Poznámka", max_length=255, blank=True, default="")

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
        verbose_name="Jídlo",
        on_delete=models.CASCADE,
        related_name="receptura",
    )
    surovina = models.ForeignKey(Surovina, verbose_name="Surovina", on_delete=models.PROTECT)
    mnozstvi_na_porci = models.DecimalField("Množství na porci", max_digits=12, decimal_places=3)

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

    nazev = models.CharField("Název", max_length=255, unique=True)
    typ = models.CharField("Typ komponenty", max_length=20, choices=TYPY, default=TYP_OSTATNI, db_index=True)
    aktivni = models.BooleanField("Aktivní", default=True)
    poznamka = models.TextField("Poznámka", blank=True, default="")
    porce_text = models.CharField(
        "Text porce",
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
        verbose_name="Komponenta",
        on_delete=models.CASCADE,
        related_name="suroviny",
    )
    surovina = models.ForeignKey(Surovina, verbose_name="Surovina", on_delete=models.PROTECT)
    mnozstvi_na_porci = models.DecimalField("Množství na porci", max_digits=12, decimal_places=3)

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
        verbose_name="Jídlo",
        on_delete=models.CASCADE,
        related_name="komponenty_jidla",
    )
    komponenta = models.ForeignKey(
        KomponentaJidla,
        verbose_name="Komponenta",
        on_delete=models.PROTECT,
        related_name="jidla",
    )
    mnozstvi_nasobek = models.DecimalField(
        "Násobek množství",
        max_digits=8,
        decimal_places=3,
        default=Decimal("1.000"),
        help_text="1.0 = standardní porce komponenty, 0.5 = půl porce, 2.0 = dvojnásobek",
    )
    poradi = models.PositiveIntegerField("Pořadí", default=0)
    povinna = models.BooleanField("Povinná", default=True)

    class Meta:
        verbose_name = "Komponenta v jídle"
        verbose_name_plural = "Komponenty v jídlech"
        unique_together = [("jidlo", "komponenta")]
        ordering = ("poradi", "id")

    def __str__(self):
        return f"{self.jidlo} -> {self.komponenta}"


class NormaSpotrebnihoKose(models.Model):
    VEK_2_3 = "2_3"
    VEK_4_6 = "4_6"
    VEK_7_10 = "7_10"
    VEK_11_14 = "11_14"
    VEK_15_PLUS = "15_PLUS"

    VEKOVE_KATEGORIE = [
        (VEK_2_3, "2-3 roky"),
        (VEK_4_6, "4-6 let"),
        (VEK_7_10, "7-10 let"),
        (VEK_11_14, "11-14 let"),
        (VEK_15_PLUS, "15 a více let"),
    ]

    TYP_SNIDANE = "SNIDANE"
    TYP_PRESNIDAVKA = "PRESNIDAVKA"
    TYP_OBED = "OBED"
    TYP_SVACINA = "SVACINA"
    TYP_VECERE = "VECERE"
    TYP_PRESNIDAVKA_OBED_SVACINA = "PRESNIDAVKA_OBED_SVACINA"
    TYP_CELODENNI = "CELODENNI"

    TYPY_JIDLA = [
        (TYP_SNIDANE, "Snídaně"),
        (TYP_PRESNIDAVKA, "Přesnídávka"),
        (TYP_OBED, "Oběd"),
        (TYP_SVACINA, "Svačina"),
        (TYP_VECERE, "Večeře"),
        (TYP_PRESNIDAVKA_OBED_SVACINA, "Přesnídávka, oběd a svačina"),
        (TYP_CELODENNI, "Celodenní stravování"),
    ]

    stravovaci_skupina = models.ForeignKey(
        "users.StravovaciSkupina",
        verbose_name="Stravovací skupina",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="normy_sk",
    )
    vekova_kategorie = models.CharField(
        "Věková kategorie",
        max_length=20,
        choices=VEKOVE_KATEGORIE,
        default=VEK_15_PLUS,
    )
    typ_jidla = models.CharField(
        "Typ jídla",
        max_length=40,
        choices=TYPY_JIDLA,
        default=TYP_OBED,
    )
    skupina_sk = models.CharField(
        "Skupina spotřebního koše",
        max_length=50,
        choices=Surovina.SKUPINY_SPOTREBNIHO_KOSE_2025,
    )
    norma_g_den = models.DecimalField(
        "Denní norma [g / strávník / den]",
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
    )
    norma_g_mesic = models.DecimalField(
        "Měsíční norma [g]",
        max_digits=12,
        decimal_places=3,
        default=Decimal("0"),
        help_text="Zastaralé pole ponechané kvůli kompatibilitě; nový výpočet používá denní normu.",
    )

    class Meta:
        unique_together = [("vekova_kategorie", "typ_jidla", "skupina_sk", "stravovaci_skupina")]
        verbose_name = "Norma spotřebního koše"
        verbose_name_plural = "Normy spotřebního koše"

    def __str__(self):
        skupina = self.stravovaci_skupina or self.get_vekova_kategorie_display()
        return f"{skupina} / {self.get_typ_jidla_display()} / {self.get_skupina_sk_display()}"


class ToleranceSpotrebnihoKose(models.Model):
    stravovaci_skupina = models.ForeignKey(
        "users.StravovaciSkupina",
        verbose_name="Stravovací skupina",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="tolerance_sk",
    )
    skupina_sk = models.CharField(
        "Skupina spotřebního koše",
        max_length=50,
        choices=Surovina.SKUPINY_SPOTREBNIHO_KOSE_2025,
    )
    min_pct = models.DecimalField("Minimum [%]", max_digits=8, decimal_places=2)
    max_pct = models.DecimalField(
        "Maximum [%]",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Prázdná hodnota znamená, že horní limit není stanoven.",
    )

    class Meta:
        unique_together = [("stravovaci_skupina", "skupina_sk")]
        verbose_name = "Tolerance spotřebního koše"
        verbose_name_plural = "Tolerance spotřebního koše"

    def __str__(self):
        return f"{self.stravovaci_skupina} / {self.skupina_sk}"
