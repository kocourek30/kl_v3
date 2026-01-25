# vydej/admin.py
from django.contrib import admin
from .models import VydejSettings


@admin.register(VydejSettings)
class VydejSettingsAdmin(admin.ModelAdmin):
    list_display = ["timeout_seconds"]
