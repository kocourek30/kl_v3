from django.db import models
from django.conf import settings
from django.contrib.auth.models import Group
from django.utils.timezone import now


class DotacniPolitika(models.Model):
    skupina = models.OneToOneField(
        Group,
        on_delete=models.CASCADE,
        related_name='dotacni_politika',
        verbose_name="Skupina strávníků",
    )
    procento = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name="Výchozí procento dotace",
        help_text="Procentní sleva z ceníkové ceny. Hodnota 0 znamená bez procentní dotace.",
    )
    castka = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name="Výchozí pevná částka dotace",
        help_text="Pevná sleva v Kč na jednu porci. Hodnota 0 znamená bez pevné částky.",
    )
    denni_limit = models.PositiveIntegerField(
        default=0,
        verbose_name="Maximální počet dotovaných porcí za den",
        help_text="0 = bez denního limitu počtu dotovaných porcí.",
    )
    mesicni_limit = models.PositiveIntegerField(
        default=0,
        verbose_name="Maximální počet dotovaných porcí za měsíc",
        help_text="0 = bez měsíčního limitu počtu dotovaných porcí.",
    )
    denni_limit_castka = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Maximální výše dotace za den",
        help_text="0 Kč = bez denního finančního limitu.",
    )
    mesicni_limit_castka = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Maximální výše dotace za měsíc",
        help_text="0 Kč = bez měsíčního finančního limitu.",
    )
    
    class Meta:
        verbose_name = "Dotační politika"
        verbose_name_plural = "Dotační politiky"

    def __str__(self):
        return f"Dotační politika pro skupinu {self.skupina.name}"



class DotaceProJidelniskouSkupinu(models.Model):
    dotacni_politika = models.ForeignKey(
        DotacniPolitika,
        on_delete=models.CASCADE,
        related_name='dotace_skupiny',
        verbose_name="Dotační politika",
    )
    jidelniskova_skupina = models.ForeignKey(
        'jidelnicek.DruhJidla',
        on_delete=models.CASCADE,
        verbose_name="Druh jídla",
    )
    procento = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name="Procento dotace",
        help_text="Volitelný přepis výchozího procenta pro tento druh jídla.",
    )
    castka = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        verbose_name="Pevná částka dotace",
        help_text="Volitelný přepis výchozí pevné částky pro tento druh jídla.",
    )
    denni_limit = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Maximální počet za den",
        help_text="Volitelný limit dotovaných porcí pro tento druh jídla. Prázdné pole použije obecný limit z dotační politiky, 0 znamená bez limitu.",
    )
    mesicni_limit = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Maximální počet za měsíc",
        help_text="Volitelný měsíční limit dotovaných porcí pro tento druh jídla. Prázdné pole použije obecný limit z dotační politiky, 0 znamená bez limitu.",
    )

    class Meta:
        unique_together = ('dotacni_politika', 'jidelniskova_skupina')
        verbose_name = "Dotace podle druhu jídla"
        verbose_name_plural = "Dotace podle druhů jídel"

    def __str__(self):
        p = self.procento if self.procento is not None else self.dotacni_politika.procento
        c = self.castka if self.castka is not None else self.dotacni_politika.castka
        return f"{self.jidelniskova_skupina} - {p}% / {c} Kč"


class SkupinoveNastaveni(models.Model):
    skupina = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='nastaveni', verbose_name="Skupina strávníků")
    cerpani_debit = models.BooleanField(default=False, verbose_name="Povolit čerpání do mínusu", help_text="Skupina může čerpat konto do povoleného debetu.")
    nutnost_dobit = models.BooleanField(default=False, verbose_name="Vyžadovat kladný zůstatek", help_text="Skupina musí mít před čerpáním dostatek peněz na kontě.")
    debit_limit = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        verbose_name="Limit debetu",
        help_text="Maximální povolený debet (v mínusu) na kontě při povoleném čerpání debetu. Např. -1500 znamená možnost čerpat až do -1500 Kč."
    )
    
    class Meta:
        verbose_name = "Nastavení konta"
        verbose_name_plural = "Nastavení kont"

    def __str__(self):
        if self.cerpani_debit:
            return f"Nastavení skupiny {self.skupina.name}: debet do {self.debit_limit} Kč"
        return f"Nastavení skupiny {self.skupina.name}"


class Dotace(models.Model):
    uzivatel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dotace', verbose_name="Uživatel")
    politika = models.ForeignKey(DotacniPolitika, on_delete=models.CASCADE, verbose_name="Dotační politika")
    datum = models.DateField(default=now, verbose_name="Datum")
    castka = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Částka")

    class Meta:
        verbose_name = "Připsaná dotace"
        verbose_name_plural = "Připsané dotace"

    def __str__(self):
        return f"Dotace {self.castka} Kč pro {self.uzivatel.username} z {self.datum}"
    
