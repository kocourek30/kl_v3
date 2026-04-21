import logging
import unicodedata
from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from PIL import Image
from django.core.files.base import ContentFile
from io import BytesIO


logger = logging.getLogger(__name__)


DRUH_JIDLA_DEFAULT_ICONS = {
    "polevka": "fa-solid fa-bowl-food",
    "hlavni chod": "fa-solid fa-utensils",
    "obed": "fa-solid fa-utensils",
    "dezert": "fa-solid fa-ice-cream",
    "snidane": "fa-solid fa-mug-saucer",
    "snidane 1": "fa-solid fa-mug-saucer",
    "snidane 2": "fa-solid fa-bread-slice",
    "presnidavka": "fa-solid fa-apple-whole",
    "svacina": "fa-solid fa-cheese",
    "vecere": "fa-solid fa-drumstick-bite",
    "pozdni vecere": "fa-solid fa-moon",
    "napoj": "fa-solid fa-glass-water",
}

JIDLO_KEYWORD_ICONS = (
    (("polevka", "vyvar", "krem"), "fa-solid fa-bowl-food"),
    (("kure", "kruti", "kachna", "slepice"), "fa-solid fa-drumstick-bite"),
    (("hovezi", "veprove", "maso", "gulas", "rizek", "karbanatek", "koule"), "fa-solid fa-drumstick-bite"),
    (("ryba", "losos", "treska", "kapr", "tun", "file"), "fa-solid fa-fish"),
    (("testoviny", "spagety", "kolinka", "nudle", "tagliatelle"), "fa-solid fa-bacon"),
    (("knedlik", "brambor", "brambory", "kase", "ryze", "rizoto"), "fa-solid fa-bowl-rice"),
    (("salat", "zelenina", "okurka", "rajce", "mrkev"), "fa-solid fa-carrot"),
    (("jogurt", "mleko", "tvaroh", "syr"), "fa-solid fa-cheese"),
    (("jablko", "ovoce", "banan", "hruska"), "fa-solid fa-apple-whole"),
    (("dezert", "kolac", "buchta", "krem", "puding", "kase", "krupicova"), "fa-solid fa-ice-cream"),
    (("napoj", "caj", "voda", "dzus", "stava", "kava"), "fa-solid fa-glass-water"),
)


def _normalizuj_text(value):
    text = str(value or "").strip().lower()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def vychozi_ikona_druhu_jidla(nazev):
    return DRUH_JIDLA_DEFAULT_ICONS.get(_normalizuj_text(nazev), "fa-solid fa-utensils")


def vychozi_ikona_jidla(nazev, druh_nazev=""):
    normalized = _normalizuj_text(nazev)
    for keywords, icon in JIDLO_KEYWORD_ICONS:
        if any(keyword in normalized for keyword in keywords):
            return icon
    return vychozi_ikona_druhu_jidla(druh_nazev)


class Alergen(models.Model):
    nazev = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Název alergenu"
    )
    ikona = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="bi-alarm"
    )

    class Meta:
        verbose_name = "Alergen"
        verbose_name_plural = "Alergeny"

    def __str__(self):
        return self.nazev


class DruhJidla(models.Model):
    nazev = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Název druhu jídla"
    )
    poradi = models.PositiveIntegerField(
        default=100,
        db_index=True,
        verbose_name="Pořadí zobrazení",
        help_text="Nižší číslo se zobrazí dříve. Například 10 = polévka, 20 = hlavní chod, 30 = dezert.",
    )
    ikona = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ikona druhu jídla"
    )

    class Meta:
        verbose_name = "Druh jídla"
        verbose_name_plural = "Druhy jídel"
        ordering = ("poradi", "nazev")

    viditelne_pro_skupiny = models.ManyToManyField(
        Group,
        blank=True,
        related_name="viditelne_druhy_jidel",
        help_text="Pokud je prázdné, druh je viditelný pro všechny skupiny.",
    )

    def __str__(self):
        return self.nazev

    @property
    def vychozi_ikona(self):
        return self.ikona or vychozi_ikona_druhu_jidla(self.nazev)


class Jidlo(models.Model):
    nazev = models.CharField(max_length=200, verbose_name="Název jídla")
    cena = models.DecimalField(max_digits=6, decimal_places=2, verbose_name="Cena")
    alergeny = models.ManyToManyField(Alergen, blank=True, verbose_name="Alergeny")
    ikona = models.CharField(max_length=100, blank=True, verbose_name="Ikona jídla")
    druh = models.ForeignKey(
        'DruhJidla',
        on_delete=models.PROTECT,
        verbose_name="Druh jídla",
        null=True,
        blank=True
    )

    # Nutriční údaje - nepovinné
    kcal = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="Energetická hodnota (kcal)")
    bílkoviny = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Bílkoviny (g)")
    tuky = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Tuky (g)")
    sacharidy = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Sacharidy (g)")
    sk_rybi_pokrm = models.BooleanField(default=False, verbose_name="Spotřební koš: rybí pokrm")
    sk_bezmasy_pokrm = models.BooleanField(default=False, verbose_name="Spotřební koš: bezmasý pokrm")
    sk_bile_maso = models.BooleanField(default=False, verbose_name="Spotřební koš: bílé maso")
    sk_cervene_maso = models.BooleanField(default=False, verbose_name="Spotřební koš: červené maso")
    sk_sladky_pokrm = models.BooleanField(default=False, verbose_name="Spotřební koš: sladký pokrm")
    sk_jemne_pecivo = models.BooleanField(default=False, verbose_name="Spotřební koš: jemné pečivo")
    sk_dezert_s_volnym_cukrem = models.BooleanField(
        default=False,
        verbose_name="Spotřební koš: dezert s volným cukrem",
    )
    sk_slazeny_napoj = models.BooleanField(
        default=False,
        verbose_name="Spotřební koš: nápoj s volným cukrem",
    )

    foto = models.ImageField(
        upload_to="jidla/",
        blank=True,
        null=True,
        verbose_name="Fotka jídla"
    )

    class Meta:
        verbose_name = "Jídlo"
        verbose_name_plural = "Jídla"

    def __str__(self):
        return self.nazev

    @property
    def vychozi_ikona(self):
        druh_nazev = self.druh.nazev if self.druh_id and self.druh else ""
        return self.ikona or vychozi_ikona_jidla(self.nazev, druh_nazev)

    @property
    def ma_fotku(self):
        return bool(self.foto)
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.foto:
            try:
                img = Image.open(self.foto.path)
                max_size = (800, 800)  # cílové max rozlišení

                img.thumbnail(max_size, Image.LANCZOS)
                img_io = BytesIO()
                img.save(img_io, format="JPEG", quality=80)
                img_content = ContentFile(img_io.getvalue(), name=self.foto.name)

                # znovu ulož zmenšenou verzi
                self.foto.save(self.foto.name, img_content, save=False)
                super().save(update_fields=["foto"])
            except Exception:
                logger.exception("Nelze zpracovat fotku jídla.")

    # v modelu Jidlo
    def spolecne_alergeny(self, user):
        from users.models import CustomUser  # podle struktury

        if user is None or not getattr(user, "is_authenticated", False):
            return self.alergeny.none()
        return self.alergeny.filter(id__in=user.alergeny.values("id"))

    
    def vypocitej_spotrebu_surovin(self, pocet_porci: int):
        """
        Vrátí dict {surovina: celkové_mnozstvi} pro daný počet porcí.

        Priorita:
        1) nové komponenty Jidlo -> Komponenta -> Surovina
        2) fallback na starou přímou recepturu Jidlo -> RecepturaPolozka
        """
        from sklad.models import JidloKomponenta, RecepturaPolozka

        spotreba = {}

        komponenty = (
            self.komponenty_jidla
            .select_related("komponenta")
            .prefetch_related("komponenta__suroviny__surovina")
            .all()
        )

        if komponenty.exists():
            for vazba in komponenty:
                nasobek = vazba.mnozstvi_nasobek or Decimal("1")
                for pol in vazba.komponenta.suroviny.all():
                    celkem = (pol.mnozstvi_na_porci or Decimal("0")) * nasobek * Decimal(pocet_porci)
                    spotreba[pol.surovina] = spotreba.get(pol.surovina, Decimal("0")) + celkem
            return spotreba

        # fallback na původní přímou recepturu
        polozky = self.receptura.select_related("surovina").all()
        for pol in polozky:
            celkem = (pol.mnozstvi_na_porci or Decimal("0")) * Decimal(pocet_porci)
            spotreba[pol.surovina] = spotreba.get(pol.surovina, Decimal("0")) + celkem

        return spotreba


class Jidelnicek(models.Model):
    platnost_od = models.DateField(
        verbose_name="Platnost od"
    )
    platnost_do = models.DateField(
        verbose_name="Platnost do"
    )
    ikona = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ikona jídelníčku"
    )

    class Meta:
        verbose_name = "Jídelníček"
        verbose_name_plural = "Jídelníčky"

    def clean(self):
        if self.platnost_do < self.platnost_od:
            raise ValidationError("Datum 'Platnost do' musí být stejné nebo větší než datum 'Platnost od'.")

        prekryv = Jidelnicek.objects.filter(
            platnost_od__lte=self.platnost_do,
            platnost_do__gte=self.platnost_od
        )
        if self.pk:
            prekryv = prekryv.exclude(pk=self.pk)

        if prekryv.exists():
            raise ValidationError("Jídelníček s překrývajícím se obdobím již existuje.")
    def obsah_textove(self):
        polozky = self.polozky.select_related('druh_jidla', 'jidlo').all()
        return ", ".join(f"{p.druh_jidla} - {p.jidlo}" for p in polozky)

    def __str__(self):
        return f"Jídelníček od {self.platnost_od} do {self.platnost_do}"


class PolozkaJidelnicku(models.Model):
    jidelnicek = models.ForeignKey('Jidelnicek', on_delete=models.CASCADE, related_name='polozky')
    druh_jidla = models.ForeignKey(
        'DruhJidla',
        on_delete=models.PROTECT,
        verbose_name="Druh jídla"
    )
    jidlo = models.ForeignKey('Jidlo', on_delete=models.PROTECT, verbose_name="Jídlo")

    
    class Meta:
        verbose_name = "Položka jídelníčku"
        verbose_name_plural = "Položky jídelníčku"
        ordering = ("druh_jidla__poradi", "druh_jidla__nazev", "jidlo__nazev")

    def __str__(self):
        return f"{self.druh_jidla} - {self.jidlo} v {self.jidelnicek}"
