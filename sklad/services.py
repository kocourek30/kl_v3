from collections import defaultdict
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
    Vydejka,
    PolozkaVydejky,
    Inventura,
    NormaSpotrebnihoKose,
    ToleranceSpotrebnihoKose,
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


def _safe_set_storno_metadata(obj):
    if hasattr(obj, "storno_meta") and callable(getattr(obj, "storno_meta")):
        obj.storno_meta()
        return

    if hasattr(obj, "stornovano"):
        obj.stornovano = True
    if hasattr(obj, "stornovano_at"):
        obj.stornovano_at = timezone.now()


def _safe_pohyb_typ(attr_name, fallback_value):
    """
    Vrátí konstantu typu pohybu, pokud existuje, jinak fallback string.
    """
    return getattr(PohybSkladu, attr_name, fallback_value)


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
    ):
        return Decimal("-1")
    return Decimal("0")


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

        PohybSkladu.objects.create(
            surovina=surovina,
            typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"),
            mnozstvi=prijate_mnozstvi,
            cena_za_jednotku=prijata_cena if "cena_za_jednotku" in [f.name for f in PohybSkladu._meta.fields] else None,
            prijem=prijem if "prijem" in [f.name for f in PohybSkladu._meta.fields] else None,
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

    _validate_neprazdny_doklad(vydejka, "Výdejku bez položek nelze uzavřít.")

    for pol in vydejka.polozky.select_related("surovina").all():
        surovina = pol.surovina
        stav = get_or_create_stav_for_update(surovina)

        mnozstvi = pol.mnozstvi or Decimal("0")
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
        stav.save(update_fields=["mnozstvi"])

        kwargs = {
            "surovina": surovina,
            "typ": _safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"),
            "mnozstvi": mnozstvi,
            "poznamka": f"Výdejka #{vydejka.id}",
        }

        pohyb_fields = {f.name for f in PohybSkladu._meta.fields}
        if "cena_za_jednotku" in pohyb_fields:
            kwargs["cena_za_jednotku"] = getattr(surovina, "prumerna_cena_za_jednotku", None)
        if "vydejka" in pohyb_fields:
            kwargs["vydejka"] = vydejka

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

    _validate_neprazdny_doklad(inventura, "Inventuru bez položek nelze uzavřít.")

    pohyb_fields = {f.name for f in PohybSkladu._meta.fields}

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

        kwargs = {
            "surovina": surovina,
            "typ": (
                _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS")
                if rozdil > 0 else
                _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")
            ),
            "mnozstvi": abs(rozdil),
            "poznamka": f"Inventura #{inventura.id}",
        }

        if "inventura" in pohyb_fields:
            kwargs["inventura"] = inventura

        PohybSkladu.objects.create(**kwargs)

    _safe_set_close_metadata(inventura, user=user)
    inventura.save(update_fields=_safe_update_fields(inventura, ["uzavreny", "uzavren_at", "uzavrel"]))
    return True


@transaction.atomic
def stornuj_prijem(prijem: PrijemSkladu, user=None) -> bool:
    """
    Storno příjemky vytvoří opačné výdejové pohyby a sníží sklad.
    Původní pohyby zůstávají zachované kvůli auditu.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(prijem):
        return False

    for pohyb in prijem.pohyby.filter(typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM")).select_related("surovina"):
        stav = get_or_create_stav_for_update(pohyb.surovina)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - (pohyb.mnozstvi or Decimal("0"))
        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            surovina=pohyb.surovina,
            typ=_safe_pohyb_typ("TYP_VYDEJ", "VYDEJ"),
            mnozstvi=pohyb.mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            prijem=prijem,
            poznamka=f"Storno příjemky #{prijem.id}",
        )

    _safe_set_storno_metadata(prijem)
    prijem.save(update_fields=_safe_update_fields(prijem, ["stornovano", "stornovano_at"]))
    return True


@transaction.atomic
def stornuj_vydejku(vydejka: Vydejka, user=None) -> bool:
    """
    Storno výdejky vytvoří opačné příjmové pohyby a vrátí suroviny na sklad.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(vydejka):
        return False

    for pohyb in vydejka.pohyby.filter(typ=_safe_pohyb_typ("TYP_VYDEJ", "VYDEJ")).select_related("surovina"):
        stav = get_or_create_stav_for_update(pohyb.surovina)
        stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + (pohyb.mnozstvi or Decimal("0"))
        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            surovina=pohyb.surovina,
            typ=_safe_pohyb_typ("TYP_PRIJEM", "PRIJEM"),
            mnozstvi=pohyb.mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            vydejka=vydejka,
            poznamka=f"Storno výdejky #{vydejka.id}",
        )

    _safe_set_storno_metadata(vydejka)
    vydejka.save(update_fields=_safe_update_fields(vydejka, ["stornovano", "stornovano_at"]))
    return True


@transaction.atomic
def stornuj_inventuru(inventura: Inventura, user=None) -> bool:
    """
    Storno inventury provede opačné inventurní pohyby.
    Pozn.: inventura vrací rozdílový efekt, ne historický stav po případných dalších pohybech.
    """
    if not _doklad_musi_byt_uzavreny_a_nestornovany(inventura):
        return False

    for pohyb in inventura.pohyby.filter(
        typ__in=[
            _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"),
            _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS"),
        ]
    ).select_related("surovina"):
        stav = get_or_create_stav_for_update(pohyb.surovina)
        mnozstvi = pohyb.mnozstvi or Decimal("0")

        if pohyb.typ == _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS"):
            stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
            storno_typ = _safe_pohyb_typ("TYP_INVENTURA_MINUS", "INVENTURA_MINUS")
        else:
            stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + mnozstvi
            storno_typ = _safe_pohyb_typ("TYP_INVENTURA_PLUS", "INVENTURA_PLUS")

        stav.save(update_fields=["mnozstvi"])

        PohybSkladu.objects.create(
            surovina=pohyb.surovina,
            typ=storno_typ,
            mnozstvi=mnozstvi,
            cena_za_jednotku=pohyb.cena_za_jednotku,
            inventura=inventura,
            poznamka=f"Storno inventury #{inventura.id}",
        )

    _safe_set_storno_metadata(inventura)
    inventura.save(update_fields=_safe_update_fields(inventura, ["stornovano", "stornovano_at"]))
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

    import calendar
    from datetime import date

    date_from = date(int(rok), int(mesic), 1)
    date_to = date(int(rok), int(mesic), calendar.monthrange(int(rok), int(mesic))[1])
    pocet_porci = spocitej_stravnikodny_obdobi(date_from, date_to, stravovaci_skupina)
    cena_na_porci = vydeje_sum / pocet_porci if pocet_porci else Decimal("0")

    return {
        "prijmy": prijmy_sum,
        "vydeje": vydeje_sum,
        "bilance": vydeje_sum,
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
