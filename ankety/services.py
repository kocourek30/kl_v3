from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from jidelnicek.models import DruhJidla
from objednavky.models import OrderItem

from .models import (
    AnketniOtazka,
    HodnoceniJidla,
    MesicniAnketa,
    MesicniAnketaHlas,
    MesicniAnketaVarianta,
    OdpovedHodnoceni,
)


def vydane_polozky_k_hodnoceni(user, target_date=None):
    target_date = target_date or timezone.localdate()
    return (
        OrderItem.objects
        .filter(order__user=user, vydano=True, order__datum_vydeje=target_date)
        .select_related("order", "menu_item__jidlo", "menu_item__druh_jidla")
        .order_by("-datum_vydani", "menu_item__druh_jidla__nazev")
    )


def anketni_prehled_uzivatele(user, target_date=None):
    hodnotit = []
    hotovo = []
    for item in vydane_polozky_k_hodnoceni(user, target_date):
        if hasattr(item, "hodnoceni"):
            hotovo.append(item)
        else:
            hodnotit.append(item)

    return {
        "hodnotit": hodnotit,
        "hotovo": hotovo,
        "otazky_count": AnketniOtazka.objects.filter(aktivni=True).count(),
        "mesicni_anketa": mesicni_anketa_kontext(user, target_date),
    }


def _aktivni_mesicni_anketa(target_date=None):
    target_date = target_date or timezone.localdate()
    return (
        MesicniAnketa.objects
        .filter(aktivni=True, hlasovani_od__lte=target_date, hlasovani_do__gte=target_date)
        .prefetch_related("varianty", "hlasy")
        .order_by("-rok", "-mesic", "-vytvoreno")
        .first()
    )


def mesicni_anketa_kontext(user, target_date=None):
    anketa = _aktivni_mesicni_anketa(target_date)
    if not anketa:
        return {
            "exists": False,
            "is_open": False,
            "already_voted": False,
            "anketa": None,
            "varianty": [],
            "my_vote": None,
            "total_votes": 0,
        }

    my_vote = MesicniAnketaHlas.objects.filter(anketa=anketa, user=user).select_related("varianta").first()
    votes_per_option = (
        anketa.hlasy.values("varianta_id")
        .annotate(total=Count("id"))
    )
    vote_map = {row["varianta_id"]: row["total"] for row in votes_per_option}
    total_votes = sum(vote_map.values())

    varianty = []
    for varianta in anketa.varianty.all().order_by("poradi", "id"):
        count = vote_map.get(varianta.id, 0)
        pct = round((count * 100 / total_votes), 1) if total_votes else 0
        varianty.append(
            {
                "obj": varianta,
                "count": count,
                "pct": pct,
                "is_selected": bool(my_vote and my_vote.varianta_id == varianta.id),
            }
        )

    return {
        "exists": True,
        "is_open": anketa.is_open(target_date),
        "already_voted": bool(my_vote),
        "anketa": anketa,
        "varianty": varianty,
        "my_vote": my_vote,
        "total_votes": total_votes,
    }


def odevzdat_hlas_v_mesicni_ankete(*, user, varianta_id, target_date=None):
    anketa = _aktivni_mesicni_anketa(target_date)
    if not anketa:
        return {"ok": False, "error": "Aktuálně není otevřené žádné měsíční hlasování."}
    if not anketa.is_open(target_date):
        return {"ok": False, "error": "Hlasování není v tomto období otevřené."}
    if MesicniAnketaHlas.objects.filter(anketa=anketa, user=user).exists():
        return {"ok": False, "error": "Hlas už byl odeslaný. Každý může hlasovat jen jednou."}

    varianta = MesicniAnketaVarianta.objects.filter(anketa=anketa, pk=varianta_id).first()
    if not varianta:
        return {"ok": False, "error": "Vybraná varianta není platná."}

    with transaction.atomic():
        MesicniAnketaHlas.objects.create(
            anketa=anketa,
            varianta=varianta,
            user=user,
        )
    return {"ok": True, "anketa": anketa, "varianta": varianta}


def _fmt_avg(value):
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _pct(part, total):
    if not total:
        return Decimal("0.00")
    return (Decimal(part) * Decimal("100") / Decimal(total)).quantize(Decimal("0.01"))


def anketni_report_obdobi(date_from, date_to, *, min_hodnoceni=1):
    hodnoceni_qs = (
        HodnoceniJidla.objects
        .filter(datum_vydeje__gte=date_from, datum_vydeje__lte=date_to)
        .select_related("user", "order_item", "order_item__menu_item", "order_item__menu_item__jidlo")
        .prefetch_related("odpovedi__otazka")
    )
    odpovedi_qs = OdpovedHodnoceni.objects.filter(
        hodnoceni_jidla__datum_vydeje__gte=date_from,
        hodnoceni_jidla__datum_vydeje__lte=date_to,
    )
    vydane_qs = OrderItem.objects.filter(
        vydano=True,
        order__datum_vydeje__gte=date_from,
        order__datum_vydeje__lte=date_to,
    ).select_related("menu_item__jidlo", "menu_item__druh_jidla")
    objednane_qs = (
        OrderItem.objects
        .filter(order__datum_vydeje__gte=date_from, order__datum_vydeje__lte=date_to)
        .exclude(order__status__in=["zruseno-uzivatelem", "zruseno-obsluhou"])
        .select_related("menu_item__jidlo", "menu_item__druh_jidla")
    )

    hodnoceni_count = hodnoceni_qs.count()
    vydane_count = vydane_qs.aggregate(total=Sum("quantity"))["total"] or 0
    hodnotitelnost_pct = _pct(hodnoceni_count, vydane_count)
    prumer = _fmt_avg(odpovedi_qs.aggregate(avg=Avg("znamka"))["avg"])

    jidla = []
    jidla_map = defaultdict(lambda: {
        "jidlo": "",
        "druh_jidla": "Bez druhu",
        "hodnoceni": 0,
        "odpovedi": 0,
        "soucet": 0,
        "objednano": 0,
        "vydano": 0,
        "poznamek": 0,
    })

    for item in objednane_qs:
        nazev = item.menu_item.jidlo.nazev if item.menu_item_id and item.menu_item.jidlo_id else str(item.menu_item)
        druh = getattr(getattr(item, "menu_item", None), "druh_jidla", None)
        jidla_map[nazev]["jidlo"] = nazev
        jidla_map[nazev]["druh_jidla"] = getattr(druh, "nazev", None) or jidla_map[nazev]["druh_jidla"]
        jidla_map[nazev]["objednano"] += item.quantity or 0
        if item.vydano:
            jidla_map[nazev]["vydano"] += item.quantity or 0

    for hodnoceni in hodnoceni_qs:
        row = jidla_map[hodnoceni.jidlo_nazev]
        row["jidlo"] = hodnoceni.jidlo_nazev
        druh = getattr(getattr(getattr(hodnoceni, "order_item", None), "menu_item", None), "druh_jidla", None)
        row["druh_jidla"] = getattr(druh, "nazev", None) or row["druh_jidla"]
        row["hodnoceni"] += 1
        if hodnoceni.poznamka:
            row["poznamek"] += 1
        for odpoved in hodnoceni.odpovedi.all():
            row["odpovedi"] += 1
            row["soucet"] += odpoved.znamka

    for row in jidla_map.values():
        row["prumer"] = _fmt_avg(Decimal(row["soucet"]) / Decimal(row["odpovedi"])) if row["odpovedi"] else None
        row["navratnost"] = _pct(row["hodnoceni"], row["vydano"])
        jidla.append(row)

    hodnocena_jidla = [row for row in jidla if row["hodnoceni"] >= min_hodnoceni and row["prumer"] is not None]
    nejlepsi = sorted(hodnocena_jidla, key=lambda row: (row["prumer"], row["hodnoceni"]), reverse=True)[:10]
    nejslabsi = sorted(hodnocena_jidla, key=lambda row: (row["prumer"], -row["hodnoceni"]))[:10]
    nejobjednavanejsi = sorted(jidla, key=lambda row: (row["objednano"], row["hodnoceni"]), reverse=True)[:12]
    nejvice_poznamek = sorted([row for row in jidla if row["poznamek"]], key=lambda row: row["poznamek"], reverse=True)[:8]

    hodnocena_podle_druhu = defaultdict(list)
    for row in hodnocena_jidla:
        hodnocena_podle_druhu[row["druh_jidla"]].append(row)

    vsechny_druhy = list(DruhJidla.objects.order_by("poradi", "nazev").values_list("nazev", flat=True))
    if "Bez druhu" in hodnocena_podle_druhu and "Bez druhu" not in vsechny_druhy:
        vsechny_druhy.append("Bez druhu")

    nejlepsi_podle_druhu = []
    nejslabsi_podle_druhu = []
    for druh_nazev in vsechny_druhy:
        rows = hodnocena_podle_druhu.get(druh_nazev, [])
        nejlepsi_rows = sorted(rows, key=lambda row: (row["prumer"], row["hodnoceni"]), reverse=True)[:5]
        nejslabsi_rows = sorted(rows, key=lambda row: (row["prumer"], -row["hodnoceni"]))[:5]
        prumer_druhu = _fmt_avg(sum(row["soucet"] for row in rows) / sum(row["odpovedi"] for row in rows)) if sum(row["odpovedi"] for row in rows) else None
        nejlepsi_podle_druhu.append({
            "druh_jidla": druh_nazev,
            "prumer": prumer_druhu,
            "count": len(rows),
            "rows": nejlepsi_rows,
        })
        nejslabsi_podle_druhu.append({
            "druh_jidla": druh_nazev,
            "prumer": prumer_druhu,
            "count": len(rows),
            "rows": nejslabsi_rows,
        })

    druhy_graf = []
    max_hodnoceni_v_druhu = max((group["count"] for group in nejlepsi_podle_druhu), default=0)
    for group in nejlepsi_podle_druhu:
        width = int((group["count"] / max_hodnoceni_v_druhu) * 100) if max_hodnoceni_v_druhu else 0
        druhy_graf.append({
            "druh_jidla": group["druh_jidla"],
            "prumer": group["prumer"],
            "count": group["count"],
            "width": width,
        })

    otazky = [
        {
            "otazka": row["otazka__text"],
            "pocet": row["pocet"],
            "prumer": _fmt_avg(row["prumer"]),
        }
        for row in odpovedi_qs.values("otazka__text").annotate(
            pocet=Count("id"),
            prumer=Avg("znamka"),
        ).order_by("otazka__poradi", "otazka__id")
    ]

    trendy = []
    hodnoceni_podle_dne = defaultdict(lambda: {"datum": None, "hodnoceni": 0, "odpovedi": 0, "soucet": 0})
    for hodnoceni in hodnoceni_qs:
        row = hodnoceni_podle_dne[hodnoceni.datum_vydeje]
        row["datum"] = hodnoceni.datum_vydeje
        row["hodnoceni"] += 1
        for odpoved in hodnoceni.odpovedi.all():
            row["odpovedi"] += 1
            row["soucet"] += odpoved.znamka
    for row in sorted(hodnoceni_podle_dne.values(), key=lambda item: item["datum"]):
        row["prumer"] = _fmt_avg(Decimal(row["soucet"]) / Decimal(row["odpovedi"])) if row["odpovedi"] else None
        trendy.append(row)

    poznamky_all = [
        {
            "jidlo": h.jidlo_nazev,
            "druh_jidla": getattr(getattr(getattr(h, "order_item", None), "menu_item", None), "druh_jidla", None),
            "stravnik": h.user.get_full_name() or h.user.username,
            "datum": h.datum_vydeje,
            "poznamka": h.poznamka,
            "prumer": _fmt_avg(sum(o.znamka for o in h.odpovedi.all()) / len(h.odpovedi.all())) if h.odpovedi.all() else None,
        }
        for h in hodnoceni_qs
        if h.poznamka
    ]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "hodnoceni_count": hodnoceni_count,
        "odpovedi_count": odpovedi_qs.count(),
        "vydane_count": vydane_count,
        "objednane_count": objednane_qs.aggregate(total=Sum("quantity"))["total"] or 0,
        "navratnost_pct": hodnotitelnost_pct,
        "prumer": prumer,
        "jidla_count": len(hodnocena_jidla),
        "jidla": jidla,
        "hodnocena_jidla": hodnocena_jidla,
        "nejlepsi": nejlepsi,
        "nejslabsi": nejslabsi,
        "nejobjednavanejsi": nejobjednavanejsi,
        "nejlepsi_podle_druhu": nejlepsi_podle_druhu,
        "nejslabsi_podle_druhu": nejslabsi_podle_druhu,
        "druhy_graf": druhy_graf,
        "nejvice_poznamek": nejvice_poznamek,
        "otazky": otazky,
        "trendy": trendy,
        "poznamky": poznamky_all[:20],
        "poznamky_all": poznamky_all,
    }
