# pokladna/admin.py

from django.contrib import admin
from django.utils.html import format_html
from .models import (
    DPHSkupina,
    PLUKategorie,
    PLUPolozka,
    Pokladna,
    PokladniDoklad,
    PokladniPolozka,
    PokladnaTile,
)


@admin.register(DPHSkupina)
class DPHSkupinaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "sazba")
    search_fields = ("nazev",)


@admin.register(PLUKategorie)
class PLUKategorieAdmin(admin.ModelAdmin):
    list_display = ("nazev",)
    search_fields = ("nazev",)


@admin.register(PLUPolozka)
class PLUPolozkaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "cena", "dph_skupina", "kategorie", "aktivni")
    list_filter = ("aktivni", "kategorie", "dph_skupina")
    search_fields = ("nazev",)


class PokladniPolozkaInline(admin.TabularInline):
    model = PokladniPolozka
    extra = 1


@admin.register(Pokladna)
class PokladnaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "aktivni")
    list_filter = ("aktivni",)
    search_fields = ("nazev",)


@admin.register(PokladniDoklad)
class PokladniDokladAdmin(admin.ModelAdmin):
    list_display = ("id", "datum", "pokladna", "obsluha", "celkem_s_dph")
    list_filter = ("pokladna", "datum")
    search_fields = ("id", "obsluha__username")
    inlines = [PokladniPolozkaInline]
    readonly_fields = ("celkem_bez_dph", "celkem_dph", "celkem_s_dph")


@admin.register(PokladnaTile)
class PokladnaTileAdmin(admin.ModelAdmin):
    list_display = ("pokladna", "plu", "nazev", "tile_preview", "aktivni", "poradi")
    list_filter = ("pokladna", "aktivni")
    search_fields = ("nazev", "plu__nazev")
    list_editable = ("aktivni", "poradi")
    ordering = ("pokladna", "poradi", "id")

    fieldsets = (
        (None, {
            "fields": ("pokladna", "plu", "nazev", "aktivni", "poradi")
        }),
        ("Vzhled", {
            "fields": (
                ("barva_pozadi", "barva_pozadi_custom", "barva_textu"),
                ("font_bold", "font_size_px", "font_family"),
                "ikona",
            )
        }),
    )

    def tile_preview(self, obj):
        bg = obj.effective_bg_color
        fg = obj.barva_textu or "#ffffff"
        return format_html(
            '<span style="display:inline-block;padding:.25rem .6rem;border-radius:6px;'
            'background:{bg};color:{fg};font-size:0.8rem;">{text}</span>',
            bg=bg,
            fg=fg,
            text=obj.nazev or obj.plu.nazev,
        )

    tile_preview.short_description = "Náhled"
