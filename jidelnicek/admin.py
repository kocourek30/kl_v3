from django.utils.html import format_html
from django.contrib import admin
from decimal import Decimal
from django.db.models import Sum

from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages

import re
from datetime import datetime, date

from .models import Alergen, Jidlo, DruhJidla, Jidelnicek, PolozkaJidelnicku
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


# ====== PŮVODNÍ ADMIN + AUTO‑PLU ======


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
            obj.ikona = 'fas fa-basketball-ball'
        super().save_model(request, obj, form, change)


@admin.register(Jidlo)
class JidloAdmin(admin.ModelAdmin):
    list_display = ('nazev', 'druh', 'cena', 'alergeny_list', 'ceny_po_dotacich', 'ma_komponenty')
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
    actions = ["vygenerovat_plu_pro_jidla"]
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

    def ma_komponenty(self, obj):
        return obj.komponenty_jidla.exists()
    ma_komponenty.boolean = True
    ma_komponenty.short_description = "Komponenty?"

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


class PolozkaJidelnickuInline(admin.TabularInline):
    model = PolozkaJidelnicku
    extra = 1


@admin.register(Jidelnicek)
class JidelnicekAdmin(admin.ModelAdmin):
    list_display = ('platnost_od', 'platnost_do', 'obsah_jidelnicku')
    inlines = [PolozkaJidelnickuInline]

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
                "import-txt/",
                self.admin_site.admin_view(self.import_txt_view),
                name="jidelnicek_import_txt",
            ),
        ]
        return my_urls + urls

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
    list_display = ("jidelnicek", "druh_jidla", "jidlo")
    list_filter = ("jidelnicek", "druh_jidla")
    search_fields = ("jidlo__nazev",)
