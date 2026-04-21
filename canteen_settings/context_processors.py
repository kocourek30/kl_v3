from django.utils import timezone
from .models import (
    CanteenContact, MealPickupTime, OperatingDays, OperatingExceptions
)

def footer_info(request):  # ← Toto musí být přesně takto!
    today = timezone.now().date()
    return {
        'canteen_contact': CanteenContact.objects.first(),
        'meal_pickup_times': MealPickupTime.objects.select_related('druh_jidla').order_by(
            'druh_jidla__poradi',
            'druh_jidla__nazev',
        ),
        'provozni_dny': OperatingDays.objects.filter(is_operating=True),
        'exceptions': OperatingExceptions.objects.filter(
            date__gte=today
        ).order_by('date')[:3]
    }

# ← Smaz 'canteen_info' pokud existuje!
