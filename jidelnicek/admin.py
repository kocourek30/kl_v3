from django.utils.html import format_html
from django.contrib import admin
from decimal import Decimal
from django.db.models import Sum

from .models import Alergen, Jidlo, DruhJidla, Jidelnicek, PolozkaJidelnicku
from dotace.models import DotacniPolitika, DotaceProJidelniskouSkupinu
from django.utils.html import format_html
from sklad.admin import RecepturaPolozkaInline

@admin.register(DruhJidla)
class DruhJidlaAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'icon_preview')
    search_fields = ('nazev',)

    def icon_preview(self, obj):
        if hasattr(obj, 'ikona') and obj.ikona:
            return format_html('<i class="{}"></i>', obj.ikona)
        return ""
    icon_preview.short_description = 'Ikona'
    icon_preview.admin_order_field = 'ikona'

    def save_model(self, request, obj, form, change):
        if not obj.ikona:
            obj.ikona = 'fas fa-basketball-ball'  # výchozí Font Awesome ikona
        super().save_model(request, obj, form, change)


@admin.register(Jidlo)
class JidloAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'cena', 'alergeny_list', 'ceny_po_dotacich')
    search_fields = ('nazev',)
    filter_horizontal = ('alergeny',)
    inlines = [RecepturaPolozkaInline]

    def alergeny_list(self, obj):
        return ", ".join([a.nazev for a in obj.alergeny.all()])
    alergeny_list.short_description = 'Alergeny'

    def ceny_po_dotacich(self, obj):
        ceny = []
        politiky = DotacniPolitika.objects.select_related('skupina').all()

        for politika in politiky:
            try:
                prepis = DotaceProJidelniskouSkupinu.objects.get(
                    dotacni_politika=politika,
                    jidelniskova_skupina=obj.druh
                )
                procento = (prepis.procento if prepis.procento is not None else politika.procento) / 100
                castka = prepis.castka if prepis.castka is not None else politika.castka
            except DotaceProJidelniskouSkupinu.DoesNotExist:
                procento = politika.procento / 100
                castka = politika.castka

            cena_sleva = obj.cena * (1 - procento) - castka
            if cena_sleva < 0:
                cena_sleva = 0
            ceny.append({
                'skupina': politika.skupina.name,
                'cena': f"{cena_sleva:.2f} Kč"
            })

        rows_html = "".join(
            f"<tr>"
            f"<td style='padding: 2px 6px; border: 1px solid #ddd; font-size: 11px;'>{c['skupina']}</td>"
            f"<td style='padding: 2px 6px; border: 1px solid #ddd; text-align: right; font-weight: 600; font-size: 11px;'>{c['cena']}</td>"
            f"</tr>"
            for c in ceny
        )
        table_html = f"""
        <table style='border-collapse: collapse; width: 100%; border: 1px solid #ccc; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;'>
            <thead>
                <tr>
                    <th style='border: 1px solid #ccc; font-size: 11px; padding: 4px 6px; background: #f4f6f9;'>Skupina</th>
                    <th style='border: 1px solid #ccc; font-size: 11px; padding: 4px 6px; background: #f4f6f9; text-align: right;'>Cena po dotaci</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        """
        return format_html(table_html)

    ceny_po_dotacich.short_description = "Ceny po dotacích"



class PolozkaJidelnickuInline(admin.TabularInline):
    model = PolozkaJidelnicku
    extra = 1  # kolik prázdných formulářů na přidání položek se zobrazí

@admin.register(Jidelnicek)
class JidelnicekAdmin(admin.ModelAdmin):
    list_display = ('platnost_od', 'platnost_do', 'obsah_jidelnicku')
    inlines = [PolozkaJidelnickuInline]
    # Odstraněno filter_horizontal = ['jidla'], protože pole jidla už není součástí Jidelnicek

    @admin.display(description='Obsah jídelníčku')
    def obsah_jidelnicku(self, obj):
        polozky = obj.polozky.select_related('druh_jidla', 'jidlo').all()
        if not polozky:
            return "-"
        rows = ""
        for p in polozky:
            ikonovy_html = ""
            if p.druh_jidla.ikona:
                # Ikona je HTML <i> se třídou z pole ikona
                ikonovy_html = f'<i class="{p.druh_jidla.ikona}" style="margin-right:5px;"></i>'
            rows += f"<tr><td>{ikonovy_html}{p.druh_jidla}</td><td>{p.jidlo}</td></tr>"

        table_html = f"""
        <table style="border-collapse: collapse; border: 1px solid #ddd;">
            <thead>
                <tr>
                    <th style="border: 1px solid #ddd; padding: 2px 5px;">Druh jídla</th>
                    <th style="border: 1px solid #ddd; padding: 2px 5px;">Jídlo</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        """
        return format_html(table_html)
   

    


@admin.register(Alergen)
class AlergenAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'icon_preview')
    search_fields = ('nazev',)

    def icon_preview(self, obj):
        if hasattr(obj, 'ikona') and obj.ikona:
            return format_html('<i class="{}"></i>', obj.ikona)
        return ""
    icon_preview.short_description = 'Ikona'
    icon_preview.admin_order_field = 'ikona'

    def save_model(self, request, obj, form, change):
        if not obj.ikona:
            obj.ikona = 'fas fa-exclamation-triangle'  # výchozí Font Awesome ikona
        super().save_model(request, obj, form, change)

