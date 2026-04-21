from django.utils.html import format_html
from django.contrib import admin
from decimal import Decimal
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.urls import reverse

from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.forms.models import BaseInlineFormSet

import re
from datetime import datetime, date

from .models import (
    Alergen,
    DruhJidla,
    Jidelnicek,
    Jidlo,
    PolozkaJidelnicku,
    vychozi_ikona_druhu_jidla,
    vychozi_ikona_jidla,
)
from dotace.models import DotacniPolitika, DotaceProJidelniskouSkupinu
from sklad.admin import RecepturaPolozkaInline, JidloKomponentaInline
from pokladna.models import PLUPolozka, DPHSkupina, PLUKategorie


# ====== IMPORT TXT LOGIKA ======

MEAL_TYPES = ["SNÍDANĚ", "PŘESNÍDÁVKA", "OBĚD", "SVAČINA", "VEČEŘE", "2.VEČEŘE"]

# Výchozí ceny podle druhu jídla
DEFAULT_PRICES = {
    "Snídaně 1": Decimal("20"),
    "Snídaně 2": Decimal("22"),
    "Přesnídávka": Decimal("15"),
    "Oběd": Decimal("45"),
    "Svačina": Decimal("18"),
    "Večeře": Decimal("35"),
    "Pozdní večeře": Decimal("25"),
}


def _map_meal_to_druh_name(meal_type: str, chod_num: int | None) -> str:
    """
    Mapování typu z TXT na název existujícího DruhJidla.
    """
    if meal_type == "SNÍDANĚ":
        if chod_num == 1:
            return "Snídaně 1"
        elif chod_num == 2:
            return "Snídaně 2"
    if meal_type == "2.VEČEŘE":
        return "Pozdní večeře"

    # PŘESNÍDÁVKA -> Přesnídávka, OBĚD -> Oběd, SVAČINA -> Svačina, VEČEŘE -> Večeře
    return meal_type.capitalize()


def parse_txt_to_structure(text: str) -> dict:
    """
    Vrátí strukturu jídelníčku podle dnů a druhů jídel.
    """
    text = text.split("PŘEJEME VÁM DOBROU CHUŤ")[0]

    period = re.search(r"od:\s*(\d{2}\.\d{2}\.\d{4})\s*do:\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not period:
        raise ValueError("Nelze najít řádek 'od: .. do: ..' v TXT.")

    start_date = datetime.strptime(period.group(1), "%d.%m.%Y").date()
    end_date = datetime.strptime(period.group(2), "%d.%m.%Y").date()

    # Rozdělení po dnech
    day_pattern = r"(Pondělí|Úterý|Středa|Čtvrtek|Pátek|Sobota|Neděle)\s+\d{2}\.\s+leden\s+\d{4}"
    day_blocks = re.split(day_pattern, text)

    menu_by_date: dict[date, dict] = {}
    current_date = start_date
    i = 1
    while i < len(day_blocks):
        day_name = day_blocks[i].strip()
        content = day_blocks[i + 1]
        i += 2

        if current_date > end_date:
            break

        menu_by_date[current_date] = {}

        parts = re.split("(" + "|".join(MEAL_TYPES) + ")", content)
        current_meal = None
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part in MEAL_TYPES:
                current_meal = part
                continue
            if not current_meal:
                continue

            # chody
            chody_raw = re.split(r"chod\s*\d*:", part)
            chod_num = 0
            for chod_content in chody_raw:
                chod_content = chod_content.strip()
                if not chod_content:
                    continue
                chod_num += 1

                druh_name = _map_meal_to_druh_name(
                    current_meal,
                    chod_num if current_meal == "SNÍDANĚ" else None
                )

                items_raw = re.split(r"\s*-\s+", chod_content)
                item_names = []
                alerg_ids_set = set()

                for raw in items_raw:
                    raw = raw.strip()
                    if not raw:
                        continue
                    m = re.match(r"(.+?)(?:\s*\(([\d\sB]+)\))?$", raw)
                    if not m:
                        continue
                    name = m.group(1).strip()
                    alerg_str = m.group(2) or ""
                    if not name:
                        continue
                    item_names.append(name)
                    for a in alerg_str.split():
                        if a:
                            alerg_ids_set.add(a)

                if not item_names:
                    continue

                jidlo_nazev = ", ".join(item_names)
                alerg_ids = sorted(alerg_ids_set, key=lambda x: (x.rstrip("B"), x.endswith("B")))

                menu_by_date[current_date].setdefault(druh_name, [])
                menu_by_date[current_date][druh_name].append((jidlo_nazev, alerg_ids))

        current_date = date.fromordinal(current_date.toordinal() + 1)

    return menu_by_date


def import_menu_structure(menu_by_date: dict, logger=None):
    """
    Importuje strukturu do modelů Jidelnicek / Jidlo / PolozkaJidelnicku.
    """
    druh_cache = {}
    alergen_cache = {}

    def log_info(msg):
        if logger and hasattr(logger, "stdout"):
            logger.stdout.write(msg)

    def log_error(msg):
        if logger and hasattr(logger, "stderr"):
            logger.stderr.write(msg)

    for datum, druhy in menu_by_date.items():
        jidelnicek, created = Jidelnicek.objects.get_or_create(
            platnost_od=datum,
            platnost_do=datum,
            defaults={"ikona": ""},
        )
        if created:
            log_info(f"Vytvořen nový Jídelníček pro {datum}.")

        for druh_nazev, polozky in druhy.items():
            if druh_nazev not in druh_cache:
                try:
                    druh = DruhJidla.objects.get(nazev=druh_nazev)
                except DruhJidla.DoesNotExist:
                    log_error(f"DruhJidla '{druh_nazev}' neexistuje, přeskočeno.")
                    druh_cache[druh_nazev] = None
                else:
                    druh_cache[druh_nazev] = druh
            else:
                druh = druh_cache[druh_nazev]

            if not druh:
                continue

            default_price = DEFAULT_PRICES.get(druh_nazev)

            for jidlo_nazev, alleg_ids in polozky:
                jidlo, created_jidlo = Jidlo.objects.get_or_create(
                    nazev=jidlo_nazev,
                    druh=druh,
                    defaults={"cena": default_price or Decimal("0")},
                )

                if default_price is not None and not created_jidlo:
                    if jidlo.cena != default_price:
                        jidlo.cena = default_price
                        jidlo.save(update_fields=["cena"])

                for al_id in alleg_ids:
                    if al_id not in alergen_cache:
                        try:
                            al = Alergen.objects.get(id=int(al_id))
                        except (Alergen.DoesNotExist, ValueError):
                            log_error(f"Neznámý alergen ID {al_id} pro {jidlo_nazev}, ignoruji.")
                            continue
                        alergen_cache[al_id] = al
                    else:
                        al = alergen_cache[al_id]
                    jidlo.alergeny.add(al)

                PolozkaJidelnicku.objects.get_or_create(
                    jidelnicek=jidelnicek,
                    druh_jidla=druh,
                    jidlo=jidlo,
                )


class TxtImportForm(forms.Form):
    soubor = forms.FileField(label="TXT jídelníček")


class JidloAdminForm(forms.ModelForm):
    class Meta:
        model = Jidlo
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["druh"].help_text = (
            "Katalogový druh jídla. Použije se jako zdroj pravdy při zařazení do jídelníčku "
            "a ovlivňuje ceny po dotacích, limity i viditelnost."
        )

    def clean_druh(self):
        druh = self.cleaned_data.get("druh")
        if druh is None and self.instance.pk and self.instance.polozkajidelnicku_set.exists():
            raise ValidationError(
                "Jídlo už je použité v jídelníčku, proto mu nelze odebrat druh jídla."
            )
        return druh


class PolozkaJidelnickuAdminForm(forms.ModelForm):
    class Meta:
        model = PolozkaJidelnicku
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["jidlo"].widget = forms.Select(attrs={"class": "menu-builder-food-select"})
        druh_jidla = self._resolve_slot_kind()
        jidlo_qs = Jidlo.objects.none()
        if druh_jidla:
            jidlo_qs = Jidlo.objects.filter(druh_id=druh_jidla).order_by("nazev")

        selected_jidlo_id = self._resolve_selected_jidlo_id()
        if selected_jidlo_id:
            selected_qs = Jidlo.objects.filter(pk=selected_jidlo_id)
            jidlo_qs = (jidlo_qs | selected_qs).distinct().order_by("nazev")

        self.fields["jidlo"].queryset = jidlo_qs
        self.fields["jidlo"].widget.choices = self.fields["jidlo"].choices
        self.fields["jidlo"].help_text = (
            "Vyber jídlo z katalogu. Nabídka je omezená jen na jídla odpovídající tomuto druhu."
        )
        self.fields["druh_jidla"].help_text = (
            "Slot jídelníčku. Po prvním uložení se pro jednotlivé druhy předpřipraví řádky automaticky."
        )
        if self.instance.pk and self.instance.jidlo_id and self.instance.jidlo.druh_id:
            self.fields["druh_jidla"].initial = self.instance.jidlo.druh_id
        if self.instance.pk or self.initial.get("druh_jidla"):
            self.fields["druh_jidla"].disabled = True

    def _resolve_slot_kind(self):
        bound_value = self.data.get(self.add_prefix("druh_jidla"))
        if bound_value:
            try:
                return int(bound_value)
            except (TypeError, ValueError):
                return None
        initial_value = self.initial.get("druh_jidla")
        if hasattr(initial_value, "pk"):
            return initial_value.pk
        if initial_value:
            try:
                return int(initial_value)
            except (TypeError, ValueError):
                return None
        if self.instance.pk and self.instance.druh_jidla_id:
            return self.instance.druh_jidla_id
        return None

    def _resolve_selected_jidlo_id(self):
        bound_value = self.data.get(self.add_prefix("jidlo"))
        if bound_value:
            try:
                return int(bound_value)
            except (TypeError, ValueError):
                return None
        if self.instance.pk and self.instance.jidlo_id:
            return self.instance.jidlo_id
        return None

    def clean(self):
        cleaned_data = super().clean()
        jidlo = cleaned_data.get("jidlo")
        druh_jidla = cleaned_data.get("druh_jidla")
        if jidlo and not jidlo.druh_id:
            self.add_error("jidlo", "Vybrané jídlo nemá v katalogu nastavený druh jídla.")
        elif jidlo and druh_jidla and jidlo.druh_id != druh_jidla.id:
            self.add_error(
                "jidlo",
                (
                    f"Vybrané jídlo patří do druhu „{jidlo.druh}“, "
                    f"ale tento slot je určený pro „{druh_jidla}“."
                ),
            )
        return cleaned_data


class PolozkaJidelnickuInlineFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance and instance.pk and not kwargs.get("initial"):
            existing_ids = set(
                instance.polozky.values_list("druh_jidla_id", flat=True)
            )
            kwargs["initial"] = [
                {"druh_jidla": druh.pk}
                for druh in DruhJidla.objects.exclude(pk__in=existing_ids).order_by("poradi", "nazev")
            ]
        super().__init__(*args, **kwargs)


# ====== PŮVODNÍ ADMIN + AUTO‑PLU ======


@admin.register(DruhJidla)
class DruhJidlaAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'poradi', 'visible_for_groups', 'icon_preview')
    list_editable = ('poradi',)
    search_fields = ('nazev',)
    ordering = ('poradi', 'nazev')
    fields = ('nazev', 'poradi', 'ikona', 'viditelne_pro_skupiny')
    filter_horizontal = ('viditelne_pro_skupiny',)
    actions = ("doplnit_ikony_druhu_jidel",)

    def icon_preview(self, obj):
        if hasattr(obj, 'ikona') and obj.ikona:
            return format_html('<i class="{}"></i>', obj.ikona)
        return ""
    icon_preview.short_description = 'Ikona'
    icon_preview.admin_order_field = 'ikona'

    @admin.display(description="Uvidí")
    def visible_for_groups(self, obj):
        groups = list(obj.viditelne_pro_skupiny.values_list("name", flat=True))
        if not groups:
            return "Všichni"
        return ", ".join(groups)

    def save_model(self, request, obj, form, change):
        if not obj.ikona:
            obj.ikona = vychozi_ikona_druhu_jidla(obj.nazev)
        super().save_model(request, obj, form, change)

    @admin.action(description="Doplnit výchozí ikony podle názvu druhu")
    def doplnit_ikony_druhu_jidel(self, request, queryset):
        aktualizovano = 0
        for druh in queryset:
            if druh.ikona:
                continue
            druh.ikona = vychozi_ikona_druhu_jidla(druh.nazev)
            druh.save(update_fields=["ikona"])
            aktualizovano += 1
        self.message_user(request, f"Doplněno ikon u druhů jídel: {aktualizovano}.")


@admin.register(Jidlo)
class JidloAdmin(admin.ModelAdmin):
    form = JidloAdminForm
    list_display = ('nahled', 'nazev', 'druh', 'cena', 'alergeny_list', 'ceny_po_dotacich', 'ma_komponenty')
    search_fields = ('nazev',)
    list_filter = (
        'druh',
        'sk_rybi_pokrm',
        'sk_bezmasy_pokrm',
        'sk_sladky_pokrm',
        'sk_slazeny_napoj',
    )
    filter_horizontal = ('alergeny',)
    inlines = [JidloKomponentaInline, RecepturaPolozkaInline]
    actions = ["vygenerovat_plu_pro_jidla", "doplnit_ikony_jidel"]
    fieldsets = (
        (
            "Základní údaje",
            {
                "fields": (
                    "nazev",
                    "druh",
                    "cena",
                    "alergeny",
                    "ikona",
                    "foto",
                ),
            },
        ),
        (
            "Nutriční údaje",
            {
                "classes": ("collapse",),
                "fields": ("kcal", "bílkoviny", "tuky", "sacharidy"),
            },
        ),
        (
            "Spotřební koš 2025",
            {
                "fields": (
                    "sk_rybi_pokrm",
                    "sk_bezmasy_pokrm",
                    "sk_bile_maso",
                    "sk_cervene_maso",
                    "sk_sladky_pokrm",
                    "sk_jemne_pecivo",
                    "sk_dezert_s_volnym_cukrem",
                    "sk_slazeny_napoj",
                ),
            },
        ),
    )

    @admin.display(description="Náhled")
    def nahled(self, obj):
        if obj.foto:
            return format_html(
                '<img src="{}" style="width:36px;height:36px;object-fit:cover;border-radius:8px;" alt="">',
                obj.foto.url,
            )
        return format_html('<i class="{}" style="font-size:22px;color:#54ae43;"></i>', obj.vychozi_ikona)

    def ma_komponenty(self, obj):
        return obj.komponenty_jidla.exists()
    ma_komponenty.boolean = True
    ma_komponenty.short_description = "Komponenty?"

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        druh_jidla = request.GET.get("druh_jidla")
        if druh_jidla:
            try:
                queryset = queryset.filter(druh_id=int(druh_jidla))
            except (TypeError, ValueError):
                queryset = queryset.none()
        return queryset, use_distinct

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

    @admin.action(description="Doplnit výchozí ikony podle názvu a druhu jídla")
    def doplnit_ikony_jidel(self, request, queryset):
        aktualizovano = 0
        for jidlo in queryset.select_related("druh"):
            if jidlo.ikona:
                continue
            jidlo.ikona = vychozi_ikona_jidla(
                jidlo.nazev,
                jidlo.druh.nazev if jidlo.druh_id else "",
            )
            jidlo.save(update_fields=["ikona"])
            aktualizovano += 1
        self.message_user(request, f"Doplněno ikon u jídel: {aktualizovano}.")

    # ==== AUTO‑PLU ====

    def _ensure_plu_for_jidlo(self, jidlo):
        # DPH 12 %
        dph_12, _ = DPHSkupina.objects.get_or_create(
            sazba=12,
            defaults={"nazev": "Jídlo 12 %"},
        )
        # výchozí kategorie
        kategorie_menu, _ = PLUKategorie.objects.get_or_create(
            nazev="Jídelna"
        )

        plu, created = PLUPolozka.objects.get_or_create(
            jidlo=jidlo,
            defaults={
                "nazev": jidlo.nazev,
                "cena": jidlo.cena,
                "dph_skupina": dph_12,
                "kategorie": kategorie_menu,
                "typ": PLUPolozka.TYP_RECEPTURA,
                "aktivni": True,
            },
        )

        if not created:
            plu.nazev = jidlo.nazev
            plu.cena = jidlo.cena
            plu.dph_skupina = dph_12
            if plu.kategorie is None:
                plu.kategorie = kategorie_menu
            plu.aktivni = True
            plu.save(
                update_fields=["nazev", "cena", "dph_skupina", "kategorie", "aktivni"]
            )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._ensure_plu_for_jidlo(obj)

    @admin.action(description="Vygenerovat / aktualizovat PLU pro vybraná jídla")
    def vygenerovat_plu_pro_jidla(self, request, queryset):
        for jidlo in queryset:
            self._ensure_plu_for_jidlo(jidlo)


class PolozkaJidelnickuInline(admin.StackedInline):
    model = PolozkaJidelnicku
    form = PolozkaJidelnickuAdminForm
    formset = PolozkaJidelnickuInlineFormSet
    extra = 0
    fields = ("druh_jidla", "jidlo", "menu_item_summary")
    readonly_fields = ("menu_item_summary",)
    classes = ("menu-builder-inline",)

    def get_extra(self, request, obj=None, **kwargs):
        if not obj or not obj.pk:
            return 0
        existing_ids = set(obj.polozky.values_list("druh_jidla_id", flat=True))
        return DruhJidla.objects.exclude(pk__in=existing_ids).count()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "druh_jidla":
            kwargs["queryset"] = DruhJidla.objects.order_by("poradi", "nazev")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Souhrn vybraného jídla")
    def menu_item_summary(self, obj):
        if not obj or not getattr(obj, "jidlo_id", None):
            return format_html(
                '<div class="menu-builder-empty">Vyber jídlo a souhrn ceny, alergenů a viditelnosti se doplní automaticky.</div>'
            )

        jidlo = obj.jidlo
        druh = jidlo.druh.nazev if jidlo.druh_id else "Bez druhu"
        alergeny = ", ".join(jidlo.alergeny.values_list("nazev", flat=True)) or "Bez alergenů"
        visible_groups = ", ".join(
            jidlo.druh.viditelne_pro_skupiny.values_list("name", flat=True)
        ) if jidlo.druh_id and jidlo.druh.viditelne_pro_skupiny.exists() else "Všichni"
        return format_html(
            '<div class="menu-builder-summary" data-menu-builder-summary>'
            '<span class="menu-builder-pill kind">Druh: {}</span>'
            '<span class="menu-builder-pill price">Cena: {} Kč</span>'
            '<span class="menu-builder-pill allergens">Alergeny: {}</span>'
            '<span class="menu-builder-pill groups">Uvidí: {}</span>'
            '</div>',
            druh,
            f"{jidlo.cena:.2f}",
            alergeny,
            visible_groups,
        )


@admin.register(Jidelnicek)
class JidelnicekAdmin(admin.ModelAdmin):
    change_form_template = "admin/jidelnicek/jidelnicek/change_form.html"
    list_display = ('platnost_od', 'platnost_do', 'obsah_jidelnicku')
    inlines = [PolozkaJidelnickuInline]

    class Media:
        css = {"all": ("jidelnicek/css/menu_builder_admin.css",)}
        js = ("jidelnicek/js/menu_builder_admin.js",)

    @admin.display(description='Obsah jídelníčku')
    def obsah_jidelnicku(self, obj):
        polozky = obj.polozky.select_related('druh_jidla', 'jidlo').all()
        if not polozky:
            return "-"
        rows = ""
        for p in polozky:
            ikonovy_html = ""
            if p.druh_jidla.ikona:
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

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path(
                "jidlo-meta/<int:jidlo_id>/",
                self.admin_site.admin_view(self.jidlo_meta_api),
                name="jidelnicek_jidlo_meta",
            ),
            path(
                "import-txt/",
                self.admin_site.admin_view(self.import_txt_view),
                name="jidelnicek_import_txt",
            ),
        ]
        return my_urls + urls

    def render_change_form(self, request, context, *args, **kwargs):
        context["jidlo_meta_url_template"] = reverse(
            "admin:jidelnicek_jidlo_meta", args=[0]
        )
        return super().render_change_form(request, context, *args, **kwargs)

    def jidlo_meta_api(self, request, jidlo_id):
        try:
            jidlo = Jidlo.objects.select_related("druh").prefetch_related("alergeny", "druh__viditelne_pro_skupiny").get(pk=jidlo_id)
        except Jidlo.DoesNotExist:
            return JsonResponse({"error": "not_found"}, status=404)

        if not jidlo.druh_id:
            return JsonResponse(
                {
                    "id": jidlo.pk,
                    "nazev": jidlo.nazev,
                    "druh_id": None,
                    "druh": "",
                    "cena": f"{jidlo.cena:.2f}",
                    "alergeny": list(jidlo.alergeny.values_list("nazev", flat=True)),
                    "visible_groups": [],
                }
            )

        return JsonResponse(
            {
                "id": jidlo.pk,
                "nazev": jidlo.nazev,
                "druh_id": jidlo.druh_id,
                "druh": jidlo.druh.nazev,
                "cena": f"{jidlo.cena:.2f}",
                "alergeny": list(jidlo.alergeny.values_list("nazev", flat=True)),
                "visible_groups": list(jidlo.druh.viditelne_pro_skupiny.values_list("name", flat=True)),
            }
        )

    def import_txt_view(self, request):
        if request.method == "POST":
            form = TxtImportForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data["soubor"]
                content = f.read().decode("utf-8", errors="ignore")
                try:
                    menu_by_date = parse_txt_to_structure(content)
                    import_menu_structure(menu_by_date, logger=None)
                except Exception as e:
                    messages.error(request, f"Chyba při importu: {e}")
                else:
                    messages.success(request, "TXT jídelníček byl naimportován (Jídla + Jídelníčky).")
                return redirect("admin:jidelnicek_jidelnicek_changelist")
        else:
            form = TxtImportForm()

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Import jídelníčku z TXT",
        }
        return render(request, "admin/jidelnicek_import_txt.html", context)


@admin.register(PolozkaJidelnicku)
class PolozkaJidelnickuAdmin(admin.ModelAdmin):
    form = PolozkaJidelnickuAdminForm
    list_display = ("jidelnicek", "druh_jidla", "jidlo")
    list_filter = ("jidelnicek", "druh_jidla")
    search_fields = ("jidlo__nazev",)
