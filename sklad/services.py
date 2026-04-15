from collections import defaultdict
import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from objednavky.models import OrderItem

from .models import (
    Surovina,
    StavSkladu,
    PohybSkladu,
    RecepturaPolozka,
    KomponentaJidla,
    KomponentaSurovina,
    JidloKomponenta,
    PrijemSkladu,
    PolozkaPrijmu,
    Vydejka,
    PolozkaVydejky,
    Inventura,
    PolozkaInventury,
    PolozkaInventurySarze,
    NormaSpotrebnihoKose,
    ToleranceSpotrebnihoKose,
    SarzeSkladu,
    OdpisExpirace,
    SkladovaUzaverka,
)


NAZVY_SKUPIN_SPOTREBNIHO_KOSE = {
    Surovina.SK_MASO: "Maso",
    Surovina.SK_RYBY: "Ryby, korýši, měkkýši",
    Surovina.SK_MLEKO: "Mléčné výrobky, mléko",
    Surovina.SK_TUKY: "Tuky volné",
    Surovina.SK_CUKRY: "Cukry volné",
    Surovina.SK_ZELENINA_OVOCE: "Zelenina, ovoce",
    Surovina.SK_BRAMBORY: "Brambory a ostatní hlízy",
    Surovina.SK_CELOZRNNE: "Celozrnné obiloviny, pseudoobiloviny",
    Surovina.SK_LUSTENINY: "Luštěniny",
    Surovina.SK_NEZAPOCITAVA_SE: "Nezapočítává se",
    "NEZARAZENO": "Nezařazeno",
    "": "Nezařazeno",
}

LEGACY_SKUPINY_SPOTREBNIHO_KOSE_MAP = {
    "brambory": Surovina.SK_BRAMBORY,
    "cukr": Surovina.SK_CUKRY,
    "maso": Surovina.SK_MASO,
    "mleko": Surovina.SK_MLEKO,
    "obiloviny": Surovina.SK_CELOZRNNE,
    "ovoce": Surovina.SK_ZELENINA_OVOCE,
    "tuky": Surovina.SK_TUKY,
    "zelenina": Surovina.SK_ZELENINA_OVOCE,
    "": Surovina.SK_NEZAPOCITAVA_SE,
}

LEGISLATIVNI_TOLERANCE_SK_2025 = {
    Surovina.SK_MASO: (Decimal("75"), Decimal("125")),
    Surovina.SK_RYBY: (Decimal("75"), None),
    Surovina.SK_MLEKO: (Decimal("75"), Decimal("125")),
    Surovina.SK_TUKY: (Decimal("75"), Decimal("100")),
    Surovina.SK_CUKRY: (Decimal("0"), Decimal("100")),
    Surovina.SK_ZELENINA_OVOCE: (Decimal("75"), None),
    Surovina.SK_BRAMBORY: (Decimal("75"), Decimal("125")),
    Surovina.SK_CELOZRNNE: (Decimal("75"), None),
    Surovina.SK_LUSTENINY: (Decimal("75"), None),
}

STRAVOVACI_SKUPINA_NA_VEK_SK = {
    "MS": NormaSpotrebnihoKose.VEK_4_6,
    "ZS1": NormaSpotrebnihoKose.VEK_7_10,
    "ZS2": NormaSpotrebnihoKose.VEK_11_14,
    "SS": NormaSpotrebnihoKose.VEK_15_PLUS,
    "JINE": NormaSpotrebnihoKose.VEK_15_PLUS,
}

TYP_VYDEJKY_NA_TYP_NORMY = {
    Vydejka.TYP_STRAVY_OBED: NormaSpotrebnihoKose.TYP_OBED,
    Vydejka.TYP_STRAVY_SVACINA: NormaSpotrebnihoKose.TYP_SVACINA,
    Vydejka.TYP_STRAVY_VECERE: NormaSpotrebnihoKose.TYP_VECERE,
}


def nazev_skupiny_spotrebniho_kose(skupina):
    skupina = normalizuj_skupinu_spotrebniho_kose(skupina)
    return NAZVY_SKUPIN_SPOTREBNIHO_KOSE.get(skupina, skupina)


def normalizuj_skupinu_spotrebniho_kose(skupina):
    if not skupina:
        return Surovina.SK_NEZAPOCITAVA_SE
    if skupina in NAZVY_SKUPIN_SPOTREBNIHO_KOSE:
        return skupina
    return LEGACY_SKUPINY_SPOTREBNIHO_KOSE_MAP.get(skupina, skupina)


def vekova_kategorie_pro_stravovaci_skupinu(stravovaci_skupina):
    if not stravovaci_skupina:
        return NormaSpotrebnihoKose.VEK_15_PLUS
    return STRAVOVACI_SKUPINA_NA_VEK_SK.get(
        getattr(stravovaci_skupina, "typ_vzdelavani", None),
        NormaSpotrebnihoKose.VEK_15_PLUS,
    )


def typ_normy_pro_vydejku(vydejka):
    return TYP_VYDEJKY_NA_TYP_NORMY.get(
        getattr(vydejka, "typ_stravy", None),
        NormaSpotrebnihoKose.TYP_OBED,
    )


def filtr_order_items_podle_typu_vydejky(qs, typ_stravy):
    if typ_stravy == Vydejka.TYP_STRAVY_OBED:
        filtered = qs.filter(
            Q(menu_item__druh_jidla__nazev__icontains="oběd")
            | Q(menu_item__druh_jidla__nazev__icontains="obed")
            | Q(menu_item__druh_jidla__nazev__icontains="hlavní")
            | Q(menu_item__druh_jidla__nazev__icontains="hlavni")
            | Q(menu_item__druh_jidla__nazev__icontains="polév")
            | Q(menu_item__druh_jidla__nazev__icontains="polev")
            | Q(menu_item__druh_jidla__nazev__icontains="dezert")
        )
        return filtered if filtered.exists() else qs
    if typ_stravy == Vydejka.TYP_STRAVY_SVACINA:
        filtered = qs.filter(Q(menu_item__druh_jidla__nazev__icontains="svačina") | Q(menu_item__druh_jidla__nazev__icontains="svacina"))
        return filtered if filtered.exists() else qs
    if typ_stravy == Vydejka.TYP_STRAVY_VECERE:
        filtered = qs.filter(Q(menu_item__druh_jidla__nazev__icontains="večeře") | Q(menu_item__druh_jidla__nazev__icontains="vecere"))
        return filtered if filtered.exists() else qs
    return qs


def get_order_items_pro_vydejku_pro_spotrebni_kos(
    vydejka,
    stravovaci_skupina=None,
    fallback_na_datum=True,
):
    qs = OrderItem.objects.filter(order__datum_vydeje=vydejka.datum)
    skupina = stravovaci_skupina or vydejka.stravovaci_skupina
    if skupina:
        filtered = qs.filter(order__user__stravovaci_skupina=skupina)
        if filtered.exists() or not fallback_na_datum:
            qs = filtered
    return filtr_order_items_podle_typu_vydejky(qs, vydejka.typ_stravy)


def get_order_items_a_vydejky_pro_spotrebni_kos_obdobi(
    date_from,
    date_to,
    stravovaci_skupina=None,
):
    """
    Spotřební koš přiřazuje spotřebu ke stravovací skupině přes objednávky,
    ne přes volitelné pole na výdejce. Uzavřená výdejka pouze potvrzuje,
    že pro daný den / typ jídla proběhl skladový výdej.
    """
    vydejky = (
        Vydejka.objects
        .filter(
            datum__gte=date_from,
            datum__lte=date_to,
            uzavreny=True,
            stornovano=False,
        )
        .select_related("stravovaci_skupina")
        .order_by("datum", "id")
    )

    order_item_ids = set()
    prinosne_vydejky_ids = set()

    for vydejka in vydejky:
        qs = OrderItem.objects.filter(order__datum_vydeje=vydejka.datum)

        if stravovaci_skupina:
            filtrovane = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)
            if filtrovane.exists():
                qs = filtrovane
            elif vydejka.stravovaci_skupina_id == stravovaci_skupina.id:
                qs = qs
            else:
                qs = filtrovane

        qs = filtr_order_items_podle_typu_vydejky(qs, vydejka.typ_stravy)
        ids_pred = len(order_item_ids)
        order_item_ids.update(qs.values_list("id", flat=True))
        if len(order_item_ids) > ids_pred:
            prinosne_vydejky_ids.add(vydejka.id)

    return (
        OrderItem.objects.filter(id__in=order_item_ids),
        prinosne_vydejky_ids,
    )


def _spotreba_order_items_pro_spotrebni_kos(order_items):
    spotreba = defaultdict(lambda: Decimal("0"))
    for item in order_items.select_related("menu_item__jidlo"):
        jidlo_spotreba = spocitej_spotrebu_jidla(item.menu_item.jidlo, Decimal(item.quantity))
        suroviny = Surovina.objects.filter(id__in=jidlo_spotreba.keys())
        suroviny_map = {s.id: s for s in suroviny}
        for surovina_id, mnozstvi in jidlo_spotreba.items():
            surovina = suroviny_map.get(surovina_id)
            if not surovina:
                continue
            skupina = normalizuj_skupinu_spotrebniho_kose(surovina.skupina_sk)
            spotreba[skupina] += spocitej_zapocitatelnou_hmotnost_sk(surovina, mnozstvi)
    return spotreba


def tolerance_pro_skupinu(skupina, stravovaci_skupina=None):
    skupina = normalizuj_skupinu_spotrebniho_kose(skupina)
    qs = ToleranceSpotrebnihoKose.objects.filter(skupina_sk=skupina)
    if stravovaci_skupina:
        tolerance = qs.filter(stravovaci_skupina=stravovaci_skupina).first()
        if tolerance:
            return tolerance.min_pct, tolerance.max_pct
    tolerance = qs.filter(stravovaci_skupina__isnull=True).first()
    if tolerance:
        return tolerance.min_pct, tolerance.max_pct
    return LEGISLATIVNI_TOLERANCE_SK_2025.get(skupina, (Decimal("0"), None))


# =========================================================
# KOMPATIBILNÍ POMOCNÉ FUNKCE
# =========================================================

def _safe_update_fields(obj, candidate_fields):
    """
    Vrátí jen ta pole, která na modelu skutečně existují.
    Díky tomu nepadneme, pokud model ještě nemá nová auditní pole.
    """
    model_field_names = {f.name for f in obj._meta.get_fields()}
    return [name for name in candidate_fields if name in model_field_names]


def _safe_set_close_metadata(obj, user=None):
    """
    Bezpečně nastaví uzavření dokladu i na starších modelech.
    """
    if hasattr(obj, "uzavri_meta") and callable(getattr(obj, "uzavri_meta")):
        obj.uzavri_meta(user=user)
        return

    if hasattr(obj, "uzavreny"):
        obj.uzavreny = True
    if hasattr(obj, "uzavren_at"):
        obj.uzavren_at = timezone.now()
    if hasattr(obj, "uzavrel"):
        obj.uzavrel = user


def _safe_set_storno_metadata(obj, duvod=""):
    if hasattr(obj, "storno_meta") and callable(getattr(obj, "storno_meta")):
        obj.storno_meta(duvod=duvod)
        return

    if hasattr(obj, "stornovano"):
        obj.stornovano = True
    if hasattr(obj, "stornovano_at"):
        obj.stornovano_at = timezone.now()
    if duvod and hasattr(obj, "stornovano_duvod"):
        obj.stornovano_duvod = duvod


def _safe_pohyb_typ(attr_name, fallback_value):
    """
    Vrátí konstantu typu pohybu, pokud existuje, jinak fallback string.
    """
    return getattr(PohybSkladu, attr_name, fallback_value)


def _datum_pohybu_dokladu(doklad):
    datum = getattr(doklad, "datum", None)
    if not datum:
        return timezone.now()
    return timezone.make_aware(
        datetime.combine(datum, time.min),
        timezone.get_current_timezone(),
    )


def _doklad_musi_byt_uzavreny_a_nestornovany(doklad):
    if not getattr(doklad, "uzavreny", False):
        raise ValidationError("Stornovat lze pouze uzavřený doklad.")
    if getattr(doklad, "stornovano", False):
        return False
    return True


def _safe_clear_jidla(vydejka):
    """
    Některé verze modelu Vydejka mají M2M 'jidla', jiné ne.
    """
    if hasattr(vydejka, "jidla"):
        try:
            vydejka.jidla.clear()
        except Exception:
            pass


def _safe_add_jidlo(vydejka, jidlo):
    if hasattr(vydejka, "jidla"):
        try:
            vydejka.jidla.add(jidlo)
        except Exception:
            pass


# =========================================================
# SKLADOVÉ JÁDRO
# =========================================================

def get_or_create_stav_for_update(surovina: Surovina) -> StavSkladu:
    stav, _ = StavSkladu.objects.select_for_update().get_or_create(
        surovina=surovina,
        defaults={
            "mnozstvi": Decimal("0"),
            "min_mnozstvi": Decimal("0"),
        },
    )
    return stav


def preved_na_gramy(surovina: Surovina, mnozstvi) -> Decimal:
    """
    Převod na gramy pro spotřební koš.
    g -> g, kg -> g, ks -> hmotnost_ks_g * množství.
    Tekutiny zatím převádíme zjednodušeně 1 ml = 1 g a 1 l = 1000 g.
    """
    mnozstvi = Decimal(mnozstvi or 0)

    if surovina.jednotka == Surovina.JEDNOTKA_G:
        return mnozstvi
    if surovina.jednotka == Surovina.JEDNOTKA_KG:
        return mnozstvi * Decimal("1000")
    if surovina.jednotka == Surovina.JEDNOTKA_ML:
        return mnozstvi
    if surovina.jednotka == Surovina.JEDNOTKA_L:
        return mnozstvi * Decimal("1000")
    if surovina.jednotka == Surovina.JEDNOTKA_KS:
        return mnozstvi * (surovina.hmotnost_ks_g or Decimal("0"))

    return mnozstvi


def spocitej_zapocitatelnou_hmotnost_sk(surovina: Surovina, mnozstvi) -> Decimal:
    """
    Hrubé skladové množství převede na zákonně započitatelnou hmotnost v gramech.
    Vyhláška pracuje s čistou hmotností a následným koeficientem započtení.
    """
    skupina = normalizuj_skupinu_spotrebniho_kose(surovina.skupina_sk)
    if not skupina or skupina == Surovina.SK_NEZAPOCITAVA_SE:
        return Decimal("0")

    gramy = preved_na_gramy(surovina, mnozstvi)
    koef_ciste = surovina.koeficient_ciste_hmotnosti_sk or Decimal("1")
    koef_zapoctu = surovina.koeficient_zapoctu_sk or surovina.koeficient_sk or Decimal("1")
    return gramy * koef_ciste * koef_zapoctu


def preved_na_skladovou_jednotku(surovina: Surovina, mnozstvi_v_gramech) -> Decimal:
    mnozstvi_v_gramech = Decimal(mnozstvi_v_gramech or 0)

    if surovina.jednotka in (Surovina.JEDNOTKA_G, Surovina.JEDNOTKA_ML):
        return mnozstvi_v_gramech
    if surovina.jednotka in (Surovina.JEDNOTKA_KG, Surovina.JEDNOTKA_L):
        return mnozstvi_v_gramech / Decimal("1000")
    if surovina.jednotka == Surovina.JEDNOTKA_KS:
        hmotnost = surovina.hmotnost_ks_g or Decimal("0")
        if hmotnost == 0:
            return Decimal("0")
        return mnozstvi_v_gramech / hmotnost

    return mnozstvi_v_gramech


def prepocitej_mnozstvi_pro_zobrazeni(surovina: Surovina, mnozstvi):
    mnozstvi = Decimal(mnozstvi or 0)
    if surovina.jednotka == Surovina.JEDNOTKA_G:
        return mnozstvi / Decimal("1000"), Surovina.JEDNOTKA_KG
    if surovina.jednotka == Surovina.JEDNOTKA_ML:
        return mnozstvi / Decimal("1000"), Surovina.JEDNOTKA_L
    return mnozstvi, surovina.jednotka


def prepocitej_cenu_pro_zobrazeni(surovina: Surovina, cena_za_jednotku):
    cena_za_jednotku = Decimal(cena_za_jednotku or 0)
    if surovina.jednotka in (Surovina.JEDNOTKA_G, Surovina.JEDNOTKA_ML):
        return cena_za_jednotku * Decimal("1000")
    return cena_za_jednotku


def format_mnozstvi_s_jednotkou(surovina: Surovina, mnozstvi, desetinna_mista=3):
    zobrazene_mnozstvi, jednotka = prepocitej_mnozstvi_pro_zobrazeni(surovina, mnozstvi)
    return f"{zobrazene_mnozstvi:.{desetinna_mista}f} {jednotka}"


def format_cena_za_jednotku(surovina: Surovina, cena_za_jednotku):
    cena = prepocitej_cenu_pro_zobrazeni(surovina, cena_za_jednotku)
    _, jednotka = prepocitej_mnozstvi_pro_zobrazeni(surovina, 0)
    return f"{cena:.4f} Kč / {jednotka}"


def validace_surovin_pro_sk():
    """
    Kontrola kvality dat pro spotřební koš.
    """
    chyby = []

    for s in Surovina.objects.all().order_by("nazev"):
        if hasattr(s, "skupina_sk") and not s.skupina_sk:
            chyby.append(f"Surovina '{s.nazev}' nemá vyplněnou skupinu spotřebního koše.")
        if s.skupina_sk and s.skupina_sk != Surovina.SK_NEZAPOCITAVA_SE:
            if s.koeficient_ciste_hmotnosti_sk is None:
                chyby.append(f"Surovina '{s.nazev}' nemá koeficient čisté hmotnosti.")
            if s.koeficient_zapoctu_sk is None:
                chyby.append(f"Surovina '{s.nazev}' nemá započítávací koeficient.")
        if getattr(s, "jednotka", None) == "ks" and not getattr(s, "hmotnost_ks_g", None):
            chyby.append(f"Surovina '{s.nazev}' je vedena v ks a nemá hmotnost 1 ks.")
        if getattr(s, "skupina_sk", None) and getattr(s, "koeficient_sk", None) is None:
            chyby.append(f"Surovina '{s.nazev}' má skupinu SK, ale nemá koeficient.")
        if s.je_zakazano_pro_skolni_stravovani:
            chyby.append(f"Surovina '{s.nazev}' je označená jako zakázaná pro školní stravování.")
    return chyby


def get_order_items_for_vydejka(vydejka: Vydejka):
    """
    Jediný zdroj pravdy pro filtrování objednávek do výdejky.

    Pro vývoj:
    - primárně filtruje podle data a stravovací skupiny
    - pokud po zapnutí skupiny nic nenajde, vrátí alespoň objednávky podle data,
      aby šla výdejka testovat i při neúplně naplněných demo datech
    """
    base_qs = (
        OrderItem.objects
        .select_related("menu_item__jidlo", "order__user")
        .filter(order__datum_vydeje=vydejka.datum)
    )

    # zatím nepřidávám typ_stravy, protože v projektu ještě není jisté,
    # kde přesně je uložený
    if getattr(vydejka, "stravovaci_skupina_id", None):
        filtered_qs = base_qs.filter(
            order__user__stravovaci_skupina=vydejka.stravovaci_skupina
        )

        # fallback pro vývoj / demo seed
        if filtered_qs.exists():
            return filtered_qs

    return base_qs


def spocitej_stravnikodny_obdobi(date_from, date_to, stravovaci_skupina=None):
    qs = OrderItem.objects.filter(
        order__datum_vydeje__gte=date_from,
        order__datum_vydeje__lte=date_to,
    )
    if stravovaci_skupina:
        qs = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)

    total = qs.aggregate(celkem=Sum("quantity"))["celkem"] or 0
    return Decimal(total)

def spocitej_spotrebu_jidla(jidlo, pocet_porci: Decimal):
    """
    Vrátí dict {surovina_id: mnozstvi} pro dané jídlo a počet porcí.
    Umí nové komponenty i starou přímou recepturu.
    """
    spotreba = defaultdict(lambda: Decimal("0"))

    if hasattr(jidlo, "vypocitej_spotrebu_surovin"):
        data = jidlo.vypocitej_spotrebu_surovin(int(pocet_porci))
        for surovina, mnozstvi in data.items():
            spotreba[surovina.id] += Decimal(mnozstvi or 0)
        return spotreba

    # nouzový fallback
    for pol in jidlo.receptura.select_related("surovina").all():
        spotreba[pol.surovina_id] += (pol.mnozstvi_na_porci or Decimal("0")) * Decimal(pocet_porci)

    return spotreba

def spocitej_spotrebu_jidla(jidlo, pocet_porci: Decimal):
    """
    Vrátí dict {surovina_id: mnozstvi} pro dané jídlo a počet porcí.
    Preferuje nový komponentový model, fallback je stará přímá receptura.
    """
    spotreba = defaultdict(lambda: Decimal("0"))

    # nový komponentový model
    komponenty = (
        getattr(jidlo, "komponenty_jidla", None)
    )

    if komponenty is not None:
        komponenty_qs = (
            jidlo.komponenty_jidla
            .select_related("komponenta")
            .prefetch_related("komponenta__suroviny__surovina")
            .all()
        )
        if komponenty_qs.exists():
            for vazba in komponenty_qs:
                nasobek = vazba.mnozstvi_nasobek or Decimal("1")
                for pol in vazba.komponenta.suroviny.all():
                    spotreba[pol.surovina_id] += (
                        (pol.mnozstvi_na_porci or Decimal("0"))
                        * nasobek
                        * Decimal(pocet_porci)
                    )
            return spotreba

    # fallback na starou recepturu
    for pol in jidlo.receptura.select_related("surovina").all():
        spotreba[pol.surovina_id] += (
            (pol.mnozstvi_na_porci or Decimal("0")) * Decimal(pocet_porci)
        )

    return spotreba

def spocitej_teoretickou_spotrebu_pro_vydejku(vydejka: Vydejka):
    """
    Výpočet teoretické spotřeby z objednávek.
    Preferuje komponentový model Jidlo -> Komponenty -> Suroviny.
    Fallback na starou přímou recepturu.
    """
    order_items = get_order_items_for_vydejka(vydejka)
    spotreba = defaultdict(lambda: Decimal("0"))

    for item in order_items.select_related("menu_item__jidlo"):
        jidlo = item.menu_item.jidlo
        pocet_porci = Decimal(item.quantity)

        jidlo_spotreba = spocitej_spotrebu_jidla(jidlo, pocet_porci)
        for surovina_id, mnozstvi in jidlo_spotreba.items():
            spotreba[surovina_id] += mnozstvi

    return spotreba


@transaction.atomic
def generate_vydejka_from_orders(datum, stravovaci_skupina, typ_stravy="OBED"):
    """
    Vygeneruje nebo přepočítá neuzavřenou výdejku z objednávek.
    """
    vydejka = Vydejka.objects.filter(
        datum=datum,
        stravovaci_skupina=stravovaci_skupina,
        typ_stravy=typ_stravy,
        stornovano=False,
    ).first()
    created = False
    if vydejka is None:
        vydejka = Vydejka.objects.create(
            datum=datum,
            stravovaci_skupina=stravovaci_skupina,
            typ_stravy=typ_stravy,
            popis="Generováno z objednávek",
            uzavreny=False,
        )
        created = True

    if getattr(vydejka, "uzavreny", False):
        raise ValueError("Uzavřenou výdejku nelze přepočítat z objednávek.")

    spotreba = spocitej_teoretickou_spotrebu_pro_vydejku(vydejka)

    vydejka.polozky.all().delete()
    _safe_clear_jidla(vydejka)

    order_items = get_order_items_for_vydejka(vydejka)
    for item in order_items.select_related("menu_item__jidlo"):
        _safe_add_jidlo(vydejka, item.menu_item.jidlo)

    suroviny = {
        s.id: s
        for s in Surovina.objects.filter(id__in=spotreba.keys())
    }

    polozky = []
    for surovina_id, mnozstvi in spotreba.items():
        if mnozstvi <= 0:
            continue
        polozky.append(
            PolozkaVydejky(
                vydejka=vydejka,
                surovina=suroviny[surovina_id],
                mnozstvi=mnozstvi,
            )
        )

    if polozky:
        PolozkaVydejky.objects.bulk_create(polozky)

    return vydejka, created


def najdi_nedostatecne_stavy_pro_vydejku(vydejka: Vydejka):
    """
    Vrátí seznam položek výdejky, které po uzavření pošlou sklad do mínusu.
    Zatím je to varování, ne blokace uzavření.
    """
    nedostatky = []

    for pol in vydejka.polozky.select_related("surovina", "surovina__stav").all():
        surovina = pol.surovina
        stav = getattr(surovina, "stav", None)
        skladove_mnozstvi = (stav.mnozstvi if stav else Decimal("0")) or Decimal("0")
        pozadovane_mnozstvi = pol.mnozstvi or Decimal("0")

        if skladove_mnozstvi < pozadovane_mnozstvi:
            nedostatky.append({
                "surovina": surovina,
                "stav": skladove_mnozstvi,
                "pozadovano": pozadovane_mnozstvi,
                "chybi": pozadovane_mnozstvi - skladove_mnozstvi,
            })

    return nedostatky


def _validate_neprazdny_doklad(doklad, message):
    if not doklad.polozky.exists():
        raise ValidationError(message)


def _pohyb_znaminko(typ):
    if typ in (
        _safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"),
        _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"),
    ):
        return Decimal("1")
    if typ in (
        _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"),
        _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS"),
        _safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS"),
    ):
        return Decimal("-1")
    return Decimal("0")


def stav_sarze_podle_data(sarze, dnes=None):
    dnes = dnes or timezone.localdate()
    if sarze.mnozstvi_zbyva <= 0:
        return SarzeSkladu.STAV_ODEPSANA
    if not sarze.datum_spotreby:
        return SarzeSkladu.STAV_POUZITELNA
    if sarze.datum_spotreby >= dnes:
        return SarzeSkladu.STAV_POUZITELNA
    if sarze.typ_data_spotreby == "MINIMALNI_TRVANLIVOST":
        return SarzeSkladu.STAV_KARANTENA
    return SarzeSkladu.STAV_EXPIROVANA


def aktualizuj_stavy_sarzi(dnes=None):
    dnes = dnes or timezone.localdate()
    zmeneno = 0
    qs = SarzeSkladu.objects.filter(mnozstvi_zbyva__gt=0).exclude(stav=SarzeSkladu.STAV_ODEPSANA)
    for sarze in qs:
        novy_stav = stav_sarze_podle_data(sarze, dnes=dnes)
        if sarze.stav != novy_stav:
            sarze.stav = novy_stav
            sarze.save(update_fields=["stav"])
            zmeneno += 1
    return zmeneno


def vytvor_nebo_aktualizuj_sarzi_z_prijmu(polozka_prijmu):
    typ_data = getattr(polozka_prijmu, "typ_data_spotreby", "POUZITELNOST") or "POUZITELNOST"
    sarze, _ = SarzeSkladu.objects.update_or_create(
        polozka_prijmu=polozka_prijmu,
        defaults={
            "surovina": polozka_prijmu.surovina,
            "sarze": polozka_prijmu.sarze,
            "typ_data_spotreby": typ_data,
            "datum_spotreby": polozka_prijmu.datum_spotreby,
            "mnozstvi_prijato": polozka_prijmu.mnozstvi or Decimal("0"),
            "mnozstvi_zbyva": polozka_prijmu.mnozstvi or Decimal("0"),
            "cena_za_jednotku": polozka_prijmu.jednotkova_cena,
            "stav": SarzeSkladu.STAV_POUZITELNA,
        },
    )
    sarze.stav = stav_sarze_podle_data(sarze)
    sarze.save(update_fields=["stav"])
    return sarze


def najdi_sarze_fefo_pro_nahled(surovina, mnozstvi):
    aktualizuj_stavy_sarzi()
    zbyva = Decimal(mnozstvi or 0)
    if zbyva <= 0:
        return []

    cerpani = []
    sarze_qs = (
        SarzeSkladu.objects
        .filter(
            surovina=surovina,
            stav=SarzeSkladu.STAV_POUZITELNA,
            mnozstvi_zbyva__gt=0,
        )
        .order_by("datum_spotreby", "id")
    )
    for sarze in sarze_qs:
        if zbyva <= 0:
            break
        odebrat = min(sarze.mnozstvi_zbyva or Decimal("0"), zbyva)
        if odebrat <= 0:
            continue
        cerpani.append({
            "sarze": sarze,
            "mnozstvi": odebrat,
            "cena_za_jednotku": sarze.cena_za_jednotku or Decimal("0"),
            "hodnota": odebrat * (sarze.cena_za_jednotku or Decimal("0")),
        })
        zbyva -= odebrat

    if zbyva > 0:
        cerpani.append({
            "sarze": None,
            "mnozstvi": zbyva,
            "cena_za_jednotku": Decimal("0"),
            "hodnota": Decimal("0"),
            "chybi": True,
        })
    return cerpani


def nahled_vydejky(vydejka):
    radky = []
    celkem = Decimal("0")
    for pol in vydejka.polozky.select_related("surovina").all():
        cerpani = najdi_sarze_fefo_pro_nahled(pol.surovina, pol.mnozstvi)
        for row in cerpani:
            hodnota = row["hodnota"]
            celkem += hodnota
            radky.append({
                "surovina": pol.surovina,
                "pozadovano": pol.mnozstvi,
                **row,
            })
    return {"radky": radky, "hodnota_celkem": celkem}


def odeber_ze_sarzi_fefo(surovina, mnozstvi, vydejka=None):
    aktualizuj_stavy_sarzi()
    zbyva = Decimal(mnozstvi or 0)
    if zbyva <= 0:
        return []

    cerpani = []
    sarze_qs = (
        SarzeSkladu.objects
        .select_for_update()
        .filter(
            surovina=surovina,
            stav=SarzeSkladu.STAV_POUZITELNA,
            mnozstvi_zbyva__gt=0,
        )
        .order_by("datum_spotreby", "id")
    )
    for sarze in sarze_qs:
        if zbyva <= 0:
            break
        odebrat = min(sarze.mnozstvi_zbyva or Decimal("0"), zbyva)
        if odebrat <= 0:
            continue
        sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or Decimal("0")) - odebrat
        if sarze.mnozstvi_zbyva <= 0:
            sarze.stav = SarzeSkladu.STAV_ODEPSANA
        sarze.save(update_fields=["mnozstvi_zbyva", "stav"])
        cerpani.append((sarze, odebrat))
        zbyva -= odebrat

    if zbyva > 0:
        raise ValidationError(
            f"Není dostupná použitelná šarže suroviny '{surovina}' v množství {mnozstvi}. "
            f"Chybí {zbyva} {surovina.jednotka}."
        )
    return cerpani


def je_obdobi_uzavrene(datum):
    if not datum:
        return False
    return SkladovaUzaverka.objects.filter(
        rok=datum.year,
        mesic=datum.month,
        uzavreny=True,
        stornovano=False,
    ).exists()


def over_doklad_mimo_uzavrene_obdobi(doklad):
    if je_obdobi_uzavrene(doklad.datum):
        raise ValidationError(
            f"Období {doklad.datum.month:02d}/{doklad.datum.year} je skladově uzavřené. "
            "Doklad nelze uzavřít ani stornovat."
        )


def najdi_rozdily_stav_vs_sarze(tolerance=Decimal("0.001")):
    rozdily = []
    sarze_soucty = defaultdict(Decimal)
    sarze_qs = (
        SarzeSkladu.objects
        .filter(mnozstvi_zbyva__gt=0)
        .exclude(stav=SarzeSkladu.STAV_ODEPSANA)
        .select_related("surovina")
    )
    for sarze in sarze_qs:
        sarze_soucty[sarze.surovina_id] += sarze.mnozstvi_zbyva or Decimal("0")

    suroviny = Surovina.objects.select_related("stav").all().order_by("nazev")
    for surovina in suroviny:
        stav_obj = getattr(surovina, "stav", None)
        stav = stav_obj.mnozstvi if stav_obj else Decimal("0")
        sarze_stav = sarze_soucty.get(surovina.id, Decimal("0"))
        rozdil = stav - sarze_stav
        if abs(rozdil) > tolerance:
            rozdily.append({
                "surovina": surovina,
                "stav": stav,
                "sarze_stav": sarze_stav,
                "rozdil": rozdil,
                "stav_display": format_mnozstvi_s_jednotkou(surovina, stav),
                "sarze_display": format_mnozstvi_s_jednotkou(surovina, sarze_stav),
                "rozdil_display": format_mnozstvi_s_jednotkou(surovina, rozdil),
            })
    return rozdily


def _obdobi_mesice(rok, mesic):
    date_from = date(int(rok), int(mesic), 1)
    date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])
    return date_from, date_to


def pruvodce_skladovou_uzaverkou(rok, mesic):
    date_from, date_to = _obdobi_mesice(rok, mesic)
    aktualizuj_stavy_sarzi(dnes=date_to)
    neuzavrene_prijemky = PrijemSkladu.objects.filter(
        datum__gte=date_from,
        datum__lte=date_to,
        uzavreny=False,
        stornovano=False,
    )
    neuzavrene_vydejky = Vydejka.objects.filter(
        datum__gte=date_from,
        datum__lte=date_to,
        uzavreny=False,
        stornovano=False,
    )
    neuzavrene_inventury = Inventura.objects.filter(
        datum__gte=date_from,
        datum__lte=date_to,
        uzavreny=False,
        stornovano=False,
    )
    neuzavrene_odpisy = OdpisExpirace.objects.filter(
        datum__gte=date_from,
        datum__lte=date_to,
        uzavreny=False,
        stornovano=False,
    )
    expirovane_sarze = SarzeSkladu.objects.filter(
        datum_spotreby__lte=date_to,
        mnozstvi_zbyva__gt=0,
        stav=SarzeSkladu.STAV_EXPIROVANA,
    ).select_related("surovina")
    rozdily = najdi_rozdily_stav_vs_sarze()
    uzaverka = mesicni_skladova_uzaverka(rok, mesic)
    kontroly = [
        {
            "nazev": "Neuzavřené příjemky",
            "pocet": neuzavrene_prijemky.count(),
            "ok": not neuzavrene_prijemky.exists(),
            "detail": list(neuzavrene_prijemky.order_by("datum", "id")[:10]),
        },
        {
            "nazev": "Neuzavřené výdejky",
            "pocet": neuzavrene_vydejky.count(),
            "ok": not neuzavrene_vydejky.exists(),
            "detail": list(neuzavrene_vydejky.order_by("datum", "id")[:10]),
        },
        {
            "nazev": "Neuzavřené inventury",
            "pocet": neuzavrene_inventury.count(),
            "ok": not neuzavrene_inventury.exists(),
            "detail": list(neuzavrene_inventury.order_by("datum", "id")[:10]),
        },
        {
            "nazev": "Neuzavřené odpisy expirací",
            "pocet": neuzavrene_odpisy.count(),
            "ok": not neuzavrene_odpisy.exists(),
            "detail": list(neuzavrene_odpisy.order_by("datum", "id")[:10]),
        },
        {
            "nazev": "Expirované šarže k odpisu",
            "pocet": expirovane_sarze.count(),
            "ok": not expirovane_sarze.exists(),
            "detail": list(expirovane_sarze.order_by("datum_spotreby", "surovina__nazev")[:10]),
        },
        {
            "nazev": "Rozdíly stav skladu vs. šarže",
            "pocet": len(rozdily),
            "ok": not rozdily,
            "detail": rozdily[:10],
        },
        {
            "nazev": "Kontrolní rozdíl uzávěrky",
            "pocet": 0 if uzaverka["kontrola_ok"] else 1,
            "ok": uzaverka["kontrola_ok"],
            "detail": [uzaverka] if not uzaverka["kontrola_ok"] else [],
        },
    ]
    return {
        "date_from": date_from,
        "date_to": date_to,
        "uzaverka": uzaverka,
        "kontroly": kontroly,
        "pripraveno": all(k["ok"] for k in kontroly),
    }


def denni_skladovy_checklist(target_date=None):
    target_date = target_date or timezone.localdate()
    aktualizuj_stavy_sarzi(dnes=target_date)
    expirovane = SarzeSkladu.objects.filter(
        datum_spotreby__lte=target_date,
        mnozstvi_zbyva__gt=0,
        stav=SarzeSkladu.STAV_EXPIROVANA,
    ).select_related("surovina").order_by("datum_spotreby", "surovina__nazev")[:20]
    return {
        "datum": target_date,
        "neuzavrene_prijemky": PrijemSkladu.objects.filter(datum=target_date, uzavreny=False, stornovano=False).order_by("id")[:20],
        "neuzavrene_vydejky": Vydejka.objects.filter(datum=target_date, uzavreny=False, stornovano=False).order_by("id")[:20],
        "neuzavrene_inventury": Inventura.objects.filter(datum=target_date, uzavreny=False, stornovano=False).order_by("id")[:20],
        "expirovane_sarze": expirovane,
        "minimum_alerty": [
            row for row in StavSkladu.objects.select_related("surovina").filter(min_mnozstvi__gt=0)
            if (row.mnozstvi or Decimal("0")) <= (row.min_mnozstvi or Decimal("0"))
        ][:20],
        "rozdily_stav_sarze": najdi_rozdily_stav_vs_sarze()[:20],
    }


def zdravi_skladu(target_date=None):
    """
    Manažerský kontrolní report skladu k jednomu dni.
    Vrací agregace i konkrétní řádky pro dashboard, PDF a uzávěrkový protokol.
    """
    target_date = target_date or timezone.localdate()
    aktualizuj_stavy_sarzi(dnes=target_date)

    checklist = denni_skladovy_checklist(target_date)
    hodnota = hodnota_skladu_aktualni()
    expirace_do = target_date + timedelta(days=14)
    expirovane_qs = (
        SarzeSkladu.objects
        .filter(
            datum_spotreby__lte=target_date,
            mnozstvi_zbyva__gt=0,
            stav=SarzeSkladu.STAV_EXPIROVANA,
        )
        .select_related("surovina")
        .order_by("datum_spotreby", "surovina__nazev", "id")
    )
    blizka_expirace_qs = (
        SarzeSkladu.objects
        .filter(
            datum_spotreby__gt=target_date,
            datum_spotreby__lte=expirace_do,
            mnozstvi_zbyva__gt=0,
            stav__in=[SarzeSkladu.STAV_POUZITELNA, SarzeSkladu.STAV_KARANTENA],
        )
        .select_related("surovina")
        .order_by("datum_spotreby", "surovina__nazev", "id")
    )
    pohyby_bez_ceny = (
        PohybSkladu.objects
        .filter(Q(cena_za_jednotku__isnull=True) | Q(cena_za_jednotku=0))
        .select_related("surovina", "prijem", "vydejka", "inventura", "odpis_expirace")
        .order_by("-datum", "-id")[:30]
    )
    sarze_bez_ceny = (
        SarzeSkladu.objects
        .filter(mnozstvi_zbyva__gt=0)
        .filter(Q(cena_za_jednotku__isnull=True) | Q(cena_za_jednotku=0))
        .select_related("surovina")
        .order_by("surovina__nazev", "datum_spotreby", "id")[:30]
    )
    suroviny_bez_skupiny = (
        Surovina.objects
        .filter(Q(skupina_sk__isnull=True) | Q(skupina_sk=""))
        .order_by("nazev")[:30]
    )
    zaporne_stavy = (
        StavSkladu.objects
        .filter(mnozstvi__lt=0)
        .select_related("surovina")
        .order_by("surovina__nazev")[:30]
    )

    otevrene_doklady = (
        len(checklist["neuzavrene_prijemky"])
        + len(checklist["neuzavrene_vydejky"])
        + len(checklist["neuzavrene_inventury"])
    )
    rizika = [
        {
            "nazev": "Otevřené doklady dne",
            "pocet": otevrene_doklady,
            "ok": otevrene_doklady == 0,
            "popis": "Příjemky, výdejky a inventury by měly být před uzávěrkou uzavřené.",
        },
        {
            "nazev": "Prošlé šarže k odpisu",
            "pocet": expirovane_qs.count(),
            "ok": not expirovane_qs.exists(),
            "popis": "Prošlé potraviny nesmí zůstávat jako použitelný sklad.",
        },
        {
            "nazev": "Suroviny pod minimem",
            "pocet": len(checklist["minimum_alerty"]),
            "ok": len(checklist["minimum_alerty"]) == 0,
            "popis": "Minimální zásoba pomáhá včas spustit nákup.",
        },
        {
            "nazev": "Rozdíly stav skladu vs. šarže",
            "pocet": len(checklist["rozdily_stav_sarze"]),
            "ok": len(checklist["rozdily_stav_sarze"]) == 0,
            "popis": "Součet aktivních šarží má sedět na stav skladu.",
        },
        {
            "nazev": "Pohyby bez ceny",
            "pocet": len(pohyby_bez_ceny),
            "ok": len(pohyby_bez_ceny) == 0,
            "popis": "Pohyby bez ceny zkreslují náklady a hodnotu skladu.",
        },
        {
            "nazev": "Aktivní šarže bez ceny",
            "pocet": len(sarze_bez_ceny),
            "ok": len(sarze_bez_ceny) == 0,
            "popis": "Šarže bez ceny neumí přesně ocenit výdej ani inventuru.",
        },
        {
            "nazev": "Suroviny bez skupiny spotřebního koše",
            "pocet": len(suroviny_bez_skupiny),
            "ok": len(suroviny_bez_skupiny) == 0,
            "popis": "Skupina spotřebního koše je nutná pro legislativní report.",
        },
        {
            "nazev": "Záporné stavy skladu",
            "pocet": len(zaporne_stavy),
            "ok": len(zaporne_stavy) == 0,
            "popis": "Záporný stav znamená problém v dokladech nebo inventuře.",
        },
    ]
    return {
        "datum": target_date,
        "expirace_do": expirace_do,
        "hodnota_skladu": hodnota["hodnota_celkem"],
        "pocet_sarzi": len(hodnota["radky"]),
        "rizika": rizika,
        "skore": int(sum(1 for row in rizika if row["ok"]) / len(rizika) * 100) if rizika else 100,
        "pripraveno_k_uzaverce": all(row["ok"] for row in rizika),
        "checklist": checklist,
        "expirovane_sarze": list(expirovane_qs[:30]),
        "blizka_expirace": list(blizka_expirace_qs[:30]),
        "pohyby_bez_ceny": list(pohyby_bez_ceny),
        "sarze_bez_ceny": list(sarze_bez_ceny),
        "suroviny_bez_skupiny": list(suroviny_bez_skupiny),
        "zaporne_stavy": list(zaporne_stavy),
    }


def navrh_nakupu(date_from=None, date_to=None, dnu=7):
    date_from = date_from or timezone.localdate()
    date_to = date_to or (date_from + timedelta(days=dnu - 1))
    planovana_spotreba = defaultdict(Decimal)
    order_items = (
        OrderItem.objects
        .filter(order__datum_vydeje__gte=date_from, order__datum_vydeje__lte=date_to)
        .select_related("menu_item__jidlo")
    )
    for item in order_items:
        spotreba = spocitej_spotrebu_jidla(item.menu_item.jidlo, Decimal(item.quantity))
        for surovina_id, mnozstvi in spotreba.items():
            planovana_spotreba[surovina_id] += mnozstvi

    surovina_ids = set(planovana_spotreba.keys()) | set(
        StavSkladu.objects.filter(min_mnozstvi__gt=0).values_list("surovina_id", flat=True)
    )
    rows = []
    for surovina in Surovina.objects.filter(id__in=surovina_ids).select_related("stav").order_by("nazev"):
        stav = getattr(surovina, "stav", None)
        aktualni = stav.mnozstvi if stav else Decimal("0")
        minimum = stav.min_mnozstvi if stav else Decimal("0")
        plan = planovana_spotreba.get(surovina.id, Decimal("0"))
        chybi = max(Decimal("0"), plan + minimum - aktualni)
        if chybi <= 0:
            continue
        posledni_polozka = (
            PolozkaPrijmu.objects
            .filter(surovina=surovina, prijem__uzavreny=True, prijem__stornovano=False)
            .select_related("prijem__dodavatel")
            .order_by("-prijem__datum", "-id")
            .first()
        )
        cena = getattr(posledni_polozka, "jednotkova_cena", None) or getattr(surovina, "prumerna_cena_za_jednotku", None) or Decimal("0")
        rows.append({
            "surovina": surovina,
            "aktualni": aktualni,
            "minimum": minimum,
            "plan": plan,
            "chybi": chybi,
            "odhad_ceny": chybi * cena,
            "dodavatel": posledni_polozka.prijem.dodavatel if posledni_polozka and posledni_polozka.prijem else None,
            "aktualni_display": format_mnozstvi_s_jednotkou(surovina, aktualni),
            "minimum_display": format_mnozstvi_s_jednotkou(surovina, minimum),
            "plan_display": format_mnozstvi_s_jednotkou(surovina, plan),
            "chybi_display": format_mnozstvi_s_jednotkou(surovina, chybi),
            "cena_display": format_cena_za_jednotku(surovina, cena),
        })
    return {
        "date_from": date_from,
        "date_to": date_to,
        "radky": rows,
        "odhad_celkem": sum((row["odhad_ceny"] for row in rows), Decimal("0")),
    }


def karta_suroviny_data(surovina, date_from=None, date_to=None):
    date_to = date_to or timezone.localdate()
    date_from = date_from or (date_to - timedelta(days=90))
    stav = getattr(surovina, "stav", None)
    pohyby = (
        PohybSkladu.objects
        .filter(surovina=surovina, datum__date__gte=date_from, datum__date__lte=date_to)
        .select_related("prijem", "vydejka", "inventura", "odpis_expirace", "sarze_skladu")
        .order_by("-datum", "-id")
    )
    prijmy = [p for p in pohyby if p.typ == _safe_pohyb_typ("TYP_PRIJEM", "PRIJEM") and p.prijem_id]
    vydeje = [p for p in pohyby if p.typ == _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ") and p.vydejka_id]
    odpisy = [p for p in pohyby if p.typ == _safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS")]
    inventury = [p for p in pohyby if p.inventura_id]
    cenova_historie = (
        PolozkaPrijmu.objects
        .filter(surovina=surovina, prijem__uzavreny=True, prijem__stornovano=False)
        .select_related("prijem", "prijem__dodavatel")
        .order_by("-prijem__datum", "-id")[:20]
    )
    return {
        "surovina": surovina,
        "date_from": date_from,
        "date_to": date_to,
        "stav": stav.mnozstvi if stav else Decimal("0"),
        "minimum": stav.min_mnozstvi if stav else Decimal("0"),
        "stav_display": format_mnozstvi_s_jednotkou(surovina, stav.mnozstvi if stav else Decimal("0")),
        "minimum_display": format_mnozstvi_s_jednotkou(surovina, stav.min_mnozstvi if stav else Decimal("0")),
        "hodnota_stavu": hodnota_skladu_aktualni()["hodnota_celkem"] if not surovina else sum((hodnota_sarze(s) for s in SarzeSkladu.objects.filter(surovina=surovina, mnozstvi_zbyva__gt=0)), Decimal("0")),
        "sarze": SarzeSkladu.objects.filter(surovina=surovina, mnozstvi_zbyva__gt=0).order_by("datum_spotreby", "id")[:30],
        "pohyby": pohyby[:50],
        "cenova_historie": cenova_historie,
        "spotreba_obdobi": sum((p.mnozstvi or Decimal("0") for p in vydeje), Decimal("0")),
        "spotreba_obdobi_display": format_mnozstvi_s_jednotkou(surovina, sum((p.mnozstvi or Decimal("0") for p in vydeje), Decimal("0"))),
        "naklady_spotreby": sum((hodnota_pohybu(p) for p in vydeje), Decimal("0")),
        "odpisy": sum((hodnota_pohybu(p) for p in odpisy), Decimal("0")),
        "inventurni_rozdily": sum((_pohyb_znaminko(p.typ) * hodnota_pohybu(p) for p in inventury), Decimal("0")),
        "prijmy_hodnota": sum((hodnota_pohybu(p) for p in prijmy), Decimal("0")),
    }


def inventurni_nahled(inventura):
    rows = []
    for pol in inventura.polozky.select_related("surovina").all():
        sarze = list(
            SarzeSkladu.objects
            .filter(surovina=pol.surovina, mnozstvi_zbyva__gt=0)
            .order_by("datum_spotreby", "id")[:20]
        )
        rows.append({
            "polozka": pol,
            "surovina": pol.surovina,
            "stav_pred": pol.stav_pred,
            "fyzicky_stav": pol.fyzicky_stav,
            "rozdil": (pol.fyzicky_stav or Decimal("0")) - (pol.stav_pred or Decimal("0")),
            "sarze": sarze,
            "stav_pred_display": format_mnozstvi_s_jednotkou(pol.surovina, pol.stav_pred),
            "fyzicky_stav_display": format_mnozstvi_s_jednotkou(pol.surovina, pol.fyzicky_stav),
            "rozdil_display": format_mnozstvi_s_jednotkou(pol.surovina, (pol.fyzicky_stav or Decimal("0")) - (pol.stav_pred or Decimal("0"))),
        })
    return rows


def napln_sarzovou_inventuru(inventura):
    if getattr(inventura, "uzavreny", False) or getattr(inventura, "stornovano", False):
        raise ValidationError("Uzavřenou nebo stornovanou inventuru nelze přepočítat.")

    vytvoreno = 0
    sarze_qs = (
        SarzeSkladu.objects
        .filter(mnozstvi_zbyva__gt=0)
        .exclude(stav=SarzeSkladu.STAV_ODEPSANA)
        .select_related("surovina")
        .order_by("surovina__nazev", "datum_spotreby", "id")
    )
    for sarze in sarze_qs:
        _, created = PolozkaInventurySarze.objects.get_or_create(
            inventura=inventura,
            sarze_skladu=sarze,
            defaults={
                "surovina": sarze.surovina,
                "sarze": sarze.sarze,
                "typ_data_spotreby": sarze.typ_data_spotreby,
                "datum_spotreby": sarze.datum_spotreby,
                "stav_pred": sarze.mnozstvi_zbyva or Decimal("0"),
                "fyzicky_stav": sarze.mnozstvi_zbyva or Decimal("0"),
                "cena_za_jednotku": sarze.cena_za_jednotku,
                "je_nova_sarze": False,
            },
        )
        if created:
            vytvoreno += 1
    return vytvoreno


def synchronizuj_surovinove_polozky_inventury(inventura):
    soucty = defaultdict(lambda: {"stav_pred": Decimal("0"), "fyzicky_stav": Decimal("0")})
    for pol in inventura.sarze_polozky.select_related("surovina").all():
        soucty[pol.surovina_id]["stav_pred"] += pol.stav_pred or Decimal("0")
        soucty[pol.surovina_id]["fyzicky_stav"] += pol.fyzicky_stav or Decimal("0")

    existujici = {
        pol.surovina_id: pol
        for pol in inventura.polozky.select_related("surovina").all()
    }
    for surovina_id, data in soucty.items():
        pol = existujici.get(surovina_id)
        if pol is None:
            pol = PolozkaInventury(inventura=inventura, surovina_id=surovina_id)
        pol.stav_pred = data["stav_pred"]
        pol.fyzicky_stav = data["fyzicky_stav"]
        pol.rozdil = data["fyzicky_stav"] - data["stav_pred"]
        pol.save()


def souhrn_sarzove_inventury(inventura):
    synchronizuj_surovinove_polozky_inventury(inventura)
    rows = []
    manko = Decimal("0")
    prebytek = Decimal("0")
    for pol in inventura.sarze_polozky.select_related("surovina", "sarze_skladu").all():
        cena = pol.cena_za_jednotku or getattr(pol.surovina, "prumerna_cena_za_jednotku", None) or Decimal("0")
        hodnota = abs(pol.rozdil or Decimal("0")) * cena
        if (pol.rozdil or Decimal("0")) < 0:
            manko += hodnota
        elif (pol.rozdil or Decimal("0")) > 0:
            prebytek += hodnota
        rows.append({
            "polozka": pol,
            "surovina": pol.surovina,
            "sarze": pol.sarze,
            "stav_pred": pol.stav_pred,
            "fyzicky_stav": pol.fyzicky_stav,
            "rozdil": pol.rozdil,
            "cena": cena,
            "hodnota": hodnota,
            "stav_pred_display": format_mnozstvi_s_jednotkou(pol.surovina, pol.stav_pred),
            "fyzicky_stav_display": format_mnozstvi_s_jednotkou(pol.surovina, pol.fyzicky_stav),
            "rozdil_display": format_mnozstvi_s_jednotkou(pol.surovina, pol.rozdil),
        })
    return {
        "radky": rows,
        "pocet_polozek": len(rows),
        "manko": manko,
        "prebytek": prebytek,
        "cisty_rozdil": prebytek - manko,
    }


def validace_prijemky_pred_uzavrenim(prijem, tolerance_ceny_pct=Decimal("30"), tolerance_faktury=Decimal("1.00")):
    varovani = []
    if prijem.castka_faktury_celkem is not None and abs(prijem.rozdil_faktury or Decimal("0")) > tolerance_faktury:
        varovani.append(f"Rozdíl proti faktuře je {prijem.rozdil_faktury:.2f} Kč.")
    for pol in prijem.polozky.select_related("surovina").all():
        if not pol.sarze:
            varovani.append(f"Položka '{pol.surovina}' nemá vyplněnou šarži.")
        if not pol.datum_spotreby and pol.typ_data_spotreby != "NEUVADI_SE":
            varovani.append(f"Položka '{pol.surovina}' nemá vyplněné datum spotřeby/minimální trvanlivosti.")
        posledni = (
            PolozkaPrijmu.objects
            .filter(surovina=pol.surovina, prijem__uzavreny=True, prijem__stornovano=False)
            .exclude(pk=pol.pk)
            .order_by("-prijem__datum", "-id")
            .first()
        )
        if posledni and posledni.jednotkova_cena:
            rozdil_pct = abs((pol.jednotkova_cena - posledni.jednotkova_cena) / posledni.jednotkova_cena * Decimal("100"))
            if rozdil_pct > tolerance_ceny_pct:
                varovani.append(
                    f"Cena položky '{pol.surovina}' se liší o {rozdil_pct:.1f} % proti poslední příjemce."
                )
    return varovani


def managersky_report_skladu(rok, mesic):
    date_from, date_to = _obdobi_mesice(rok, mesic)
    qs = PohybSkladu.objects.filter(
        datum__date__gte=date_from,
        datum__date__lte=date_to,
    ).select_related("surovina")
    spotreba = defaultdict(lambda: {"mnozstvi": Decimal("0"), "hodnota": Decimal("0"), "surovina": None})
    odpisy = defaultdict(lambda: {"mnozstvi": Decimal("0"), "hodnota": Decimal("0"), "surovina": None})
    inventury = defaultdict(lambda: {"mnozstvi": Decimal("0"), "hodnota": Decimal("0"), "surovina": None})
    for pohyb in qs:
        bucket = None
        if pohyb.typ == _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ") and pohyb.vydejka_id:
            bucket = spotreba
        elif pohyb.typ == _safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS"):
            bucket = odpisy
        elif pohyb.typ in [_safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"), _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")]:
            bucket = inventury
        if bucket is None:
            continue
        row = bucket[pohyb.surovina_id]
        row["surovina"] = pohyb.surovina
        row["mnozstvi"] += pohyb.mnozstvi or Decimal("0")
        row["hodnota"] += hodnota_pohybu(pohyb)
    return {
        "top_spotreba": sorted(spotreba.values(), key=lambda r: r["hodnota"], reverse=True)[:20],
        "top_odpisy": sorted(odpisy.values(), key=lambda r: r["hodnota"], reverse=True)[:20],
        "top_inventury": sorted(inventury.values(), key=lambda r: r["hodnota"], reverse=True)[:20],
        "hodnota_skladu": hodnota_skladu_k_datu(date_to),
        "naklady": spocitej_naklady_mesic(rok, mesic),
    }


def vytvor_inventurni_sarzi(surovina, inventura, mnozstvi):
    cena = getattr(surovina, "prumerna_cena_za_jednotku", None) or Decimal("0")
    return SarzeSkladu.objects.create(
        surovina=surovina,
        sarze=f"INV-{inventura.id}-{surovina.id}",
        typ_data_spotreby="NEUVADI_SE",
        datum_spotreby=None,
        mnozstvi_prijato=mnozstvi,
        mnozstvi_zbyva=mnozstvi,
        cena_za_jednotku=cena,
        stav=SarzeSkladu.STAV_POUZITELNA,
        poznamka=f"Inventurní přebytek z inventury #{inventura.id}",
    )


@transaction.atomic
def uzavri_odpis_expirace(odpis: OdpisExpirace, user=None) -> bool:
    if getattr(odpis, "uzavreny", False):
        return False
    if getattr(odpis, "stornovano", False):
        raise ValidationError("Stornovaný odpis expirace nelze uzavřít.")
    over_doklad_mimo_uzavrene_obdobi(odpis)

    aktualizuj_stavy_sarzi(dnes=odpis.datum)
    sarze_qs = SarzeSkladu.objects.select_for_update().filter(
        stav=SarzeSkladu.STAV_EXPIROVANA,
        mnozstvi_zbyva__gt=0,
        datum_spotreby__lte=odpis.datum,
    )
    if not sarze_qs.exists():
        raise ValidationError("Neexistují žádné expirované šarže k odpisu.")

    for sarze in sarze_qs:
        mnozstvi = sarze.mnozstvi_zbyva or Decimal("0")
        stav = get_or_create_stav_for_update(sarze.surovina)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
        stav.save(update_fields=["mnozstvi"])

        sarze.mnozstvi_zbyva = Decimal("0")
        sarze.stav = SarzeSkladu.STAV_ODEPSANA
        sarze.save(update_fields=["mnozstvi_zbyva", "stav"])

        PohybSkladu.objects.create(
            datum=_datum_pohybu_dokladu(odpis),
            surovina=sarze.surovina,
            typ=_safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS"),
            mnozstvi=mnozstvi,
            cena_za_jednotku=sarze.cena_za_jednotku,
            odpis_expirace=odpis,
            sarze_skladu=sarze,
            poznamka=f"Odpis expirace #{odpis.id} / šarže {sarze.sarze or sarze.id}",
        )

    _safe_set_close_metadata(odpis, user=user)
    odpis.save(update_fields=_safe_update_fields(odpis, ["uzavreny", "uzavren_at", "uzavrel"]))
    return True


def souhrn_odpisu_expirace(odpis: OdpisExpirace):
    pohyby = (
        PohybSkladu.objects
        .filter(odpis_expirace=odpis)
        .select_related("surovina", "sarze_skladu")
        .order_by("surovina__nazev", "id")
    )
    celkem = Decimal("0")
    mnozstvi_celkem = Decimal("0")
    polozky = []
    for pohyb in pohyby:
        hodnota = (pohyb.mnozstvi or Decimal("0")) * (pohyb.cena_za_jednotku or Decimal("0"))
        celkem += hodnota
        mnozstvi_celkem += pohyb.mnozstvi or Decimal("0")
        polozky.append({
            "pohyb": pohyb,
            "surovina": pohyb.surovina,
            "sarze": pohyb.sarze_skladu,
            "mnozstvi": pohyb.mnozstvi or Decimal("0"),
            "cena_za_jednotku": pohyb.cena_za_jednotku or Decimal("0"),
            "hodnota": hodnota,
        })

    return {
        "pocet_pohybu": pohyby.count(),
        "mnozstvi_celkem": mnozstvi_celkem,
        "hodnota_celkem": celkem,
        "polozky": polozky,
    }


def hodnota_pohybu(pohyb):
    return (pohyb.mnozstvi or Decimal("0")) * (pohyb.cena_za_jednotku or Decimal("0"))


def hodnota_sarze(sarze):
    return (sarze.mnozstvi_zbyva or Decimal("0")) * (sarze.cena_za_jednotku or Decimal("0"))


def hodnota_skladu_aktualni():
    celkem = Decimal("0")
    radky = []
    qs = (
        SarzeSkladu.objects
        .filter(mnozstvi_zbyva__gt=0)
        .select_related("surovina")
        .order_by("surovina__nazev", "datum_spotreby", "id")
    )
    for sarze in qs:
        hodnota = hodnota_sarze(sarze)
        celkem += hodnota
        radky.append({
            "surovina": sarze.surovina,
            "sarze": sarze,
            "mnozstvi": sarze.mnozstvi_zbyva or Decimal("0"),
            "jednotka": sarze.surovina.jednotka,
            "cena_za_jednotku": sarze.cena_za_jednotku or Decimal("0"),
            "hodnota": hodnota,
        })
    return {"hodnota_celkem": celkem, "radky": radky}


def hodnota_skladu_k_datu(date_to, surovina=None):
    qs = PohybSkladu.objects.filter(datum__date__lte=date_to).select_related("surovina")
    if surovina is not None:
        qs = qs.filter(surovina=surovina)

    celkem = Decimal("0")
    for pohyb in qs:
        znaminko = _pohyb_znaminko(pohyb.typ)
        if znaminko == 0:
            continue
        celkem += znaminko * hodnota_pohybu(pohyb)
    return celkem


def hodnota_pohybu_obdobi(date_from, date_to, typy=None, **filtry):
    qs = PohybSkladu.objects.filter(
        datum__date__gte=date_from,
        datum__date__lte=date_to,
        **filtry,
    )
    if typy is not None:
        qs = qs.filter(typ__in=typy)
    return sum((hodnota_pohybu(pohyb) for pohyb in qs), Decimal("0"))


def mesicni_skladova_uzaverka(rok, mesic):
    from datetime import date, timedelta

    date_from = date(int(rok), int(mesic), 1)
    date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])
    predchozi_den = date_from - timedelta(days=1)

    typ_prijem = _safe_pohyb_typ("TYP_PRIJEM", "PRIJEM")
    typ_vydej = _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ")
    typ_inventura_plus = _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS")
    typ_inventura_minus = _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")
    typ_expirace = _safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS")

    prijmy = hodnota_pohybu_obdobi(date_from, date_to, [typ_prijem], prijem__isnull=False)
    storna_prijmu = hodnota_pohybu_obdobi(date_from, date_to, [typ_vydej], prijem__isnull=False)
    vydeje = hodnota_pohybu_obdobi(date_from, date_to, [typ_vydej], vydejka__isnull=False)
    storna_vydeju = hodnota_pohybu_obdobi(date_from, date_to, [typ_prijem], vydejka__isnull=False)
    odpisy_expirace = hodnota_pohybu_obdobi(date_from, date_to, [typ_expirace], odpis_expirace__isnull=False)
    inventura_plus = hodnota_pohybu_obdobi(date_from, date_to, [typ_inventura_plus], inventura__isnull=False)
    inventura_minus = hodnota_pohybu_obdobi(date_from, date_to, [typ_inventura_minus], inventura__isnull=False)

    pocatecni_stav = hodnota_skladu_k_datu(predchozi_den)
    konecny_stav = hodnota_skladu_k_datu(date_to)
    vypocet_konecneho_stavu = (
        pocatecni_stav
        + prijmy
        - storna_prijmu
        - vydeje
        + storna_vydeju
        - odpisy_expirace
        + inventura_plus
        - inventura_minus
    )
    rozdil_kontroly = konecny_stav - vypocet_konecneho_stavu

    return {
        "date_from": date_from,
        "date_to": date_to,
        "pocatecni_stav": pocatecni_stav,
        "prijmy": prijmy,
        "storna_prijmu": storna_prijmu,
        "vydeje": vydeje,
        "storna_vydeju": storna_vydeju,
        "odpisy_expirace": odpisy_expirace,
        "inventura_plus": inventura_plus,
        "inventura_minus": inventura_minus,
        "konecny_stav": konecny_stav,
        "vypocet_konecneho_stavu": vypocet_konecneho_stavu,
        "rozdil_kontroly": rozdil_kontroly,
        "kontrola_ok": rozdil_kontroly == 0,
    }


@transaction.atomic
def uzavri_skladovou_uzaverku(uzaverka: SkladovaUzaverka, user=None) -> bool:
    if getattr(uzaverka, "uzavreny", False):
        return False
    if getattr(uzaverka, "stornovano", False):
        raise ValidationError("Stornovanou skladovou uzávěrku nelze uzavřít.")

    data = mesicni_skladova_uzaverka(uzaverka.rok, uzaverka.mesic)
    for field in [
        "pocatecni_stav",
        "prijmy",
        "storna_prijmu",
        "vydeje",
        "storna_vydeju",
        "odpisy_expirace",
        "inventura_plus",
        "inventura_minus",
        "vypocet_konecneho_stavu",
        "konecny_stav",
        "rozdil_kontroly",
    ]:
        setattr(uzaverka, field, data[field])
    uzaverka.datum = data["date_to"]
    _safe_set_close_metadata(uzaverka, user=user)
    uzaverka.save()
    return True


@transaction.atomic
def otevri_skladovou_uzaverku(uzaverka: SkladovaUzaverka, user=None, duvod="Storno uzávěrky z administrace") -> bool:
    if not getattr(uzaverka, "uzavreny", False) or getattr(uzaverka, "stornovano", False):
        return False
    uzaverka.storno_meta(duvod=duvod)
    uzaverka.save(update_fields=_safe_update_fields(uzaverka, ["stornovano", "stornovano_at", "stornovano_duvod"]))
    return True


def stav_skladu_k_datu(date_to, surovina=None):
    """
    Rekonstrukce skladu z pohybů k danému dni včetně storen jako opačných pohybů.
    """
    qs = PohybSkladu.objects.filter(datum__date__lte=date_to).select_related("surovina")
    if surovina is not None:
        qs = qs.filter(surovina=surovina)

    stavy = defaultdict(lambda: Decimal("0"))
    for pohyb in qs:
        stavy[pohyb.surovina_id] += _pohyb_znaminko(pohyb.typ) * (pohyb.mnozstvi or Decimal("0"))

    if surovina is not None:
        return stavy.get(surovina.id, Decimal("0"))

    return dict(stavy)


@transaction.atomic
def uzavri_prijem(prijem: PrijemSkladu, user=None) -> bool:
    """
    Idempotentní uzavření příjemky.
    Kompatibilní i se starší verzí modelů.
    """
    if getattr(prijem, "uzavreny", False):
        return False
    if getattr(prijem, "stornovano", False):
        raise ValidationError("Stornovanou příjemku nelze uzavřít.")
    over_doklad_mimo_uzavrene_obdobi(prijem)

    _validate_neprazdny_doklad(prijem, "Příjemku bez položek nelze uzavřít.")

    for pol in prijem.polozky.select_related("surovina").all():
        surovina = pol.surovina
        stav = get_or_create_stav_for_update(surovina)

        stare_mnozstvi = stav.mnozstvi or Decimal("0")
        stara_cena = getattr(surovina, "prumerna_cena_za_jednotku", None) or Decimal("0")

        prijate_mnozstvi = pol.mnozstvi or Decimal("0")
        prijata_cena = getattr(pol, "jednotkova_cena", None) or Decimal("0")
        if prijate_mnozstvi <= 0:
            raise ValidationError(
                f"Položka příjemky '{surovina}' musí mít kladné množství."
            )

        nove_mnozstvi = stare_mnozstvi + prijate_mnozstvi

        if nove_mnozstvi > 0 and hasattr(surovina, "prumerna_cena_za_jednotku"):
            if stare_mnozstvi > 0:
                nova_cena = (
                    stare_mnozstvi * stara_cena + prijate_mnozstvi * prijata_cena
                ) / nove_mnozstvi
            else:
                nova_cena = prijata_cena

            surovina.prumerna_cena_za_jednotku = nova_cena
            surovina.save(update_fields=["prumerna_cena_za_jednotku"])

        stav.mnozstvi = nove_mnozstvi
        stav.save(update_fields=["mnozstvi"])

        sarze = vytvor_nebo_aktualizuj_sarzi_z_prijmu(pol)
        PohybSkladu.objects.create(
            datum=_datum_pohybu_dokladu(prijem),
            surovina=surovina,
            typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"),
            mnozstvi=prijate_mnozstvi,
            cena_za_jednotku=prijata_cena if "cena_za_jednotku" in [f.name for f in PohybSkladu._meta.fields] else None,
            prijem=prijem if "prijem" in [f.name for f in PohybSkladu._meta.fields] else None,
            sarze_skladu=sarze if "sarze_skladu" in [f.name for f in PohybSkladu._meta.fields] else None,
            poznamka=f"Příjemka #{prijem.id}",
        )

    _safe_set_close_metadata(prijem, user=user)
    prijem.save(update_fields=_safe_update_fields(prijem, ["uzavreny", "uzavren_at", "uzavrel"]))
    return True


@transaction.atomic
def uzavri_vydejku(vydejka: Vydejka, user=None) -> bool:
    """
    Idempotentní uzavření výdejky.
    Jediná povolená cesta pro odpis skladu.
    """
    if getattr(vydejka, "uzavreny", False):
        return False
    if getattr(vydejka, "stornovano", False):
        raise ValidationError("Stornovanou výdejku nelze uzavřít.")
    over_doklad_mimo_uzavrene_obdobi(vydejka)

    _validate_neprazdny_doklad(vydejka, "Výdejku bez položek nelze uzavřít.")

    for pol in vydejka.polozky.select_related("surovina").all():
        surovina = pol.surovina
        stav = get_or_create_stav_for_update(surovina)

        mnozstvi = pol.mnozstvi or Decimal("0")
        cerpani_sarzi = odeber_ze_sarzi_fefo(surovina, mnozstvi, vydejka=vydejka)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
        stav.save(update_fields=["mnozstvi"])

        pohyb_fields = {f.name for f in PohybSkladu._meta.fields}
        for sarze, odebrano in cerpani_sarzi:
            kwargs = {
                "datum": _datum_pohybu_dokladu(vydejka),
                "surovina": surovina,
                "typ": _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"),
                "mnozstvi": odebrano,
                "poznamka": f"Výdejka #{vydejka.id} / šarže {sarze.sarze or sarze.id}",
            }
            if "cena_za_jednotku" in pohyb_fields:
                kwargs["cena_za_jednotku"] = sarze.cena_za_jednotku or getattr(surovina, "prumerna_cena_za_jednotku", None)
            if "vydejka" in pohyb_fields:
                kwargs["vydejka"] = vydejka
            if "sarze_skladu" in pohyb_fields:
                kwargs["sarze_skladu"] = sarze

            PohybSkladu.objects.create(**kwargs)

    _safe_set_close_metadata(vydejka, user=user)
    vydejka.save(update_fields=_safe_update_fields(vydejka, ["uzavreny", "uzavren_at", "uzavrel"]))
    return True


@transaction.atomic
def uzavri_inventuru(inventura: Inventura, user=None) -> bool:
    """
    Inventura provede rozdílový pohyb a nastaví reálný stav skladu.
    """
    if getattr(inventura, "uzavreny", False):
        return False
    if getattr(inventura, "stornovano", False):
        raise ValidationError("Stornovanou inventuru nelze uzavřít.")
    over_doklad_mimo_uzavrene_obdobi(inventura)

    if inventura.sarze_polozky.exists():
        synchronizuj_surovinove_polozky_inventury(inventura)

    _validate_neprazdny_doklad(inventura, "Inventuru bez položek nelze uzavřít.")

    pohyb_fields = {f.name for f in PohybSkladu._meta.fields}

    if inventura.sarze_polozky.exists():
        for pol in inventura.sarze_polozky.select_related("surovina", "sarze_skladu").all():
            surovina = pol.surovina
            stav = get_or_create_stav_for_update(surovina)
            rozdil = pol.rozdil or Decimal("0")
            if rozdil == 0:
                continue

            sarze = pol.sarze_skladu
            if sarze is not None and sarze.pk:
                sarze = SarzeSkladu.objects.select_for_update().get(pk=sarze.pk)
                sarze.mnozstvi_zbyva = pol.fyzicky_stav or Decimal("0")
                sarze.stav = SarzeSkladu.STAV_ODEPSANA if sarze.mnozstvi_zbyva <= 0 else stav_sarze_podle_data(sarze)
                sarze.save(update_fields=["mnozstvi_zbyva", "stav"])
            else:
                if (pol.fyzicky_stav or Decimal("0")) <= 0:
                    continue
                sarze = SarzeSkladu.objects.create(
                    surovina=surovina,
                    sarze=pol.sarze,
                    typ_data_spotreby=pol.typ_data_spotreby,
                    datum_spotreby=pol.datum_spotreby,
                    mnozstvi_prijato=pol.fyzicky_stav or Decimal("0"),
                    mnozstvi_zbyva=pol.fyzicky_stav or Decimal("0"),
                    cena_za_jednotku=pol.cena_za_jednotku or surovina.prumerna_cena_za_jednotku,
                    stav=SarzeSkladu.STAV_POUZITELNA,
                    poznamka=f"Nově nalezená šarže z inventury #{inventura.id}",
                )
                sarze.stav = stav_sarze_podle_data(sarze)
                sarze.save(update_fields=["stav"])
                pol.sarze_skladu = sarze
                pol.je_nova_sarze = True
                pol.save(update_fields=["sarze_skladu", "je_nova_sarze"])

            stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + rozdil
            stav.save(update_fields=["mnozstvi"])

            typ = (
                _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS")
                if rozdil > 0 else
                _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")
            )
            kwargs = {
                "datum": _datum_pohybu_dokladu(inventura),
                "surovina": surovina,
                "typ": typ,
                "mnozstvi": abs(rozdil),
                "cena_za_jednotku": pol.cena_za_jednotku or sarze.cena_za_jednotku or surovina.prumerna_cena_za_jednotku,
                "poznamka": f"Šaržová inventura #{inventura.id} / šarže {sarze.sarze or sarze.id}",
            }
            if "inventura" in pohyb_fields:
                kwargs["inventura"] = inventura
            if "sarze_skladu" in pohyb_fields:
                kwargs["sarze_skladu"] = sarze
            PohybSkladu.objects.create(**kwargs)

        _safe_set_close_metadata(inventura, user=user)
        inventura.save(update_fields=_safe_update_fields(inventura, ["uzavreny", "uzavren_at", "uzavrel"]))
        return True

    for pol in inventura.polozky.select_related("surovina").all():
        surovina = pol.surovina
        stav = get_or_create_stav_for_update(surovina)

        aktualni = stav.mnozstvi or Decimal("0")
        fyzicky = pol.fyzicky_stav or Decimal("0")
        rozdil = fyzicky - aktualni

        if hasattr(pol, "stav_pred"):
            pol.stav_pred = aktualni
        if hasattr(pol, "rozdil"):
            pol.rozdil = rozdil

        pol.save(update_fields=_safe_update_fields(pol, ["stav_pred", "rozdil"]))

        if rozdil == 0:
            continue

        stav.mnozstvi = fyzicky
        stav.save(update_fields=["mnozstvi"])

        if rozdil > 0:
            sarze = vytvor_inventurni_sarzi(surovina, inventura, rozdil)
            kwargs = {
                "datum": _datum_pohybu_dokladu(inventura),
                "surovina": surovina,
                "typ": _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"),
                "mnozstvi": rozdil,
                "cena_za_jednotku": sarze.cena_za_jednotku,
                "poznamka": f"Inventurní přebytek #{inventura.id} / šarže {sarze.sarze}",
            }
            if "inventura" in pohyb_fields:
                kwargs["inventura"] = inventura
            if "sarze_skladu" in pohyb_fields:
                kwargs["sarze_skladu"] = sarze
            PohybSkladu.objects.create(**kwargs)
        else:
            cerpani_sarzi = odeber_ze_sarzi_fefo(surovina, abs(rozdil))
            for sarze, odebrano in cerpani_sarzi:
                kwargs = {
                    "datum": _datum_pohybu_dokladu(inventura),
                    "surovina": surovina,
                    "typ": _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS"),
                    "mnozstvi": odebrano,
                    "cena_za_jednotku": sarze.cena_za_jednotku or surovina.prumerna_cena_za_jednotku,
                    "poznamka": f"Inventurní manko #{inventura.id} / šarže {sarze.sarze or sarze.id}",
                }
                if "inventura" in pohyb_fields:
                    kwargs["inventura"] = inventura
                if "sarze_skladu" in pohyb_fields:
                    kwargs["sarze_skladu"] = sarze
                PohybSkladu.objects.create(**kwargs)

    _safe_set_close_metadata(inventura, user=user)
    inventura.save(update_fields=_safe_update_fields(inventura, ["uzavreny", "uzavren_at", "uzavrel"]))
    return True


@transaction.atomic
def stornuj_prijem(prijem: PrijemSkladu, user=None, duvod="Storno z administrace") -> bool:
    """
    Storno příjemky vytvoří opačné výdejové pohyby a sníží sklad.
    Původní pohyby zůstávají zachované kvůli auditu.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(prijem):
        return False
    over_doklad_mimo_uzavrene_obdobi(prijem)

    for pohyb in prijem.pohyby.filter(typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM")).select_related("surovina", "sarze_skladu"):
        sarze = pohyb.sarze_skladu
        if sarze is None:
            sarze = (
                SarzeSkladu.objects
                .select_for_update()
                .filter(polozka_prijmu__prijem=prijem, surovina=pohyb.surovina)
                .order_by("id")
                .first()
            )
        elif sarze.pk:
            sarze = SarzeSkladu.objects.select_for_update().get(pk=sarze.pk)

        mnozstvi = pohyb.mnozstvi or Decimal("0")
        if sarze is not None:
            if (sarze.mnozstvi_zbyva or Decimal("0")) < mnozstvi:
                raise ValidationError(
                    f"Příjemku nelze stornovat: ze šarže '{sarze}' už bylo vydáno zboží."
                )
            sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or Decimal("0")) - mnozstvi
            sarze.stav = SarzeSkladu.STAV_ODEPSANA if sarze.mnozstvi_zbyva <= 0 else stav_sarze_podle_data(sarze)
            sarze.save(update_fields=["mnozstvi_zbyva", "stav"])

        stav = get_or_create_stav_for_update(pohyb.surovina)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            datum=_datum_pohybu_dokladu(prijem),
            surovina=pohyb.surovina,
            typ=_safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"),
            mnozstvi=mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            prijem=prijem,
            sarze_skladu=sarze,
            poznamka=f"Storno příjemky #{prijem.id}",
        )

    _safe_set_storno_metadata(prijem, duvod=duvod)
    prijem.save(update_fields=_safe_update_fields(prijem, ["stornovano", "stornovano_at", "stornovano_duvod"]))
    return True


@transaction.atomic
def stornuj_vydejku(vydejka: Vydejka, user=None, duvod="Storno z administrace") -> bool:
    """
    Storno výdejky vytvoří opačné příjmové pohyby a vrátí suroviny na sklad.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(vydejka):
        return False
    over_doklad_mimo_uzavrene_obdobi(vydejka)

    for pohyb in vydejka.pohyby.filter(typ=_safe_pohyb_typ("TYP_VYDEJ", "VYDEJ")).select_related("surovina", "sarze_skladu"):
        sarze = pohyb.sarze_skladu
        mnozstvi = pohyb.mnozstvi or Decimal("0")
        if sarze is not None and sarze.pk:
            sarze = SarzeSkladu.objects.select_for_update().get(pk=sarze.pk)
            sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or Decimal("0")) + mnozstvi
            sarze.stav = stav_sarze_podle_data(sarze)
            sarze.save(update_fields=["mnozstvi_zbyva", "stav"])

        stav = get_or_create_stav_for_update(pohyb.surovina)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + mnozstvi
        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            datum=_datum_pohybu_dokladu(vydejka),
            surovina=pohyb.surovina,
            typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"),
            mnozstvi=mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            vydejka=vydejka,
            sarze_skladu=sarze,
            poznamka=f"Storno výdejky #{vydejka.id}",
        )

    _safe_set_storno_metadata(vydejka, duvod=duvod)
    vydejka.save(update_fields=_safe_update_fields(vydejka, ["stornovano", "stornovano_at", "stornovano_duvod"]))
    return True


@transaction.atomic
def stornuj_inventuru(inventura: Inventura, user=None, duvod="Storno z administrace") -> bool:
    """
    Storno inventury provede opačné inventurní pohyby.
    Pozn.: inventura vrací rozdílový efekt, ne historický stav po případných dalších pohybech.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(inventura):
        return False
    over_doklad_mimo_uzavrene_obdobi(inventura)

    for pohyb in inventura.pohyby.filter(
        typ__in=[
            _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"),
            _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS"),
        ]
    ).select_related("surovina", "sarze_skladu"):
        stav = get_or_create_stav_for_update(pohyb.surovina)
        mnozstvi = pohyb.mnozstvi or Decimal("0")
        sarze = pohyb.sarze_skladu
        if sarze is not None and sarze.pk:
            sarze = SarzeSkladu.objects.select_for_update().get(pk=sarze.pk)

        if pohyb.typ == _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"):
            if sarze is not None:
                if (sarze.mnozstvi_zbyva or Decimal("0")) < mnozstvi:
                    raise ValidationError(
                        f"Inventuru nelze stornovat: inventurní šarže '{sarze}' už byla částečně vydána."
                    )
                sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or Decimal("0")) - mnozstvi
                sarze.stav = SarzeSkladu.STAV_ODEPSANA if sarze.mnozstvi_zbyva <= 0 else stav_sarze_podle_data(sarze)
                sarze.save(update_fields=["mnozstvi_zbyva", "stav"])
            stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
            storno_typ = _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")
        else:
            if sarze is not None:
                sarze.mnozstvi_zbyva = (sarze.mnozstvi_zbyva or Decimal("0")) + mnozstvi
                sarze.stav = stav_sarze_podle_data(sarze)
                sarze.save(update_fields=["mnozstvi_zbyva", "stav"])
            stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + mnozstvi
            storno_typ = _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS")

        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            datum=_datum_pohybu_dokladu(inventura),
            surovina=pohyb.surovina,
            typ=storno_typ,
            mnozstvi=mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            inventura=inventura,
            sarze_skladu=sarze,
            poznamka=f"Storno inventury #{inventura.id}",
        )

    _safe_set_storno_metadata(inventura, duvod=duvod)
    inventura.save(update_fields=_safe_update_fields(inventura, ["stornovano", "stornovano_at", "stornovano_duvod"]))
    return True


def objednavky_rekap_data(vydejka: Vydejka):
    """
    Datový základ pro admin rekap a PDF.
    Vrací:
      - porce_per_jidlo
      - jidla
      - detail komponent / surovin
    """
    order_items = get_order_items_for_vydejka(vydejka)

    porce_per_jidlo = defaultdict(Decimal)
    jidla = {}
    detail = {}

    for item in order_items:
        jidlo = item.menu_item.jidlo
        jidla[jidlo.id] = jidlo
        porce_per_jidlo[jidlo.id] += Decimal(item.quantity)

    for jidlo_id, pocet_porci in porce_per_jidlo.items():
        jidlo = jidla[jidlo_id]
        komponenty_data = []

        komponenty = (
            jidlo.komponenty_jidla
            .select_related("komponenta")
            .prefetch_related("komponenta__suroviny__surovina")
            .all()
        )

        if komponenty.exists():
            for vazba in komponenty:
                radky = []
                for pol in vazba.komponenta.suroviny.all():
                    na_porci = (pol.mnozstvi_na_porci or Decimal("0")) * (
                        vazba.mnozstvi_nasobek or Decimal("1")
                    )
                    celkem = na_porci * pocet_porci
                    radky.append({
                        "surovina": pol.surovina,
                        "na_porci": na_porci,
                        "celkem": celkem,
                    })

                komponenty_data.append({
                    "komponenta": vazba.komponenta,
                    "radky": radky,
                })
        else:
            # fallback na starou recepturu
            radky = []
            for pol in jidlo.receptura.select_related("surovina").all():
                na_porci = pol.mnozstvi_na_porci or Decimal("0")
                celkem = na_porci * pocet_porci
                radky.append({
                    "surovina": pol.surovina,
                    "na_porci": na_porci,
                    "celkem": celkem,
                })

            komponenty_data.append({
                "komponenta": None,
                "radky": radky,
            })

        detail[jidlo_id] = komponenty_data

    return porce_per_jidlo, jidla, detail


# =========================================================
# KOMPATIBILNÍ STUBY PRO ADMIN / REPORTY
# =========================================================
# Tyhle funkce přidej zatím proto, aby Django NABĚHLO
# a prošly migrace. Později je můžeš nahradit reálnou logikou.

def spocitej_spotrebu_sk_mesic(rok, mesic, stravovaci_skupina=None):
    """
    Spotřeba pro spotřební koš v gramech podle objednaných jídel.
    """
    import calendar
    from datetime import date

    date_from = date(int(rok), int(mesic), 1)
    date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])
    return spocitej_spotrebu_sk_obdobi(date_from, date_to, stravovaci_skupina)


def spocitej_spotrebu_sk_obdobi(date_from, date_to, stravovaci_skupina=None):
    return spocitej_spotrebu_sk_z_vydejek(date_from, date_to, stravovaci_skupina)


def spocitej_planovanou_spotrebu_sk_obdobi(date_from, date_to, stravovaci_skupina=None):
    qs = (
        OrderItem.objects
        .select_related("menu_item__jidlo", "order__user")
        .filter(order__datum_vydeje__gte=date_from, order__datum_vydeje__lte=date_to)
    )
    if stravovaci_skupina:
        qs = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)

    spotreba = defaultdict(lambda: Decimal("0"))

    for item in qs:
        jidlo_spotreba = spocitej_spotrebu_jidla(item.menu_item.jidlo, Decimal(item.quantity))
        suroviny = Surovina.objects.filter(id__in=jidlo_spotreba.keys())
        suroviny_map = {s.id: s for s in suroviny}
        for surovina_id, mnozstvi in jidlo_spotreba.items():
            surovina = suroviny_map.get(surovina_id)
            if not surovina:
                continue
            skupina = normalizuj_skupinu_spotrebniho_kose(surovina.skupina_sk)
            spotreba[skupina] += spocitej_zapocitatelnou_hmotnost_sk(surovina, mnozstvi)

    return spotreba


def spocitej_spotrebu_sk_z_vydejek(date_from, date_to, stravovaci_skupina=None):
    order_items, _ = get_order_items_a_vydejky_pro_spotrebni_kos_obdobi(
        date_from,
        date_to,
        stravovaci_skupina,
    )
    return _spotreba_order_items_pro_spotrebni_kos(order_items)


def spocitej_stravnikodny_z_uzavrenych_vydejek(date_from, date_to, stravovaci_skupina=None):
    order_items, _ = get_order_items_a_vydejky_pro_spotrebni_kos_obdobi(
        date_from,
        date_to,
        stravovaci_skupina,
    )
    return Decimal(order_items.aggregate(celkem=Sum("quantity"))["celkem"] or 0)


def spocitej_souhrn_spotrebniho_kose(date_from, date_to, stravovaci_skupina=None):
    order_items, vydejka_ids = get_order_items_a_vydejky_pro_spotrebni_kos_obdobi(
        date_from,
        date_to,
        stravovaci_skupina,
    )
    pocet_jidel = Decimal(order_items.aggregate(celkem=Sum("quantity"))["celkem"] or 0)
    stravnici_ids = set(order_items.values_list("order__user_id", flat=True).distinct())

    return {
        "pocet_jidel": pocet_jidel,
        "pocet_stravniku": len(stravnici_ids),
        "pocet_vydejek": len(vydejka_ids),
    }


def spocitej_normy_sk_obdobi(date_from, date_to, stravovaci_skupina=None):
    normy = defaultdict(lambda: Decimal("0"))
    vydejky = (
        Vydejka.objects
        .filter(
            datum__gte=date_from,
            datum__lte=date_to,
            uzavreny=True,
            stornovano=False,
        )
        .select_related("stravovaci_skupina")
        .order_by("datum", "id")
    )

    zpracovane_order_items = set()
    for vydejka in vydejky:
        fallback_na_datum = not (
            stravovaci_skupina and not vydejka.stravovaci_skupina_id
        )
        order_items = get_order_items_pro_vydejku_pro_spotrebni_kos(
            vydejka,
            stravovaci_skupina,
            fallback_na_datum=fallback_na_datum,
        ).exclude(id__in=zpracovane_order_items)
        order_item_ids = set(order_items.values_list("id", flat=True))
        if not order_item_ids:
            continue
        zpracovane_order_items.update(order_item_ids)

        vekova_kategorie = vekova_kategorie_pro_stravovaci_skupinu(
            stravovaci_skupina or vydejka.stravovaci_skupina
        )
        typ_jidla = typ_normy_pro_vydejku(vydejka)
        pocet_stravniku = Decimal(order_items.aggregate(celkem=Sum("quantity"))["celkem"] or 0)
        normy_qs = NormaSpotrebnihoKose.objects.filter(
            vekova_kategorie=vekova_kategorie,
            typ_jidla=typ_jidla,
        ).filter(stravovaci_skupina__isnull=True)
        for norma in normy_qs:
            normy[norma.skupina_sk] += (norma.norma_g_den or Decimal("0")) * pocet_stravniku

    return normy


def stav_plneni_tolerance(skutecnost_pct, min_pct, max_pct):
    if min_pct is not None and skutecnost_pct < min_pct:
        return "pod_limitem"
    if max_pct is not None and skutecnost_pct > max_pct:
        return "nad_limitem"
    return "ok"


def priprav_radky_spotrebi_kos_tabulka(
    rok,
    mesic,
    stravovaci_skupina=None,
    pocet_stravniku=0,
    date_from=None,
    date_to=None,
):
    """
    Řádky spotřebního koše podle normy a reálné objednané spotřeby.
    """
    if date_from is None or date_to is None:
        import calendar
        from datetime import date

        date_from = date(int(rok), int(mesic), 1)
        date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])

    spotreba = spocitej_spotrebu_sk_obdobi(date_from, date_to, stravovaci_skupina)
    normy = spocitej_normy_sk_obdobi(date_from, date_to, stravovaci_skupina)

    skupiny = sorted(set(spotreba.keys()) | set(normy.keys()))
    rows = []
    for skupina in skupiny:
        if skupina in ("NEZARAZENO", Surovina.SK_NEZAPOCITAVA_SE):
            continue
        norma_g = normy.get(skupina, Decimal("0"))
        skutecnost_g = spotreba.get(skupina, Decimal("0"))
        rozdil_g = skutecnost_g - norma_g
        skutecnost_pct = Decimal("0")
        if norma_g:
            skutecnost_pct = (skutecnost_g / norma_g) * Decimal("100")
        min_pct, max_pct = tolerance_pro_skupinu(skupina, stravovaci_skupina)

        rows.append({
            "skupina_kod": skupina,
            "skupina_nazev": nazev_skupiny_spotrebniho_kose(skupina),
            "norma_g": norma_g,
            "skutecnost_g": skutecnost_g,
            "rozdil_g": rozdil_g,
            "skutecnost_pct": skutecnost_pct,
            "min_pct": min_pct,
            "max_pct": max_pct,
            "stav": stav_plneni_tolerance(skutecnost_pct, min_pct, max_pct) if norma_g else "bez_normy",
        })

    return rows


def spocitej_naklady_mesic(rok, mesic, stravovaci_skupina=None):
    """
    Souhrn nákladů skladu za měsíc.
    """
    qs = PohybSkladu.objects.filter(datum__year=rok, datum__month=mesic).select_related(
        "surovina", "vydejka"
    )
    if stravovaci_skupina:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina) | PohybSkladu.objects.filter(
            datum__year=rok,
            datum__month=mesic,
            prijem__isnull=False,
        ).select_related("surovina", "vydejka")

    prijmy_sum = Decimal("0")
    vydeje_sum = Decimal("0")
    odpisy_expirace_sum = Decimal("0")
    inventura_plus_sum = Decimal("0")
    inventura_minus_sum = Decimal("0")

    for pohyb in qs:
        cena = pohyb.cena_za_jednotku or Decimal("0")
        hodnota = (pohyb.mnozstvi or Decimal("0")) * cena
        je_storno = pohyb.poznamka.lower().startswith("storno")

        if pohyb.typ == _safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"):
            if pohyb.vydejka_id and je_storno:
                vydeje_sum -= hodnota
            elif pohyb.prijem_id:
                prijmy_sum += hodnota
        elif pohyb.typ == _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"):
            if pohyb.prijem_id and je_storno:
                prijmy_sum -= hodnota
            elif pohyb.vydejka_id:
                vydeje_sum += hodnota
        elif pohyb.typ == _safe_pohyb_typ("TYP_EXPIRACE_MINUS", "EXPIRACE_MINUS"):
            if pohyb.odpis_expirace_id:
                odpisy_expirace_sum += hodnota
        elif pohyb.typ == _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"):
            inventura_plus_sum += hodnota
        elif pohyb.typ == _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS"):
            inventura_minus_sum += hodnota

    import calendar
    from datetime import date

    date_from = date(int(rok), int(mesic), 1)
    date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])
    pocet_porci = spocitej_stravnikodny_obdobi(date_from, date_to, stravovaci_skupina)
    cena_na_porci = vydeje_sum / pocet_porci if pocet_porci else Decimal("0")
    naklady_celkem = vydeje_sum + odpisy_expirace_sum

    return {
        "prijmy": prijmy_sum,
        "vydeje": vydeje_sum,
        "odpisy_expirace": odpisy_expirace_sum,
        "inventura_plus": inventura_plus_sum,
        "inventura_minus": inventura_minus_sum,
        "naklady_celkem": naklady_celkem,
        "bilance": naklady_celkem,
        "pocet_porci": pocet_porci,
        "cena_na_porci": cena_na_porci,
    }


def priprav_naklady_podle_skupin_sk(rok, mesic, stravovaci_skupina=None):
    """
    Náklady výdejů podle skupin spotřebního koše.
    """
    qs = PohybSkladu.objects.filter(
        datum__year=rok,
        datum__month=mesic,
        vydejka__isnull=False,
    ).select_related("surovina", "vydejka")
    if stravovaci_skupina:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    naklady = defaultdict(lambda: Decimal("0"))
    for pohyb in qs:
        cena = pohyb.cena_za_jednotku or Decimal("0")
        hodnota = (pohyb.mnozstvi or Decimal("0")) * cena
        skupina = normalizuj_skupinu_spotrebniho_kose(pohyb.surovina.skupina_sk)
        je_storno = pohyb.poznamka.lower().startswith("storno")

        if pohyb.typ == _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"):
            naklady[skupina] += hodnota
        elif pohyb.typ == _safe_pohyb_typ("TYP_PRIJEM", "PRIJEM") and je_storno:
            naklady[skupina] -= hodnota

    return [
        {
            "skupina_kod": skupina,
            "skupina_nazev": nazev_skupiny_spotrebniho_kose(skupina),
            "naklady": hodnota,
        }
        for skupina, hodnota in sorted(naklady.items())
    ]


def spocitej_podil_masnych_vyrobku(date_from, date_to, stravovaci_skupina=None):
    celkem_g, masne_g, _, _ = _spocitej_specialni_podily(date_from, date_to, stravovaci_skupina)
    return {
        "celkem_g": celkem_g,
        "masne_vyrobky_g": masne_g,
        "podil_pct": (masne_g / celkem_g * Decimal("100")) if celkem_g else Decimal("0"),
    }


def spocitej_podil_bio(date_from, date_to, stravovaci_skupina=None):
    celkem_g, _, bio_g, _ = _spocitej_specialni_podily(date_from, date_to, stravovaci_skupina)
    return {
        "celkem_g": celkem_g,
        "bio_g": bio_g,
        "podil_pct": (bio_g / celkem_g * Decimal("100")) if celkem_g else Decimal("0"),
    }


def spocitej_volny_cukr(date_from, date_to, stravovaci_skupina=None):
    _, _, _, volny_cukr_g = _spocitej_specialni_podily(date_from, date_to, stravovaci_skupina)
    return volny_cukr_g


def _spocitej_specialni_podily(date_from, date_to, stravovaci_skupina=None):
    qs = (
        PolozkaVydejky.objects
        .select_related("surovina", "vydejka")
        .filter(
            vydejka__datum__gte=date_from,
            vydejka__datum__lte=date_to,
            vydejka__uzavreny=True,
            vydejka__stornovano=False,
        )
    )
    if stravovaci_skupina:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    celkem_g = Decimal("0")
    masne_g = Decimal("0")
    bio_g = Decimal("0")
    volny_cukr_g = Decimal("0")

    for polozka in qs:
        surovina = polozka.surovina
        gramy = preved_na_gramy(surovina, polozka.mnozstvi)
        celkem_g += gramy
        if surovina.je_masny_vyrobek:
            masne_g += gramy
        if surovina.je_bio:
            bio_g += gramy
        if surovina.volny_cukr_na_100g:
            volny_cukr_g += gramy * surovina.volny_cukr_na_100g / Decimal("100")

    return celkem_g, masne_g, bio_g, volny_cukr_g


def spocitej_legislativni_ukazatele_sk(date_from, date_to, stravovaci_skupina=None):
    qs = (
        PolozkaVydejky.objects
        .select_related("surovina", "vydejka")
        .filter(
            vydejka__datum__gte=date_from,
            vydejka__datum__lte=date_to,
            vydejka__uzavreny=True,
            vydejka__stornovano=False,
        )
    )
    if stravovaci_skupina:
        qs = qs.filter(vydejka__stravovaci_skupina=stravovaci_skupina)

    celkem_nakoupeno_g = Decimal("0")
    bio_g = Decimal("0")
    sezonni_g = Decimal("0")
    zelenina_ovoce_g = Decimal("0")
    sterilovane_g = Decimal("0")
    maso_g = Decimal("0")
    masne_vyrobky_g = Decimal("0")
    rostlinne_tuky_g = Decimal("0")
    zivocisne_tuky_g = Decimal("0")
    zakazane = []

    for polozka in qs:
        surovina = polozka.surovina
        skupina = normalizuj_skupinu_spotrebniho_kose(surovina.skupina_sk)
        gramy = preved_na_gramy(surovina, polozka.mnozstvi)
        celkem_nakoupeno_g += gramy
        if surovina.je_bio:
            bio_g += gramy
        if surovina.je_sezonni and skupina in (Surovina.SK_ZELENINA_OVOCE, Surovina.SK_BRAMBORY):
            sezonni_g += gramy
        if skupina == Surovina.SK_ZELENINA_OVOCE:
            zelenina_ovoce_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
            if surovina.je_sterilovana_nebo_kompot:
                sterilovane_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
        if skupina == Surovina.SK_MASO:
            maso_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
            if surovina.je_masny_vyrobek:
                masne_vyrobky_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
        if skupina == Surovina.SK_TUKY:
            if surovina.je_rostlinny_tuk:
                rostlinne_tuky_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
            if surovina.je_zivocisny_tuk:
                zivocisne_tuky_g += spocitej_zapocitatelnou_hmotnost_sk(surovina, polozka.mnozstvi)
        if surovina.je_zakazano_pro_skolni_stravovani:
            zakazane.append(surovina.nazev)

    return {
        "bio_pct": (bio_g / celkem_nakoupeno_g * Decimal("100")) if celkem_nakoupeno_g else Decimal("0"),
        "sezonni_pct": (sezonni_g / celkem_nakoupeno_g * Decimal("100")) if celkem_nakoupeno_g else Decimal("0"),
        "sterilovane_pct": (sterilovane_g / zelenina_ovoce_g * Decimal("100")) if zelenina_ovoce_g else Decimal("0"),
        "masne_vyrobky_pct": (masne_vyrobky_g / maso_g * Decimal("100")) if maso_g else Decimal("0"),
        "pomer_rostlinne_zivocisne_tuky": (rostlinne_tuky_g / zivocisne_tuky_g) if zivocisne_tuky_g else None,
        "zakazane_suroviny": sorted(set(zakazane)),
    }


def zkontroluj_jidelnicek_sk(date_from, date_to, stravovaci_skupina=None):
    qs = (
        OrderItem.objects
        .select_related("menu_item__jidlo", "menu_item__druh_jidla", "order__user")
        .filter(order__datum_vydeje__gte=date_from, order__datum_vydeje__lte=date_to)
    )
    if stravovaci_skupina:
        qs = qs.filter(order__user__stravovaci_skupina=stravovaci_skupina)

    dny_s_rybou = set()
    dny_sladky_obed = set()
    slazene_napoje = []
    dezerty_s_cukrem = 0
    jemne_pecivo = 0

    for item in qs:
        jidlo = item.menu_item.jidlo
        datum = item.order.datum_vydeje
        druh = (item.menu_item.druh_jidla.nazev or "").lower()
        if getattr(jidlo, "sk_rybi_pokrm", False):
            dny_s_rybou.add(datum)
        if getattr(jidlo, "sk_sladky_pokrm", False) and "ob" in druh:
            dny_sladky_obed.add(datum)
        if getattr(jidlo, "sk_slazeny_napoj", False):
            slazene_napoje.append(f"{datum.strftime('%d.%m.%Y')}: {jidlo.nazev}")
        if getattr(jidlo, "sk_dezert_s_volnym_cukrem", False):
            dezerty_s_cukrem += 1
        if getattr(jidlo, "sk_jemne_pecivo", False):
            jemne_pecivo += 1

    varovani = []
    if len(dny_s_rybou) < 2:
        varovani.append("Rybí pokrm je v období méně než 2x měsíčně.")
    if len(dny_sladky_obed) > 2:
        varovani.append("Sladký hlavní chod k obědu je nabízen častěji než 1x za 2 týdny.")
    if slazene_napoje:
        varovani.append("V jídelníčku jsou nápoje s volným cukrem: " + ", ".join(slazene_napoje[:5]))
    if dezerty_s_cukrem > 1:
        varovani.append("Dezert s volným cukrem je v měsíci použit více než 1x.")
    if jemne_pecivo > 2:
        varovani.append("Jemné pečivo je u obědů použito více než 2x měsíčně.")

    return {
        "pocet_dnu_s_rybou": len(dny_s_rybou),
        "pocet_sladkych_obedu": len(dny_sladky_obed),
        "varovani": varovani,
    }
