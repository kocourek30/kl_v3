from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from sklad.models import PohybSkladu, StavSkladu
from users.models import Vklad
from dotace.models import SkupinoveNastaveni

from .models import PLUPolozka, PokladniDoklad, PokladniPolozka, PokladniUzaverka


HALER = Decimal("0.01")
MNOZSTVI_KROK = Decimal("0.001")


def decimal_z_postu(value, default="1"):
    try:
        return Decimal(str(value or default).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError("Zadané množství není platné číslo.")


def _q2(value):
    return Decimal(value or 0).quantize(HALER)


def _q3(value):
    return Decimal(value or 0).quantize(MNOZSTVI_KROK)


def konto_nastaveni_uzivatele(uzivatel):
    """
    Vrací limit konta, do kterého může uživatel klesnout:
    - bez debetu je limit 0.00
    - s debetem je limit záporný (např. -1500.00)
    """
    skupina = uzivatel.groups.first()
    if not skupina:
        return {
            "cerpani_debit": False,
            "nutnost_dobit": False,
            "debit_limit": Decimal("0.00"),
            "minimalni_zustatek": Decimal("0.00"),
        }

    try:
        nastaveni = skupina.nastaveni
    except SkupinoveNastaveni.DoesNotExist:
        return {
            "cerpani_debit": False,
            "nutnost_dobit": False,
            "debit_limit": Decimal("0.00"),
            "minimalni_zustatek": Decimal("0.00"),
        }

    debit_limit = Decimal(nastaveni.debit_limit or 0).quantize(HALER)
    if debit_limit > 0:
        debit_limit = -debit_limit

    minimalni_zustatek = debit_limit if nastaveni.cerpani_debit else Decimal("0.00")
    if nastaveni.nutnost_dobit:
        minimalni_zustatek = Decimal("0.00")

    return {
        "cerpani_debit": bool(nastaveni.cerpani_debit),
        "nutnost_dobit": bool(nastaveni.nutnost_dobit),
        "debit_limit": debit_limit,
        "minimalni_zustatek": minimalni_zustatek,
    }


def vypocitej_dph(cena_s_dph, mnozstvi, sazba):
    cena_s_dph = Decimal(cena_s_dph or 0)
    mnozstvi = Decimal(mnozstvi or 0)
    sazba = Decimal(sazba or 0)
    celkem = _q2(cena_s_dph * mnozstvi)
    koeficient = Decimal("1") + (sazba / Decimal("100"))
    zaklad = _q2(celkem / koeficient)
    dph = _q2(celkem - zaklad)
    return zaklad, dph, celkem


def vytvor_doklad(pokladna, obsluha, zakaznik=None):
    return PokladniDoklad.objects.create(
        pokladna=pokladna,
        obsluha=obsluha,
        zakaznik=zakaznik,
    )


def _over_rozpracovany(doklad):
    if not doklad.je_rozpracovany:
        raise ValidationError("Upravovat lze pouze rozpracovaný pokladní doklad.")


@transaction.atomic
def pridej_polozku(doklad, plu, mnozstvi=Decimal("1")):
    doklad = PokladniDoklad.objects.select_for_update().get(pk=doklad.pk)
    _over_rozpracovany(doklad)

    mnozstvi = _q3(mnozstvi)
    if mnozstvi <= 0:
        raise ValidationError("Množství musí být větší než nula.")

    plu = PLUPolozka.objects.select_related("dph_skupina", "surovina").get(pk=plu.pk)
    sazba = plu.dph_skupina.sazba
    zaklad, dph, celkem = vypocitej_dph(plu.cena, mnozstvi, sazba)
    jednotka = plu.surovina.jednotka if plu.surovina_id else "ks"

    polozka = PokladniPolozka.objects.create(
        doklad=doklad,
        plu=plu,
        nazev_snapshot=plu.nazev,
        mnozstvi=mnozstvi,
        jednotka_text=jednotka,
        cena_jednotkova=plu.cena,
        dph_sazba=sazba,
        zaklad_dph=zaklad,
        castka_dph=dph,
        castka_celkem=celkem,
    )
    doklad.prepocitej_sumy()
    return polozka


@transaction.atomic
def smaz_polozku(doklad, polozka_id):
    doklad = PokladniDoklad.objects.select_for_update().get(pk=doklad.pk)
    _over_rozpracovany(doklad)
    polozka = doklad.polozky.filter(pk=polozka_id).first()
    if not polozka:
        return False
    polozka.delete()
    doklad.prepocitej_sumy()
    return True


def _cislo_dokladu(doklad):
    local_date = timezone.localtime(doklad.datum).date()
    return f"PKD-{local_date:%Y%m%d}-{doklad.id:06d}"


def _vytvor_konto_pohyb(doklad):
    if doklad.zpusob_platby != PokladniDoklad.PLATBA_KONTO:
        return None
    if not doklad.zakaznik_id:
        raise ValidationError("Platba kontem vyžaduje vybraného zákazníka.")
    if doklad.konto_pohyb_id:
        return doklad.konto_pohyb

    stav = doklad.zakaznik.aktualni_zustatek
    konto_nastaveni = konto_nastaveni_uzivatele(doklad.zakaznik)
    limit = konto_nastaveni["minimalni_zustatek"]
    novy_zustatek = stav - doklad.celkem_s_dph
    if novy_zustatek < limit:
        chybi = _q2(limit - novy_zustatek)
        raise ValidationError(
            f"Překročen povolený limit konta. Chybí minimálně {chybi} Kč."
        )

    return Vklad.objects.create(
        uzivatel=doklad.zakaznik,
        castka=-_q2(doklad.celkem_s_dph),
        poznamka=f"Čerpání konta pokladním dokladem {_cislo_dokladu(doklad)}",
    )


def _odepis_skladovou_polozku(polozka):
    if not polozka.plu.surovina_id or polozka.skladovy_pohyb_id:
        return None

    surovina = polozka.plu.surovina
    mnozstvi = polozka.mnozstvi or Decimal("0")
    stav, _ = StavSkladu.objects.select_for_update().get_or_create(
        surovina=surovina,
        defaults={"mnozstvi": Decimal("0"), "min_mnozstvi": Decimal("0")},
    )
    stav.mnozstvi = (stav.mnozstvi or Decimal("0")) - mnozstvi
    stav.save(update_fields=["mnozstvi"])

    pohyb = PohybSkladu.objects.create(
        datum=timezone.now(),
        surovina=surovina,
        typ=PohybSkladu.TYP_VYDEJ,
        mnozstvi=mnozstvi,
        cena_za_jednotku=surovina.prumerna_cena_za_jednotku,
        poznamka=f"Pokladní prodej {polozka.doklad.cislo_dokladu or polozka.doklad_id}",
    )
    polozka.skladovy_pohyb = pohyb
    polozka.save(update_fields=["skladovy_pohyb"])
    return pohyb


@transaction.atomic
def uzavri_doklad(doklad, zpusob_platby, user=None):
    doklad = (
        PokladniDoklad.objects
        .select_for_update()
        .select_related("zakaznik", "pokladna")
        .get(pk=doklad.pk)
    )
    if doklad.je_uzavreny:
        return doklad
    _over_rozpracovany(doklad)
    if not doklad.polozky.exists():
        raise ValidationError("Prázdnou účtenku nelze uzavřít.")
    if zpusob_platby not in dict(PokladniDoklad.ZPUSOBY_PLATBY):
        raise ValidationError("Vyber platný způsob platby.")

    doklad.zpusob_platby = zpusob_platby
    doklad.cislo_dokladu = doklad.cislo_dokladu or _cislo_dokladu(doklad)
    doklad.konto_pohyb = _vytvor_konto_pohyb(doklad)

    for polozka in doklad.polozky.select_related("plu__surovina").all():
        _odepis_skladovou_polozku(polozka)

    doklad.stav = PokladniDoklad.STAV_UZAVRENO
    doklad.uzavren_at = timezone.now()
    doklad.uzavrel = user
    doklad.save(update_fields=[
        "stav",
        "zpusob_platby",
        "cislo_dokladu",
        "konto_pohyb",
        "uzavren_at",
        "uzavrel",
    ])
    return doklad


def _vrat_skladovou_polozku(polozka):
    if not polozka.skladovy_pohyb_id or not polozka.plu.surovina_id:
        return None
    surovina = polozka.plu.surovina
    mnozstvi = polozka.mnozstvi or Decimal("0")
    stav, _ = StavSkladu.objects.select_for_update().get_or_create(
        surovina=surovina,
        defaults={"mnozstvi": Decimal("0"), "min_mnozstvi": Decimal("0")},
    )
    stav.mnozstvi = (stav.mnozstvi or Decimal("0")) + mnozstvi
    stav.save(update_fields=["mnozstvi"])
    return PohybSkladu.objects.create(
        datum=timezone.now(),
        surovina=surovina,
        typ=PohybSkladu.TYP_PRIJEM,
        mnozstvi=mnozstvi,
        cena_za_jednotku=polozka.skladovy_pohyb.cena_za_jednotku,
        poznamka=f"Storno pokladního prodeje {polozka.doklad.cislo_dokladu or polozka.doklad_id}",
    )


@transaction.atomic
def stornuj_doklad(doklad, user=None, duvod=""):
    doklad = (
        PokladniDoklad.objects
        .select_for_update()
        .select_related("zakaznik", "konto_pohyb")
        .get(pk=doklad.pk)
    )
    if doklad.je_stornovany:
        return doklad
    if not doklad.je_uzavreny:
        raise ValidationError("Stornovat lze pouze uzavřený doklad.")
    if doklad.uzaverka_id:
        raise ValidationError("Doklad je v denní uzávěrce. Storno uzavřeného dne řeš opravným dokladem.")

    if doklad.konto_pohyb_id and doklad.zakaznik_id:
        Vklad.objects.create(
            uzivatel=doklad.zakaznik,
            castka=_q2(doklad.celkem_s_dph),
            poznamka=f"Storno čerpání konta dokladem {doklad.cislo_dokladu or doklad.id}",
        )

    for polozka in doklad.polozky.select_related("plu__surovina", "skladovy_pohyb").all():
        _vrat_skladovou_polozku(polozka)

    doklad.stav = PokladniDoklad.STAV_STORNOVANO
    doklad.stornovano_at = timezone.now()
    doklad.stornoval = user
    doklad.storno_duvod = duvod or "Storno pokladního dokladu"
    doklad.save(update_fields=["stav", "stornovano_at", "stornoval", "storno_duvod"])
    return doklad


@transaction.atomic
def uzavri_denni_uzaverku(pokladna, datum, user=None, hotovost_spoctena=None, poznamka=""):
    uzaverka, _ = PokladniUzaverka.objects.select_for_update().get_or_create(
        pokladna=pokladna,
        datum=datum,
        defaults={"uzavrel": user},
    )
    doklady = (
        PokladniDoklad.objects
        .select_for_update()
        .filter(
            pokladna=pokladna,
            datum__date=datum,
            stav=PokladniDoklad.STAV_UZAVRENO,
        )
        .filter(Q(uzaverka__isnull=True) | Q(uzaverka=uzaverka))
    )
    storna = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        datum__date=datum,
        stav=PokladniDoklad.STAV_STORNOVANO,
    )
    soucty = {
        PokladniDoklad.PLATBA_HOTOVOST: Decimal("0"),
        PokladniDoklad.PLATBA_KARTA: Decimal("0"),
        PokladniDoklad.PLATBA_KONTO: Decimal("0"),
    }
    for platba, suma in doklady.values_list("zpusob_platby").annotate(suma=Sum("celkem_s_dph")):
        if platba in soucty:
            soucty[platba] = _q2(suma)

    uzaverka.pocet_dokladu = doklady.count()
    uzaverka.pocet_storen = storna.count()
    uzaverka.hotovost = soucty[PokladniDoklad.PLATBA_HOTOVOST]
    uzaverka.karta = soucty[PokladniDoklad.PLATBA_KARTA]
    uzaverka.konto = soucty[PokladniDoklad.PLATBA_KONTO]
    uzaverka.storna = _q2(storna.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0"))
    uzaverka.hotovost_spoctena = hotovost_spoctena
    uzaverka.rozdil_hotovosti = _q2((hotovost_spoctena or uzaverka.hotovost) - uzaverka.hotovost)
    uzaverka.poznamka = poznamka
    uzaverka.uzavrel = user or uzaverka.uzavrel
    uzaverka.save()
    doklady.filter(uzaverka__isnull=True).update(uzaverka=uzaverka)
    return uzaverka
