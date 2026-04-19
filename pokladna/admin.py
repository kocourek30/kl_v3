# pokladna/admin.py

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from .services import stornuj_doklad, uzavri_denni_uzaverku
from .models import (
    DPHSkupina,
    PLUKategorie,
    PLUPolozka,
    Pokladna,
    PokladniDoklad,
    PokladniPolozka,
    PokladnaTile,
    PokladniUzaverka,
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
    list_display = ("nazev", "typ", "cena", "dph_skupina", "kategorie", "surovina", "jidlo", "aktivni")
    list_filter = ("aktivni", "typ", "kategorie", "dph_skupina")
    search_fields = ("nazev", "surovina__nazev", "jidlo__nazev")


class PokladniPolozkaInline(admin.TabularInline):
    model = PokladniPolozka
    extra = 0
    readonly_fields = (
        "plu",
        "nazev_snapshot",
        "mnozstvi",
        "jednotka_text",
        "cena_jednotkova",
        "dph_sazba",
        "zaklad_dph",
        "castka_dph",
        "castka_celkem",
        "skladovy_pohyb",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Pokladna)
class PokladnaAdmin(admin.ModelAdmin):
    list_display = ("nazev", "aktivni")
    list_filter = ("aktivni",)
    search_fields = ("nazev",)
    actions = ("uzavrit_dnesni_trzbu",)

    @admin.action(description="Vytvořit dnešní denní uzávěrku")
    def uzavrit_dnesni_trzbu(self, request, queryset):
        from django.utils import timezone

        for pokladna in queryset:
            uzaverka = uzavri_denni_uzaverku(pokladna, timezone.localdate(), user=request.user)
            self.message_user(
                request,
                f"Uzávěrka pro {pokladna} byla vytvořena: {uzaverka.celkem_trzba} Kč.",
                level=messages.SUCCESS,
            )


@admin.register(PokladniDoklad)
class PokladniDokladAdmin(admin.ModelAdmin):
    list_display = (
        "cislo_dokladu",
        "datum",
        "pokladna",
        "stav",
        "zpusob_platby",
        "zakaznik",
        "obsluha",
        "celkem_s_dph",
    )
    list_filter = ("stav", "zpusob_platby", "pokladna", "datum")
    search_fields = ("id", "cislo_dokladu", "obsluha__username", "zakaznik__username")
    inlines = [PokladniPolozkaInline]
    readonly_fields = (
        "cislo_dokladu",
        "stav",
        "datum",
        "uzavren_at",
        "uzavrel",
        "stornovano_at",
        "stornoval",
        "konto_pohyb",
        "uzaverka",
        "celkem_bez_dph",
        "celkem_dph",
        "celkem_s_dph",
    )
    actions = ("stornovat_doklady",)

    fieldsets = (
        (None, {
            "fields": (
                ("pokladna", "cislo_dokladu", "stav"),
                ("datum", "obsluha", "zakaznik"),
                "zpusob_platby",
            )
        }),
        ("Uzavření a storno", {
            "fields": (
                ("uzavren_at", "uzavrel"),
                ("stornovano_at", "stornoval"),
                "storno_duvod",
                "konto_pohyb",
                "uzaverka",
            )
        }),
        ("Součty", {
            "fields": ("celkem_bez_dph", "celkem_dph", "celkem_s_dph")
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj and not obj.je_rozpracovany:
            return tuple(f.name for f in self.model._meta.fields)
        return self.readonly_fields

    def has_delete_permission(self, request, obj=None):
        return bool(obj and obj.je_rozpracovany)

    @admin.action(description="Stornovat vybrané uzavřené doklady")
    def stornovat_doklady(self, request, queryset):
        ok = 0
        for doklad in queryset:
            try:
                stornuj_doklad(doklad, user=request.user, duvod="Storno z administrace")
                ok += 1
            except ValidationError as exc:
                self.message_user(request, f"{doklad}: {exc.messages[0]}", level=messages.ERROR)
        if ok:
            self.message_user(request, f"Stornováno dokladů: {ok}.", level=messages.SUCCESS)


@admin.register(PokladniUzaverka)
class PokladniUzaverkaAdmin(admin.ModelAdmin):
    list_display = (
        "datum",
        "pokladna",
        "pocet_dokladu",
        "hotovost",
        "karta",
        "konto",
        "storna",
        "rozdil_hotovosti",
        "uzavrel",
    )
    list_filter = ("pokladna", "datum")
    search_fields = ("pokladna__nazev",)
    readonly_fields = (
        "pocet_dokladu",
        "pocet_storen",
        "hotovost",
        "karta",
        "konto",
        "storna",
        "rozdil_hotovosti",
    )
    actions = ("prepocitat_uzaverky",)

    @admin.action(description="Přepočítat vybrané uzávěrky")
    def prepocitat_uzaverky(self, request, queryset):
        for uzaverka in queryset:
            uzavri_denni_uzaverku(
                uzaverka.pokladna,
                uzaverka.datum,
                user=request.user,
                hotovost_spoctena=uzaverka.hotovost_spoctena,
                poznamka=uzaverka.poznamka,
            )
        self.message_user(request, "Vybrané uzávěrky byly přepočítány.", level=messages.SUCCESS)


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
