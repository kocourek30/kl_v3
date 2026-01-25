from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group
from PIL import Image
from django.core.files.base import ContentFile
from io import BytesIO


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
    ikona = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ikona druhu jídla"
    )

    class Meta:
        verbose_name = "Druh jídla"
        verbose_name_plural = "Druhy jídel"

    viditelne_pro_skupiny = models.ManyToManyField(
        Group,
        blank=True,
        related_name="viditelne_druhy_jidel",
        help_text="Pokud je prázdné, druh je viditelný pro všechny skupiny.",
    )

    def __str__(self):
        return self.nazev


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
            except Exception as e:
                print("⚠️ Nelze zpracovat fotku jídla:", e)

    # v modelu Jidlo
    def spolecne_alergeny(self, user):
        from users.models import CustomUser  # podle struktury

        if user is None or not getattr(user, "is_authenticated", False):
            return self.alergeny.none()
        return self.alergeny.filter(id__in=user.alergeny.values("id"))

    
    def vypocitej_spotrebu_surovin(self, pocet_porci: int):
        """
        Vrátí dict {surovina: celkové_mnozstvi} pro daný počet porcí.
        Nic neodečítá ze skladu, jen počítá.
        """
        from sklad.models import RecepturaPolozka  # import uvnitř kvůli cyklickým závislostem

        spotreba = {}
        polozky = self.receptura.select_related("surovina").all()

        for pol in polozky:
            celkem = pol.mnozstvi_na_porci * Decimal(pocet_porci)
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

    def __str__(self):
        return f"{self.druh_jidla} - {self.jidlo} v {self.jidelnicek}"
