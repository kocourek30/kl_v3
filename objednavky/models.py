from django.db import models
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, F
from datetime import timedelta
from jidelnicek.models import PolozkaJidelnicku
from django.contrib.auth.models import Group
from canteen_settings.models import OrderClosingTime, GroupOrderLimit  # ✅ Správný import
from dotace.models import SkupinoveNastaveni, DotacniPolitika, DotaceProJidelniskouSkupinu
from decimal import Decimal
from users.group_utils import get_effective_user_groups, get_primary_effective_group




class Order(models.Model):
    STATUS_CHOICES = [
        ('zalozena-obsluhou', 'Založená obsluhou'),
        ('objednano', 'Objednáno'),
        ('zruseno-uzivatelem', 'Zrušeno uživatelem'),
        ('zruseno-obsluhou', 'Zrušeno obsluhou'),
        ('castecne-vydano', 'Částečně vydáno'),
        ('vydano', 'Vydáno'),
        ('nevyzvednuto', 'Nevyzvednuto'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    datum_vydeje = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # ← PŘIDAT
    datum_vydani = models.DateTimeField(null=True, blank=True, verbose_name="Datum a čas vydání")  # ← PŘIDAT
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default='objednano'
    )
    storno_user = models.ForeignKey('users.CustomUser', null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Storno provedl")
    storno_datum = models.DateTimeField(null=True, blank=True, verbose_name="Storno datum")
    class Meta:
        unique_together = ('user', 'datum_vydeje')
        verbose_name = "Objednávka"
        verbose_name_plural = "Objednávky uživatelů"

    def __str__(self):
        return f"Objednávka {self.user} na {self.datum_vydeje}"

    def total_quantity(self):
        if hasattr(self, '_prefetched_objects_cache') and 'items' in self._prefetched_objects_cache:
            return sum(item.quantity for item in self.items.all())
        return self.items.aggregate(total=Sum('quantity'))['total'] or 0

    def total_price(self):
        if hasattr(self, '_prefetched_objects_cache') and 'items' in self._prefetched_objects_cache:
            return sum(item.quantity * item.cena for item in self.items.all())
        return self.items.aggregate(total=Sum(F('quantity') * F('cena')))['total'] or Decimal('0')


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(PolozkaJidelnicku, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    cena = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    vydano = models.BooleanField(default=False)  # NOVÉ POLE
    datum_vydani = models.DateTimeField(null=True, blank=True) 

    def __str__(self):
        return f"{self.menu_item.jidlo.nazev} x {self.quantity}"

    def total_price(self):
        return self.quantity * self.cena
    
    class Meta:
        unique_together = ('order', 'menu_item')
        indexes = [
            models.Index(fields=['vydano', 'datum_vydani']),
            models.Index(fields=['order', 'menu_item']),
        ]
        verbose_name = "Objednaná jídla"
        verbose_name_plural = "Objednaná jídla"    

class OrderValidator:
    @staticmethod
    def can_order_for_date(user, target_date):
        """Kontrola uzavíracího času objednávek z canteen_settings"""
        if user.is_staff:
            return True, ""
        
        try:
            settings = OrderClosingTime.objects.first()
            if not settings:
                return True, ""
            
            closing_date = target_date - timedelta(days=settings.advance_days)
            closing_datetime = timezone.datetime.combine(closing_date, settings.closing_time)
            closing_datetime = timezone.make_aware(closing_datetime, timezone.get_current_timezone())
            
            if timezone.now() >= closing_datetime:
                return False, f"Objednávky na {target_date.strftime('%d.%m.%Y')} jsou uzavřeny."
            return True, ""
        except:
            return True, ""

    @staticmethod
    def check_group_limit(user, menu_item, target_date, quantity):
        """Kontrola limitu objednávek podle skupiny a druhu jídla"""
        if user.is_staff:
            return True, ""
        
        user_group = get_primary_effective_group(user)
        if not user_group:
            return True, ""
        
        # ✅ Opraveno: GroupOrderLimit + správná pole
        limit_setting = GroupOrderLimit.objects.filter(
            group=user_group,
            druh_jidla=menu_item.druh_jidla
        ).first()
        
        if not limit_setting or limit_setting.max_orders_per_day is None or limit_setting.max_orders_per_day == 0:
            return True, ""
        
        current_orders = OrderItem.objects.filter(
            order__user=user,
            order__datum_vydeje=target_date,
            menu_item__druh_jidla=menu_item.druh_jidla
        ).aggregate(total=Sum('quantity'))['total'] or 0
        
        if current_orders + quantity > limit_setting.max_orders_per_day:
            return False, f"Limit {limit_setting.max_orders_per_day} ks {menu_item.druh_jidla.nazev} za den!"
        return True, ""

    @staticmethod
    def get_price_for_user(user, menu_item, target_date=None, quantity=1, exclude_order_item_id=None):
        """Výpočet ceny s dotací podle skupiny"""
        from dotace.services import vypocet_dotovane_ceny

        return vypocet_dotovane_ceny(
            user,
            menu_item,
            target_date=target_date,
            quantity=quantity,
            exclude_order_item_id=exclude_order_item_id,
        )

    @staticmethod
    def check_user_balance(user, total_price):
        """Kontrola zůstatku a nastavení skupiny"""
        nastaveni = SkupinoveNastaveni.objects.filter(
            skupina__in=get_effective_user_groups(user)
        ).first()
        
        if not nastaveni:
            return True, ""
        
        zustatek = getattr(user, 'aktualni_zustatek', Decimal('0'))
        
        if nastaveni.nutnost_dobit and zustatek < total_price:
            return False, "insufficient_balance"
        
        if nastaveni.cerpani_debit and (zustatek - total_price) < nastaveni.debit_limit:
            return False, "debit_limit_exceeded"
        
        if not nastaveni.cerpani_debit and zustatek < total_price:
            return False, "insufficient_balance"
        
        return True, ""


class UserRFID(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rfid')
    rfid_tag = models.CharField(max_length=32, unique=True)
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.rfid_tag}"
    
class PriceRecalculationLog(models.Model):
    """Audit log pro přepočty cen objednávek"""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Provedeno")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True,
        verbose_name="Provedl"
    )
    date_from = models.DateField(verbose_name="Datum od")
    date_to = models.DateField(verbose_name="Datum do")
    orders_affected = models.PositiveIntegerField(default=0, verbose_name="Ovlivněných objednávek")
    items_affected = models.PositiveIntegerField(default=0, verbose_name="Ovlivněných položek")
    total_price_diff = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        verbose_name="Celkový cenový rozdíl"
    )
    note = models.TextField(blank=True, verbose_name="Poznámka")
    
    class Meta:
        verbose_name = "Přepočet cen log"
        verbose_name_plural = "Přepočet cen log"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Přepočet {self.created_at.strftime('%d.%m.%Y %H:%M')} ({self.items_affected} položek)"


class PriceRecalculationDetail(models.Model):
    """Detail změny ceny jednotlivé objednávky"""
    log = models.ForeignKey(
        PriceRecalculationLog, 
        on_delete=models.CASCADE, 
        related_name='details'
    )
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE)
    old_price = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Stará cena")
    new_price = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Nová cena")
    price_diff = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="Rozdíl")
    
    class Meta:
        verbose_name = "Přepočet cen detail"
        verbose_name_plural = "Přepočet cen detail"
        
        

    def __str__(self):
        return f"{self.order_item}: {self.old_price} → {self.new_price}"
