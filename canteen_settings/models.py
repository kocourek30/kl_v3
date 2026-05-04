# canteen_settings/models.py

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import Group
from jidelnicek.models import DruhJidla


class CanteenContact(models.Model):
    canteen_name = models.CharField(max_length=120, verbose_name="Název jídelny", blank=True)
    contact_name = models.CharField(max_length=100, verbose_name="Kontaktní osoba")
    contact_email = models.EmailField(verbose_name="Email")
    contact_phone = models.CharField(max_length=20, verbose_name="Telefon")
    address = models.TextField(verbose_name="Adresa provozovny")

    class Meta:
        verbose_name = "Kontakt jídelny"
        verbose_name_plural = "Kontakty jídelny"

    def __str__(self):
        return f"{self.canteen_name} – {self.contact_name} ({self.contact_phone})"


class OrderClosingTime(models.Model):
    je_aktivni = models.BooleanField(default=True, verbose_name="Aktivní nastavení")

    druh_jidla = models.ForeignKey(
        'jidelnicek.DruhJidla',
        on_delete=models.CASCADE,
        related_name='order_closing_times',
        verbose_name="Druh jídla",
        null=True,
        blank=True,
        help_text="Pokud není vyplněno, nastavení platí globálně pro všechny druhy."
    )

    # ❗ počet provozních dní dopředu pro VYTVOŘENÍ / ZMĚNU objednávky
    advance_days = models.PositiveIntegerField(
        default=1,
        verbose_name="Počet provozních dní dopředu pro objednání",
        help_text="Např. 3 = objednávat lze nejpozději 3 provozní dny předem do stanoveného času."
    )

    closing_time = models.TimeField(
        default=timezone.datetime.strptime("07:00", "%H:%M").time(),
        verbose_name="Čas uzavření objednávek"
    )

    # ❗ počet provozních dní dopředu pro ZRUŠENÍ objednávky
    cancel_days = models.PositiveIntegerField(
        default=0,
        verbose_name="Počet provozních dní dopředu pro zrušení",
        help_text="Např. 0 = rušení ve stejný provozní den do stanoveného času."
    )

    cancel_until_time = models.TimeField(
        default=timezone.datetime.strptime("09:00", "%H:%M").time(),
        verbose_name="Čas povoleného zrušení objednávky",
        help_text="Do tohoto času lze objednávku zrušit dle nastaveného počtu dní dopředu."
    )

    class Meta:
        verbose_name = "Čas uzavření objednávek"
        verbose_name_plural = "Časy uzavření objednávek"

    def __str__(self):
        druh = self.druh_jidla.nazev if self.druh_jidla else "Všechny druhy"
        return (
            f"{druh}: objednat {self.advance_days} prac. dní dopředu do "
            f"{self.closing_time.strftime('%H:%M')}, "
            f"zrušit {self.cancel_days} prac. dní dopředu do "
            f"{self.cancel_until_time.strftime('%H:%M')}"
        )


class GroupOrderLimit(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    druh_jidla = models.ForeignKey(DruhJidla, on_delete=models.CASCADE)
    max_orders_per_day = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ['group', 'druh_jidla']
        verbose_name = "Limit objednávek skupiny"
        verbose_name_plural = "Limity objednávek skupin"

    def __str__(self):
        return f"{self.group.name} - {self.druh_jidla.nazev}: {self.max_orders_per_day}"


class MealPickupTime(models.Model):
    druh_jidla = models.ForeignKey(
        'jidelnicek.DruhJidla', 
        on_delete=models.CASCADE, 
        verbose_name="Druh jídla",
        related_name='pickup_times'
    )
    pickup_from = models.TimeField(
        verbose_name="Výdej od",
        help_text="Čas zahájení výdeje (každý den)"
    )
    pickup_to = models.TimeField(
        verbose_name="Výdej do",
        help_text="Čas ukončení výdeje (každý den)"
    )

    class Meta:
        verbose_name = "Výdejní čas jídla"
        verbose_name_plural = "Výdejní časy jídel"
        unique_together = ['druh_jidla']

    def __str__(self):
        return f"{self.druh_jidla.nazev}: {self.pickup_from.strftime('%H:%M')} - {self.pickup_to.strftime('%H:%M')} (denně)"


# ✅ NOVÉ MODELY PRO PROVOZNÍ DNY A VÝJIMKY

class OperatingDays(models.Model):
    """Standardní provozní dny v týdnu"""
    DAYS_OF_WEEK = [
        (0, 'Pondělí'),
        (1, 'Úterý'),
        (2, 'Středa'),
        (3, 'Čtvrtek'),
        (4, 'Pátek'),
        (5, 'Sobota'),
        (6, 'Neděle'),
    ]
    
    day_of_week = models.IntegerField(
        choices=DAYS_OF_WEEK,
        unique=True,
        verbose_name="Den v týdnu"
    )
    is_operating = models.BooleanField(
        default=True,
        verbose_name="Jídelna v provozu"
    )
    
    class Meta:
        verbose_name = "Provozní den (týdenní)"
        verbose_name_plural = "Provozní dny (týdenní)"
        ordering = ['day_of_week']
    
    def __str__(self):
        day_name = dict(self.DAYS_OF_WEEK)[self.day_of_week]
        status = "✅ Provoz" if self.is_operating else "❌ Zavřeno"
        return f"{day_name}: {status}"


class OperatingExceptions(models.Model):
    """Výjimky z pravidelného provozu - konkrétní dny"""
    EXCEPTION_TYPE = [
        ('closed', '❌ Zavřeno'),
        ('open', '✅ Otevřeno'),
    ]
    
    date = models.DateField(
        unique=True,
        verbose_name="Datum"
    )
    exception_type = models.CharField(
        max_length=10,
        choices=EXCEPTION_TYPE,
        default='closed',
        verbose_name="Typ výjimky"
    )
    reason = models.CharField(
        max_length=200,
        verbose_name="Důvod",
        blank=True,
        help_text="Např. 'Státní svátek', 'Prázdniny', 'Mimořádný provoz'"
    )
    
    class Meta:
        verbose_name = "Výjimka z provozu"
        verbose_name_plural = "Výjimky z provozu"
        ordering = ['date']
    
    def __str__(self):
        type_display = dict(self.EXCEPTION_TYPE)[self.exception_type]
        reason_text = f" - {self.reason}" if self.reason else ""
        return f"{self.date.strftime('%d.%m.%Y')}: {type_display}{reason_text}"
