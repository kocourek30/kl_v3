from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils.timezone import now
from django.db.models import Sum, F
from decimal import Decimal
from objednavky.models import OrderItem, Order


from django.db import models
from django.contrib.auth.models import Group


class StravovaciSkupina(models.Model):
    TYP_VZDELAVANI = [
        ("MS", "Mateřská škola"),
        ("ZS1", "ZŠ 7–10 let"),
        ("ZS2", "ZŠ 11–14 let"),
        ("SS", "Střední škola"),
        ("JINE", "Jiné"),
    ]



    kod = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Kód skupiny",
        help_text="Např. SS, ZS1, ZS2…"
    )
    nazev = models.CharField(
        max_length=100,
        verbose_name="Název skupiny",
        help_text="Např. 'Střední škola – žáci'."
    )
    typ_vzdelavani = models.CharField(
        max_length=10,
        choices=TYP_VZDELAVANI,
        default="SS",
        verbose_name="Typ vzdělávání",
    )
    # volitelná vazba na Django Group, kterou už používáš pro dotace
    django_group = models.OneToOneField(
        Group,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stravovaci_skupina",
        verbose_name="Django skupina (pro dotace, oprávnění)",
    )

    class Meta:
        verbose_name = "Stravovací skupina"
        verbose_name_plural = "Stravovací skupiny"

    def __str__(self):
        return f"{self.nazev} ({self.kod})"



class Vklad(models.Model):
    STATUS_CHOICES = [
        ('standard', 'Standardní vklad'),
        ('nulovani_konta', 'Nulování konta'),
    ]
    uzivatel = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='vklady')
    castka = models.DecimalField(max_digits=10, decimal_places=2)
    datum = models.DateTimeField(default=now)
    poznamka = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='standard', editable=False)

    class Meta:
        verbose_name = "Vklad na konto"
        verbose_name_plural = "Vklady na konta"

    def _uzivatel_ma_povoleny_debet(self):
        if not self.uzivatel_id:
            return False
        for skupina in self.uzivatel.groups.all():
            nastaveni = getattr(skupina, "nastaveni", None)
            if nastaveni and nastaveni.cerpani_debit:
                return True
        return False

    def clean(self):
        super().clean()
        if (
            self.status == "standard"
            and self.castka is not None
            and self.castka > 0
            and self._uzivatel_ma_povoleny_debet()
        ):
            raise ValidationError({
                "uzivatel": (
                    "Tomuto uživateli je povoleno čerpání do debetu, "
                    "proto mu nelze vložit peníze na konto. "
                    "Debetní konto se vyrovnává pouze systémovým nulováním."
                )
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Vklad {self.castka} Kč pro {self.uzivatel} ({self.datum.date()})"


class CustomUser(AbstractUser):
    identifikacni_medium = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_("Identifikační médium")
    )     
    osobni_cislo = models.CharField(max_length=100, blank=True, null=True, verbose_name=_("Osobní číslo"))
    alergeny = models.ManyToManyField('jidelnicek.Alergen', blank=True, verbose_name=_("Alergeny"))
    stravovaci_skupina = models.ForeignKey(
        StravovaciSkupina,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Stravovací skupina"),
        help_text=_("Např. SŠ žák, ZŠ 1. stupeň…"),
    )

    def __str__(self):
        return self.username

    @property
    def aktualni_zustatek(self):
        """✅ SPRÁVNÝ VÝPOČET ZŮSTATKU"""
        try:
            # 1. VKLADY
            soucet_vkladu = self.vklady.aggregate(soucet=Sum('castka'))['soucet'] or Decimal('0')
            
            # 2. DOTACE (pokud model existuje)
            soucet_dotaci = Decimal('0')
            if hasattr(self, 'dotace'):
                soucet_dotaci = self.dotace.aggregate(soucet=Sum('castka'))['soucet'] or Decimal('0')
            
            # 3. OBJEDNÁVKY (SPRÁVNĚ!)
            soucet_objednavek = OrderItem.objects.filter(
                order__user=self,
                order__status__in=['zalozena-obsluhou', 'objednano', 'vydano', 'nevyzvednuto']            ).aggregate(
                total=Sum(F('quantity') * F('cena'))  # ← quantity * cena!
            )['total'] or Decimal('0')
            
            # VÝPOČET
            zustatek = soucet_vkladu + soucet_dotaci - soucet_objednavek
            return zustatek.quantize(Decimal('0.01'))
            
        except Exception as e:
            print(f"⚠️ aktualni_zustatek CHYBA: {e}")
            return Decimal('0')
