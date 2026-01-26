from django.db import models
from django.conf import settings
from django.contrib.auth.models import Group
from django.utils.timezone import now


class DotacniPolitika(models.Model):
    skupina = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='dotacni_politika')
    procento = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Výchozí procentní nárok na dotaci"
    )
    castka = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Výchozí fixní částka dotace"
    )
    mesicni_limit = models.PositiveIntegerField(
        default=0,
        help_text="Maximální počet dotací za měsíc"
    )
    
    class Meta:
        verbose_name = "Dotační politika"
        verbose_name_plural = "Dotační politiky"



class DotaceProJidelniskouSkupinu(models.Model):
    dotacni_politika = models.ForeignKey(DotacniPolitika, on_delete=models.CASCADE, related_name='dotace_skupiny')
    jidelniskova_skupina = models.ForeignKey('jidelnicek.DruhJidla', on_delete=models.CASCADE)
    procento = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True, help_text="Přepis procenta")
    castka = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True, help_text="Přepis částky")

    class Meta:
        unique_together = ('dotacni_politika', 'jidelniskova_skupina')
        verbose_name = "Přepis dotace pro skupinu jídla"
        verbose_name_plural = "Přepisy dotací pro skupiny jídel"

    def __str__(self):
        p = self.procento if self.procento is not None else self.dotacni_politika.procento
        c = self.castka if self.castka is not None else self.dotacni_politika.castka
        return f"{self.jidelniskova_skupina} - {p}% / {c} Kč"


class SkupinoveNastaveni(models.Model):
    skupina = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='nastaveni')
    cerpani_debit = models.BooleanField(default=False, help_text="Skupina může čerpat do debetu")
    nutnost_dobit = models.BooleanField(default=False, help_text="Skupina musí mít peníze na účtu při čerpání")
    debit_limit = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
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
    uzivatel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dotace')
    politika = models.ForeignKey(DotacniPolitika, on_delete=models.CASCADE)
    datum = models.DateField(default=now)
    castka = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        verbose_name = "Dotace"
        verbose_name_plural = "Dotace"

    def __str__(self):
        return f"Dotace {self.castka} Kč pro {self.uzivatel.username} z {self.datum}"
    
