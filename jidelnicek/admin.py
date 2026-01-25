from django.utils.html import format_html
from django.contrib import admin
from decimal import Decimal
from django.db.models import Sum

from .models import Alergen, Jidlo, DruhJidla, Jidelnicek, PolozkaJidelnicku
from dotace.models import DotacniPolitika, DotaceProJidelniskouSkupinu
from sklad.admin import RecepturaPolozkaInline

from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages

# ====== IMPORT TXT LOGIKA (z management commandu zkráceně) ======
import re
from datetime import datetime, date

from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages

from .models import Alergen, Jidlo, DruhJidla, Jidelnicek, PolozkaJidelnicku

import re
from datetime import datetime, date
from jidelnicek.models import Jidlo, Jidelnicek, PolozkaJidelnicku, DruhJidla, Alergen

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
    SNÍDANĚ + chod1 -> 'Snídaně 1'
    SNÍDANĚ + chod2 -> 'Snídaně 2'
    2.VEČEŘE        -> 'Pozdní večeře'
    ostatní         -> titulek s první velkou (aby seděl na tvoje názvy).
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
    Vrátí:
    {
      date(2026,1,26): {
        "Snídaně 1": [("Housky, Máslo, Džem, Čaj, Kakao", ["1","6","7"])],
        "Snídaně 2": [("Chléb, Pomazánka z paštiky, Zelenina, Čaj", ["1","7"])],
        "Oběd":      [("Brokolicová polévka, Špagety po boloňsku, Ovoce, Voda se sirupem", [...])],
        ...
      },
      ...
    }

    Tj. pro každý chod je jen jedno „jídlo“, do kterého jsou slité všechny položky chodu.
    """
    # odseknout konec s přáním
    text = text.split("PŘEJEME VÁM DOBROU CHUŤ")[0]

    period = re.search(r"od:\s*(\d{2}\.\d{2}\.\d{4})\s*do:\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not period:
        raise ValueError("Nelze najít řádek 'od: .. do: ..' v TXT.")

    start_date = datetime.strptime(period.group(1), "%d.%m.%Y").date()
    end_date = datetime.strptime(period.group(2), "%d.%m.%Y").date()

    # Rozdělení po dnech – zdroj má "Pondělí 26. leden 2026" bez odřádkování
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

            # chody: SNÍDANĚchod 1: ... chod 2: ...
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

                # položky v rámci jednoho chodu: " - Housky (1)- Máslo (7)- Džem- Čaj- Kakao (6 7)"
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

                # název jídla = všechny položky chodu spojené čárkou
                jidlo_nazev = ", ".join(item_names)
                alerg_ids = sorted(alerg_ids_set, key=lambda x: (x.rstrip("B"), x.endswith("B")))

                menu_by_date[current_date].setdefault(druh_name, [])
                menu_by_date[current_date][druh_name].append((jidlo_nazev, alerg_ids))

        current_date = date.fromordinal(current_date.toordinal() + 1)

    return menu_by_date


def import_menu_structure(menu_by_date: dict, logger=None):
    """
    - Pro každé datum vytvoří / najde Jídelníček (platnost_od=platnost_do=datum).
    - DruhJidla NEzakládá – jen páruje na existující názvy.
      Pokud druh neexistuje, řádek přeskočí.
    - Jídlo se zakládá (pokud neexistuje pro daný druh).
    - Cena se nastaví podle DEFAULT_PRICES podle názvu druhu.
    - Alergeny se přiřazují podle ID.
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
            # DruhJidla – POUZE EXISTUJÍCÍ, jinak přeskočit
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

                # Pokud chceš při každém importu přepsat cenu podle aktuálního nastavení:
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


# ====== DOTEK PŮVODNÍHO ADMINA ======

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

    # URL pro import v adminu
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
                    import_menu_structure(menu_by_date, logger=None)  # logger klidně None
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
            obj.ikona = 'fas fa-exclamation-triangle'
        super().save_model(request, obj, form, change)
