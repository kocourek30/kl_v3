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
    fields = ("jidelniskova_skupina", "procento", "castka", "denni_limit", "mesicni_limit")
    verbose_name = "Dotace podle druhu jídla"
    verbose_name_plural = "Dotace podle druhů jídel"


@admin.register(DotacniPolitika)
class DotacniPolitikaAdmin(admin.ModelAdmin):
    fieldsets = (
        (
            "Komu pravidlo platí",
            {
                "fields": ("skupina",),
                "description": (
                    "Dotační politika se váže na uživatelskou skupinu. "
                    "Uživatel získá první dotační politiku podle svých skupin."
                ),
            },
        ),
        (
            "Výše dotace",
            {
                "fields": ("procento", "castka"),
                "description": (
                    "Nastav výchozí dotaci na jednu porci. Procentní a pevná "
                    "částka se sčítají, výsledná dotace ale nikdy nesníží cenu pod 0 Kč."
                ),
            },
        ),
        (
            "Bezpečnostní limity",
            {
                "fields": (
                    "denni_limit",
                    "mesicni_limit",
                    "denni_limit_castka",
                    "mesicni_limit_castka",
                ),
                "description": (
                    "Limity chrání rozpočet. Hodnota 0 znamená bez limitu. "
                    "Početní limity počítají dotované porce, finanční limity "
                    "počítají skutečnou poskytnutou dotaci v Kč."
                ),
            },
        ),
    )
    inlines = [DotaceProJidelniskouSkupinuInline]
    list_display = (
        "skupina",
        "procento",
        "castka",
        "denni_limit",
        "mesicni_limit",
        "denni_limit_castka",
        "mesicni_limit_castka",
    )
    list_filter = ("skupina",)


@admin.register(SkupinoveNastaveni)
class SkupinoveNastaveniAdmin(admin.ModelAdmin):
    list_display = ("skupina", "cerpani_debit", "debit_limit", "nutnost_dobit")
    fields = ("skupina", "cerpani_debit", "debit_limit", "nutnost_dobit")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return form
