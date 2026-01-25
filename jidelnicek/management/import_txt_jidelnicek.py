# jidelnicek/management/commands/import_txt_jidelnicek.py
import re
from datetime import datetime, date

from django.core.management.base import BaseCommand

from jidelnicek.models import Jidlo, Jidelnicek, PolozkaJidelnicku, DruhJidla, Alergen


MEAL_TYPES = ["SNÍDANĚ", "PŘESNÍDÁVKA", "OBĚD", "SVAČINA", "VEČEŘE", "2.VEČEŘE"]


def _map_meal_to_druh_name(meal_type: str, chod_num: int | None) -> str:
    if meal_type == "SNÍDANĚ":
        if chod_num == 1:
            return "SNÍDANĚ 1"
        elif chod_num == 2:
            return "SNÍDANĚ 2"
    if meal_type == "2.VEČEŘE":
        return "Pozdní večeře"
    return meal_type  # PŘESNÍDÁVKA, OBĚD, SVAČINA, VEČEŘE


def parse_txt_to_structure(text: str) -> dict:
    """
    Vrátí:
    {
      date(2026,1,26): {
        "SNÍDANĚ 1": [("Housky", ["1"]), ...],
        "SNÍDANĚ 2": [...],
        "OBĚD": [...],
        "Pozdní večeře": [...],
        ...
      },
      ...
    }
    """
    # Najdi období od/do
    period = re.search(r"od:\s*(\d{2}\.\d{2}\.\d{4})\s*do:\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not period:
        raise ValueError("Nelze najít řádek 'od: .. do: ..' v TXT.")

    start_date = datetime.strptime(period.group(1), "%d.%m.%Y").date()
    end_date = datetime.strptime(period.group(2), "%d.%m.%Y").date()

    # Rozdělení po dnech (Pondělí/Úterý/...)
    day_blocks = re.split(r"(Pondělí|Úterý|Středa|Čtvrtek|Pátek|Sobota|Neděle)\s+\d{2}\.\s+leden\s+\d{4}", text)
    # day_blocks bude něco jako ["hlavička...", "Pondělí", " obsah...", "Úterý", " obsah...", ...]
    menu_by_date: dict[date, dict] = {}

    # Přibližný posun od start_date (první den po hlavičce je start_date atd.)
    current_date = start_date
    i = 1
    while i < len(day_blocks):
        day_name = day_blocks[i].strip()
        content = day_blocks[i + 1]
        i += 2

        # Bezpečnost – pokud jsme za end_date, skonči
        if current_date > end_date:
            break

        menu_by_date[current_date] = {}

        # Rozdělení podle typů jídel
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

            # Rozdělení na chody (pro SNÍDANĚ)
            chody_raw = re.split(r"chod\s*\d*:", part)
            chod_num = 0
            for chod_content in chody_raw:
                chod_content = chod_content.strip()
                if not chod_content:
                    continue
                chod_num += 1

                druh_name = _map_meal_to_druh_name(current_meal, chod_num if current_meal == "SNÍDANĚ" else None)
                menu_by_date[current_date].setdefault(druh_name, [])

                # položky: "- Název (1 3 7)" nebo "- Název"
                items = re.findall(r"-\s*([^(–\n]+?)(?:\s*\(([\d\sB]+)\))?(?=-|$)", chod_content + "-")
                # items: [(nazev, "1 7"), (nazev2, ""), ...]

                for name, alerg_str in items:
                    name = name.strip().rstrip("-").strip()
                    if not name:
                        continue
                    alerg_ids = [a for a in (alerg_str or "").split() if a]
                    menu_by_date[current_date][druh_name].append((name, alerg_ids))

        current_date = date.fromordinal(current_date.toordinal() + 1)

    return menu_by_date


def import_menu_structure(menu_by_date: dict, logger=None):
    """
    Vytvoří Jidelnicek pro každý den (platnost_od = platnost_do = den),
    vytvoří DruhJidla (pokud neexistují),
    vytvoří Jidlo a PolozkaJidelnicku.
    """
    druh_cache = {}
    alergen_cache = {}

    for datum, druhy in menu_by_date.items():
        # Jídelníček pro konkrétní den
        jidelnicek, created = Jidelnicek.objects.get_or_create(
            platnost_od=datum,
            platnost_do=datum,
            defaults={"ikona": ""},
        )
        if created and logger:
            logger.stdout.write(f"Vytvořen nový Jídelníček pro {datum}.")

        for druh_nazev, polozky in druhy.items():
            # DruhJidla
            if druh_nazev not in druh_cache:
                druh, _ = DruhJidla.objects.get_or_create(nazev=druh_nazev)
                druh_cache[druh_nazev] = druh
                if logger:
                    logger.stdout.write(f"DruhJidla '{druh_nazev}' připraven.")
            else:
                druh = druh_cache[druh_nazev]

            for jidlo_nazev, alleg_ids in polozky:
                jidlo, _ = Jidlo.objects.get_or_create(
                    nazev=jidlo_nazev,
                    druh=druh,
                    defaults={"cena": 0},
                )

                # alergeny
                for al_id in alleg_ids:
                    if al_id not in alergen_cache:
                        try:
                            al = Alergen.objects.get(id=int(al_id))
                        except (Alergen.DoesNotExist, ValueError):
                            if logger:
                                logger.stderr.write(f"Neznámý alergen ID {al_id} pro {jidlo_nazev}, ignoruji.")
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


class Command(BaseCommand):
    help = "Import jednoho TXT souboru (ručně, spíš pro debug)."

    def add_arguments(self, parser):
        parser.add_argument("txt_file", type=str)

    def handle(self, *args, **options):
        path = options["txt_file"]
        with open(path, encoding="utf-8") as f:
            content = f.read()

        menu_by_date = parse_txt_to_structure(content)
        import_menu_structure(menu_by_date, logger=self)
        self.stdout.write(self.style.SUCCESS("Import z TXT dokončen."))
