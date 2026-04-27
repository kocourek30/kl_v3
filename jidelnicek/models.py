import logging
import unicodedata
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from django.utils import timezone
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

    def clean(self):
        super().clean()
        if self.pk and not self.druh_id and self.polozkajidelnicku_set.exists():
            raise ValidationError(
                {
                    "druh": (
                        "Jídlo použité v jídelníčku musí mít vyplněný druh jídla. "
                        "Ten určuje zařazení, ceny po dotacích, limity i viditelnost."
                    )
                }
            )

    @property
    def vychozi_ikona(self):
        druh_nazev = self.druh.nazev if self.druh_id and self.druh else ""
        return self.ikona or vychozi_ikona_jidla(self.nazev, druh_nazev)

    @property
    def ma_fotku(self):
        return bool(self.foto)
    
    def save(self, *args, **kwargs):
        puvodni_druh_id = None
        if self.pk:
            puvodni_druh_id = (
                Jidlo.objects.filter(pk=self.pk).values_list("druh_id", flat=True).first()
            )

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

        if self.druh_id and puvodni_druh_id != self.druh_id:
            self.polozkajidelnicku_set.exclude(druh_jidla_id=self.druh_id).update(
                druh_jidla_id=self.druh_id
            )

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
        indexes = [
            models.Index(fields=["platnost_od", "platnost_do"]),
            models.Index(fields=["platnost_do", "platnost_od"]),
        ]
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
        indexes = [
            models.Index(fields=["jidelnicek", "druh_jidla"]),
            models.Index(fields=["jidelnicek", "jidlo"]),
            models.Index(fields=["druh_jidla", "jidlo"]),
        ]
        verbose_name = "Položka jídelníčku"
        verbose_name_plural = "Položky jídelníčku"
        ordering = ("druh_jidla__poradi", "druh_jidla__nazev", "jidlo__nazev")

    def clean(self):
        super().clean()
        if not self.jidlo_id:
            return

        jidlo_druh_id = self.jidlo.druh_id
        if not jidlo_druh_id:
            raise ValidationError(
                {
                    "jidlo": (
                        "Vybrané jídlo nemá nastavený druh jídla. "
                        "Nejdřív ho doplň v katalogu jídel."
                    )
                }
            )

        if self.druh_jidla_id and self.druh_jidla_id != jidlo_druh_id:
            raise ValidationError(
                {
                    "jidlo": (
                        f"Vybrané jídlo patří do druhu „{self.jidlo.druh}“, "
                        f"ale tento řádek jídelníčku je určený pro „{self.druh_jidla}“."
                    )
                }
            )

        if not self.druh_jidla_id:
            self.druh_jidla_id = jidlo_druh_id

    def save(self, *args, **kwargs):
        if self.jidlo_id and self.jidlo.druh_id and not self.druh_jidla_id:
            self.druh_jidla_id = self.jidlo.druh_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.druh_jidla} - {self.jidlo} v {self.jidelnicek}"


class MenuImportRun(models.Model):
    SOURCE_DATAX = "datax"
    SOURCE_AUTO = "auto"
    SOURCE_CHOICES = (
        (SOURCE_DATAX, "Datax DBF import"),
        (SOURCE_AUTO, "Automatický import"),
    )

    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_RUNNING, "Probíhá"),
        (STATUS_SUCCESS, "Hotovo"),
        (STATUS_FAILED, "Chyba"),
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_DATAX,
        verbose_name="Zdroj importu",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RUNNING,
        db_index=True,
        verbose_name="Stav",
    )
    started_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Spuštěno",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Dokončeno",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="menu_import_runs",
        verbose_name="Spustil",
    )
    dry_run = models.BooleanField(
        default=False,
        verbose_name="Dry-run",
    )
    rows_read = models.PositiveIntegerField(
        default=0,
        verbose_name="Načtených řádků",
    )
    rows_after_merge = models.PositiveIntegerField(
        default=0,
        verbose_name="Řádků po sloučení",
    )
    menu_days = models.PositiveIntegerField(
        default=0,
        verbose_name="Dnů v importu",
    )
    menus_created = models.PositiveIntegerField(
        default=0,
        verbose_name="Vytvořených jídelníčků",
    )
    foods_created = models.PositiveIntegerField(
        default=0,
        verbose_name="Nových jídel",
    )
    items_created = models.PositiveIntegerField(
        default=0,
        verbose_name="Vytvořených položek",
    )
    summary = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Souhrn",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Chybová zpráva",
    )

    class Meta:
        verbose_name = "Běh importu jídelníčku"
        verbose_name_plural = "Běhy importu jídelníčku"
        ordering = ("-started_at",)

    def __str__(self):
        return f"{self.get_source_display()} • {self.get_status_display()} • {timezone.localtime(self.started_at):%d.%m.%Y %H:%M}"

    @property
    def duration_seconds(self):
        if not self.finished_at:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds()))


class JidloPhotoProposal(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_APPLIED = "applied"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Čeká na schválení"),
        (STATUS_APPROVED, "Schváleno"),
        (STATUS_REJECTED, "Zamítnuto"),
        (STATUS_APPLIED, "Použito v jídle"),
    )

    jidlo = models.ForeignKey(
        "Jidlo",
        on_delete=models.CASCADE,
        related_name="photo_proposals",
        verbose_name="Jídlo",
    )
    image = models.ImageField(
        upload_to="jidla/proposals/%Y/%m/",
        verbose_name="Návrh fotky",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="Stav návrhu",
    )
    prompt = models.TextField(
        blank=True,
        verbose_name="Použitý prompt",
    )
    model_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Model",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Chybová zpráva",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Vytvořeno",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Schváleno / zamítnuto",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_food_photo_proposals",
        verbose_name="Zkontroloval",
    )

    class Meta:
        verbose_name = "Návrh AI fotky jídla"
        verbose_name_plural = "Návrhy AI fotek jídel"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.jidlo.nazev} • {self.get_status_display()} • {timezone.localtime(self.created_at):%d.%m.%Y %H:%M}"
