from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
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


def _safe_pohyb_typ(attr_name, fallback_value):
    """
    Vrátí konstantu typu pohybu, pokud existuje, jinak fallback string.
    """
    return getattr(PohybSkladu, attr_name, fallback_value)


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


def validace_surovin_pro_sk():
    """
    Kontrola kvality dat pro spotřební koš.
    """
    chyby = []

    for s in Surovina.objects.all().order_by("nazev"):
        if hasattr(s, "skupina_sk") and not s.skupina_sk:
            chyby.append(f"Surovina '{s.nazev}' nemá vyplněnou skupinu spotřebního koše.")
        if getattr(s, "jednotka", None) == "ks" and not getattr(s, "hmotnost_ks_g", None):
            chyby.append(f"Surovina '{s.nazev}' je vedena v ks a nemá hmotnost 1 ks.")
        if getattr(s, "skupina_sk", None) and getattr(s, "koeficient_sk", None) is None:
            chyby.append(f"Surovina '{s.nazev}' má skupinu SK, ale nemá koeficient.")
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
    vydejka, created = Vydejka.objects.get_or_create(
        datum=datum,
        stravovaci_skupina=stravovaci_skupina,
        typ_stravy=typ_stravy,
        defaults={
            "popis": "Generováno z objednávek",
            "uzavreny": False,
        },
    )

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


@transaction.atomic
def uzavri_prijem(prijem: PrijemSkladu, user=None) -> bool:
    """
    Idempotentní uzavření příjemky.
    Kompatibilní i se starší verzí modelů.
    """
    if getattr(prijem, "uzavreny", False):
        return False

    for pol in prijem.polozky.select_related("surovina").all():
        surovina = pol.surovina
        stav = get_or_create_stav_for_update(surovina)

        stare_mnozstvi = stav.mnozstvi or Decimal("0")
        stara_cena = getattr(surovina, "prumerna_cena_za_jednotku", None) or Decimal("0")

        prijate_mnozstvi = pol.mnozstvi or Decimal("0")
        prijata_cena = getattr(pol, "jednotkova_cena", None) or Decimal("0")

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
    Dočasná kompatibilní funkce.
    """
    return defaultdict(lambda: Decimal("0"))


def priprav_radky_spotrebi_kos_tabulka(
    rok,
    mesic,
    stravovaci_skupina=None,
    pocet_stravniku=0,
    date_from=None,
    date_to=None,
):
    """
    Dočasná kompatibilní funkce pro admin view spotřebního koše.
    Pokud chceš, později ji nahradíme skutečným výpočtem.
    """
    return []


def spocitej_naklady_mesic(rok, mesic, stravovaci_skupina=None):
    """
    Dočasná kompatibilní funkce pro report nákladů.
    """
    return Decimal("0")


def priprav_naklady_podle_skupin_sk(rok, mesic, stravovaci_skupina=None):
    """
    Dočasná kompatibilní funkce.
    """
    return []


def spocitej_podil_masnych_vyrobku(date_from, date_to, stravovaci_skupina=None):
    return {
        "celkem_g": Decimal("0"),
        "masne_vyrobky_g": Decimal("0"),
        "podil_pct": Decimal("0"),
    }


def spocitej_podil_bio(date_from, date_to, stravovaci_skupina=None):
    return {
        "celkem_g": Decimal("0"),
        "bio_g": Decimal("0"),
        "podil_pct": Decimal("0"),
    }


def spocitej_volny_cukr(date_from, date_to, stravovaci_skupina=None):
    return Decimal("0")
