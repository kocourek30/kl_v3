from django.contrib import admin
from .models import (
    DotacniPolitika,
    DotaceProJidelniskouSkupinu,
    Dotace,
    SkupinoveNastaveni,
)


@admin.register(Dotace)
class DotaceAdmin(admin.ModelAdmin):
    # schová celý modul "Dotace" z levého menu
    def has_module_permission(self, request):
        return False

    # volitelně: zakáže i přístup přímo na URL
    def has_view_permission(self, request, obj=None):
        return False


class DotaceProJidelniskouSkupinuInline(admin.TabularInline):
    model = DotaceProJidelniskouSkupinu
    extra = 1
    autocomplete_fields = ["jidelniskova_skupina"]
    fields = ("jidelniskova_skupina", "procento", "castka")
    verbose_name = "Dotace pro skupinu jídla"
    verbose_name_plural = "Dotace pro skupiny jídel"


@admin.register(DotacniPolitika)
class DotacniPolitikaAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            None,
            {
                "fields": ("skupina",),
                "description": (
                    'Zvol skupinu uživatelů. Detailní dotace pro druhy jídel '
                    'nastav dole v sekci "Dotace pro skupiny jídel".'
                ),
            },
        ),
    )
    inlines = [DotaceProJidelniskouSkupinuInline]
    list_display = ("skupina",)


@admin.register(SkupinoveNastaveni)
class SkupinoveNastaveniAdmin(admin.ModelAdmin):
    list_display = ("skupina", "cerpani_debit", "debit_limit", "nutnost_dobit")
    fields = ("skupina", "cerpani_debit", "debit_limit", "nutnost_dobit")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return form
