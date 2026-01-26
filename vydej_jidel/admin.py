from django.contrib import admin
from .models import VydejSettings  # musí sedět jméno i modul

@admin.register(VydejSettings)
class VydejSettingsAdmin(admin.ModelAdmin):
    list_display = ["timeout_seconds"]
