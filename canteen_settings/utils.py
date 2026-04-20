from datetime import timedelta, date
import logging

from django.utils import timezone

from .models import OrderClosingTime, OperatingDays, OperatingExceptions


logger = logging.getLogger(__name__)


def is_operating_day(check_date):
    """
    Kontrola, zda je daný datum provozní den.
    Priorita: Výjimky > Standardní provozní dny
    """
    # 1. Kontrola výjimek (má přednost)
    exception = OperatingExceptions.objects.filter(date=check_date).first()
    if exception:
        return exception.exception_type == 'open'
    
    # 2. Kontrola standardních provozních dnů
    day_of_week = check_date.weekday()
    operating_day = OperatingDays.objects.filter(day_of_week=day_of_week).first()
    
    if operating_day:
        return operating_day.is_operating
    
    # 3. Výchozí: Po-Pá jsou provozní
    return day_of_week < 5


def get_order_closing_datetime(target_date):
    """
    Vrátí datum a čas uzávěrky pro daný cílový datum vydeje.
    Přeskakuje neprovozní dny a respektuje výjimky.
    """
    try:
        settings = OrderClosingTime.objects.filter(je_aktivni=True).first()
        if not settings:
            return None
        
        # Kontrola, zda je cílový den vůbec provozní
        if not is_operating_day(target_date):
            return None
        
        # Spočítej uzávěrku s přeskakováním neprovozních dnů
        closing_date = target_date
        days_to_subtract = settings.advance_days
        
        while days_to_subtract > 0:
            closing_date -= timedelta(days=1)
            
            # Počítej pouze provozní dny
            if is_operating_day(closing_date):
                days_to_subtract -= 1
        
        # Kombinuj datum a čas
        closing_datetime = timezone.datetime.combine(
            closing_date, 
            settings.closing_time
        )
        closing_datetime = timezone.make_aware(
            closing_datetime, 
            timezone.get_current_timezone()
        )
        
        return closing_datetime
        
    except Exception:
        logger.exception("Chyba při výpočtu uzávěrky objednávek.")
        return None


def is_ordering_allowed(target_date):
    """Kontrola, zda je pro daný datum povoleno objednávání"""
    closing_datetime = get_order_closing_datetime(target_date)
    
    if not closing_datetime:
        return False
    
    return timezone.now() < closing_datetime
