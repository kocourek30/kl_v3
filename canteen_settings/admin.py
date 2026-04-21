# canteen_settings/admin.py

from django.contrib import admin
from django.utils.html import format_html
from .models import (
    CanteenContact, 
    OrderClosingTime, 
    GroupOrderLimit, 
    MealPickupTime,
    OperatingDays,
    OperatingExceptions
)


@admin.register(CanteenContact)
class CanteenContactAdmin(admin.ModelAdmin):
    list_display = ('contact_name', 'contact_email', 'contact_phone', 'address')


@admin.register(OrderClosingTime)
class OrderClosingTimeAdmin(admin.ModelAdmin):
    list_display = (
        'je_aktivni',
        'get_druh',
        'advance_days',
        'closing_time',
        'cancel_days',
        'cancel_until_time',
    )
    list_filter = ('je_aktivni', 'druh_jidla')
    search_fields = ('druh_jidla__nazev',)

    def get_druh(self, obj):
        return obj.druh_jidla.nazev if obj.druh_jidla else "Všechny druhy"
    get_druh.short_description = "Druh jídla"



@admin.register(GroupOrderLimit)
class GroupOrderLimitAdmin(admin.ModelAdmin):
    list_display = ['group', 'druh_jidla', 'max_orders_per_day_display']
    list_filter = ['group', 'druh_jidla']
    
    def max_orders_per_day_display(self, obj):
        return "neomezeno" if obj.max_orders_per_day == 0 else f"{obj.max_orders_per_day} ks"
    max_orders_per_day_display.short_description = "Limit za den"


@admin.register(MealPickupTime)
class MealPickupTimeAdmin(admin.ModelAdmin):
    list_display = ['druh_jidla', 'pickup_from', 'pickup_to']
    list_filter = ['druh_jidla']
    list_editable = ['pickup_from', 'pickup_to']
    ordering = ['druh_jidla__poradi', 'druh_jidla__nazev', 'pickup_from']


@admin.register(OperatingDays)
class OperatingDaysAdmin(admin.ModelAdmin):
    list_display = ['get_day_name', 'is_operating', 'status_icon']
    list_editable = ['is_operating']
    ordering = ['day_of_week']
    
    def get_day_name(self, obj):
        return obj.get_day_of_week_display()
    get_day_name.short_description = 'Den v týdnu'
    
    def status_icon(self, obj):
        if obj.is_operating:
            return format_html('<span style="color: green; font-size: 16px;">✅ Provoz</span>')
        return format_html('<span style="color: red; font-size: 16px;">❌ Zavřeno</span>')
    status_icon.short_description = 'Stav'
    
    def has_add_permission(self, request):
        # Povolit přidání pouze pokud není 7 dní
        return OperatingDays.objects.count() < 7
    
    def has_delete_permission(self, request, obj=None):
        return True


@admin.register(OperatingExceptions)
class OperatingExceptionsAdmin(admin.ModelAdmin):
    list_display = ['date', 'get_formatted_date', 'exception_type', 'reason', 'status_icon']
    list_filter = ['exception_type', 'date']
    list_editable = ['exception_type', 'reason']
    date_hierarchy = 'date'
    ordering = ['-date']
    
    def get_formatted_date(self, obj):
        day_name = obj.date.strftime('%A')
        day_names_cz = {
            'Monday': 'Pondělí',
            'Tuesday': 'Úterý',
            'Wednesday': 'Středa',
            'Thursday': 'Čtvrtek',
            'Friday': 'Pátek',
            'Saturday': 'Sobota',
            'Sunday': 'Neděle'
        }
        return f"{day_names_cz.get(day_name, day_name)}"
    get_formatted_date.short_description = 'Den v týdnu'
    
    def status_icon(self, obj):
        if obj.exception_type == 'open':
            return format_html('<span style="color: green; font-size: 16px;">✅ Otevřeno</span>')
        return format_html('<span style="color: red; font-size: 16px;">❌ Zavřeno</span>')
    status_icon.short_description = 'Status'
