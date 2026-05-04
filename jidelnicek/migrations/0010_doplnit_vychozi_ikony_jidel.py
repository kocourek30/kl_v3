# Generated manually for KlikniJídlo visual fallbacks.

import unicodedata

from django.db import migrations


DRUH_ICONS = {
    "polevka": "fa-solid fa-bowl-food",
    "hlavni chod": "fa-solid fa-utensils",
    "obed": "fa-solid fa-utensils",
    "dezert": "fa-solid fa-ice-cream",
    "snidane": "fa-solid fa-mug-saucer",
    "snidane 1": "fa-solid fa-mug-saucer",
    "snidane 2": "fa-solid fa-bread-slice",
    "presnidavka": "fa-solid fa-apple-whole",
    "svacina": "fa-solid fa-cheese",
    "vecere": "fa-solid fa-drumstick-bite",
    "pozdni vecere": "fa-solid fa-moon",
    "napoj": "fa-solid fa-glass-water",
}

DRUH_ORDER = {
    "polevka": 10,
    "hlavni chod": 20,
    "obed": 20,
    "dezert": 30,
    "snidane": 40,
    "snidane 1": 40,
    "snidane 2": 41,
    "presnidavka": 50,
    "svacina": 60,
    "vecere": 70,
    "pozdni vecere": 80,
    "napoj": 90,
}

JIDLO_KEYWORD_ICONS = (
    (("polevka", "vyvar", "krem"), "fa-solid fa-bowl-food"),
    (("kure", "kruti", "kachna", "slepice"), "fa-solid fa-drumstick-bite"),
    (("hovezi", "veprove", "maso", "gulas", "rizek", "karbanatek", "koule"), "fa-solid fa-drumstick-bite"),
    (("ryba", "losos", "treska", "kapr", "tun", "file"), "fa-solid fa-fish"),
    (("testoviny", "spagety", "kolinka", "nudle", "tagliatelle"), "fa-solid fa-bacon"),
    (("knedlik", "brambor", "brambory", "kase", "ryze", "rizoto"), "fa-solid fa-bowl-rice"),
    (("salat", "zelenina", "okurka", "rajce", "mrkev"), "fa-solid fa-carrot"),
    (("jogurt", "mleko", "tvaroh", "syr"), "fa-solid fa-cheese"),
    (("jablko", "ovoce", "banan", "hruska"), "fa-solid fa-apple-whole"),
    (("dezert", "kolac", "buchta", "krem", "puding", "krupicova"), "fa-solid fa-ice-cream"),
    (("napoj", "caj", "voda", "dzus", "stava", "kava"), "fa-solid fa-glass-water"),
)


def normalize(value):
    text = str(value or "").strip().lower()
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def icon_for_druh(nazev):
    return DRUH_ICONS.get(normalize(nazev), "fa-solid fa-utensils")


def icon_for_jidlo(nazev, druh_nazev=""):
    normalized = normalize(nazev)
    for keywords, icon in JIDLO_KEYWORD_ICONS:
        if any(keyword in normalized for keyword in keywords):
            return icon
    return icon_for_druh(druh_nazev)


def doplnit_ikony(apps, schema_editor):
    DruhJidla = apps.get_model("jidelnicek", "DruhJidla")
    Jidlo = apps.get_model("jidelnicek", "Jidlo")

    for druh in DruhJidla.objects.all():
        normalized = normalize(druh.nazev)
        update_fields = []
        if not druh.ikona:
            druh.ikona = icon_for_druh(druh.nazev)
            update_fields.append("ikona")
        if getattr(druh, "poradi", 100) == 100 and normalized in DRUH_ORDER:
            druh.poradi = DRUH_ORDER[normalized]
            update_fields.append("poradi")
        if update_fields:
            druh.save(update_fields=update_fields)

    for jidlo in Jidlo.objects.select_related("druh").all():
        if jidlo.ikona:
            continue
        jidlo.ikona = icon_for_jidlo(
            jidlo.nazev,
            jidlo.druh.nazev if jidlo.druh_id else "",
        )
        jidlo.save(update_fields=["ikona"])


class Migration(migrations.Migration):

    dependencies = [
        ("jidelnicek", "0009_alter_druhjidla_options_druhjidla_poradi"),
    ]

    operations = [
        migrations.RunPython(doplnit_ikony, migrations.RunPython.noop),
    ]
