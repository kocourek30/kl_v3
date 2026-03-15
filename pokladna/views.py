from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Pokladna, PLUPolozka, PokladniDoklad, PokladniPolozka, PokladnaTile
from jidelnicek.models import Jidelnicek, Jidlo
from users.models import CustomUser

SESSION_KEY_TEMPLATE = "pokladna_doklad_{pokladna_id}"


@login_required
def pokladna_view(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)

    session_key = SESSION_KEY_TEMPLATE.format(pokladna_id=pokladna_id)
    doklad_id = request.session.get(session_key)
    doklad = PokladniDoklad.objects.filter(pk=doklad_id).first() if doklad_id else None

    if doklad_id and not doklad:
        request.session.pop(session_key, None)
        doklad_id = None

    chyba_konto = None  # textová hláška pro nedostatek prostředků / limit

    # --- POST: mazání, uzavírání, markování, přiřazení zákazníka ---
    if request.method == "POST":
        akce = request.POST.get("akce") or "pridat"

        # smazání konkrétní položky
        if akce == "smazat_polozku" and doklad:
            pol_id = request.POST.get("polozka_id")
            pol = doklad.polozky.filter(pk=pol_id).first()
            if pol:
                pol.delete()
                doklad.prepocitej_sumy()
                if not doklad.polozky.exists():
                    request.session.pop(session_key, None)
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # uzavření účtenky (s kontrolou limitu, ne jen nuly)
        if akce == "uzavrit" and doklad:
            zakaznik = doklad.zakaznik
            celkem = doklad.celkem_s_dph or Decimal("0")

            if zakaznik is not None:
                stav = zakaznik.aktualni_zustatek
                limit = getattr(zakaznik, "kreditni_limit", Decimal("0"))

                novy_zustatek = stav - celkem  # po zaúčtování účtenky

                if novy_zustatek < limit:
                    # kolik by měl minimálně dobít, aby byl zase na limitu
                    chybi = (limit - novy_zustatek).quantize(Decimal("0.01"))
                    chyba_konto = (
                        "Překročen kreditní limit na kontě. "
                        f"Chybí minimálně {chybi} Kč, aby bylo možné účet uzavřít."
                    )
                else:
                    request.session.pop(session_key, None)
                    return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            else:
                # bez zákazníka – hotovost
                request.session.pop(session_key, None)
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # AJAX přiřazení zákazníka z modalu – bez reloadu
        if akce == "pripojit_zakaznika_bez_karty_ajax":
            if not doklad:
                return JsonResponse(
                    {"ok": False, "error": "Neexistuje otevřený doklad."},
                    status=400,
                )

            zakaznik_id = request.POST.get("zakaznik_id")
            if not zakaznik_id:
                return JsonResponse(
                    {"ok": False, "error": "Nebyl vybrán žádný zákazník."},
                    status=400,
                )

            try:
                zak = CustomUser.objects.get(pk=zakaznik_id)
            except CustomUser.DoesNotExist:
                return JsonResponse(
                    {"ok": False, "error": "Zákazník nebyl nalezen."},
                    status=404,
                )

            doklad.zakaznik = zak
            doklad.save(update_fields=["zakaznik"])

            stav = zak.aktualni_zustatek
            limit = getattr(zak, "kreditni_limit", Decimal("0"))
            celkem = doklad.celkem_s_dph or Decimal("0")

            novy_zustatek = stav - celkem
            prekrocen_limit = novy_zustatek < limit

            if prekrocen_limit:
                chybi = (limit - novy_zustatek).quantize(Decimal("0.01"))
            else:
                chybi = Decimal("0.00")

            if zak.first_name or zak.last_name:
                jmeno = f"{zak.first_name} {zak.last_name}".strip()
            else:
                jmeno = zak.username

            return JsonResponse(
                {
                    "ok": True,
                    "jmeno": jmeno,
                    "stav_konta": f"{stav:.2f}",
                    "aktualni_ucet": f"{celkem:.2f}",
                    "nema_dostatek": bool(prekrocen_limit),
                    "chybi_castka": f"{chybi:.2f}",
                }
            )

        # přidání položky (markování)
        plu_id = request.POST.get("plu_id")
        jidlo_id = request.POST.get("jidlo_id")
        mnozstvi = Decimal(request.POST.get("mnozstvi", "1") or "1")

        if not doklad:
            doklad = PokladniDoklad.objects.create(
                pokladna=pokladna,
                obsluha=request.user,
                zakaznik=None,
            )
            request.session[session_key] = doklad.id

        sazba = None
        cena_s_dph = None
        plu = None

        if plu_id:
            plu = get_object_or_404(PLUPolozka, pk=plu_id, aktivni=True)
            sazba = plu.dph_skupina.sazba
            cena_s_dph = plu.cena

        elif jidlo_id:
            jidlo = get_object_or_404(Jidlo, pk=jidlo_id)

            plu = PLUPolozka.objects.filter(jidlo=jidlo, aktivni=True).first()
            if not plu:
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

            plu.nazev = jidlo.nazev
            plu.cena = jidlo.cena
            plu.save(update_fields=["nazev", "cena"])

            sazba = Decimal("12.0")
            cena_s_dph = jidlo.cena

        else:
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        k = sazba / Decimal("100")
        zaklad = (cena_s_dph * mnozstvi) / (Decimal("1") + k)
        dph = cena_s_dph * mnozstvi - zaklad

        PokladniPolozka.objects.create(
            doklad=doklad,
            plu=plu,
            mnozstvi=mnozstvi,
            cena_jednotkova=cena_s_dph,
            dph_sazba=sazba,
            zaklad_dph=zaklad.quantize(Decimal("0.01")),
            castka_dph=dph.quantize(Decimal("0.01")),
            castka_celkem=(cena_s_dph * mnozstvi).quantize(Decimal("0.01")),
        )

        doklad.prepocitej_sumy()
        return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

    # --- GET: data pro šablonu ---

    tiles = (
        PokladnaTile.objects
        .filter(pokladna=pokladna, aktivni=True)
        .select_related("plu", "plu__dph_skupina", "plu__kategorie")
        .order_by("poradi", "id")
    )

    today = timezone.localdate()
    dnesni_jidelnicek = (
        Jidelnicek.objects
        .filter(
            platnost_od__lte=today,
            platnost_do__gte=today,
        )
        .prefetch_related("polozky__jidlo")
        .first()
    )

    dnesni_jidla = []
    if dnesni_jidelnicek:
        for polozka in dnesni_jidelnicek.polozky.all():
            if polozka.jidlo:
                dnesni_jidla.append(polozka.jidlo)

    polozky = doklad.polozky.select_related("plu") if doklad else []

    aktivni_zakaznik = doklad.zakaznik if doklad and doklad.zakaznik_id else None
    stav_konta = aktivni_zakaznik.aktualni_zustatek if aktivni_zakaznik else None
    kreditni_limit = getattr(aktivni_zakaznik, "kreditni_limit", None) if aktivni_zakaznik else None
    aktualni_ucet_celkem = doklad.celkem_s_dph if doklad else None

    moznosti_prepnuti = True

    velka_uctenka = doklad
    velka_uctenka_polozky = polozky

    context = {
        "pokladna": pokladna,
        "tiles": tiles,
        "dnesni_jidla": dnesni_jidla,
        "doklad": doklad,
        "polozky": polozky,
        "aktivni_zakaznik": aktivni_zakaznik,
        "stav_konta": stav_konta,
        "kreditni_limit": kreditni_limit,
        "aktualni_ucet_celkem": aktualni_ucet_celkem,
        "moznosti_prepnuti": moznosti_prepnuti,
        "velka_uctenka": velka_uctenka,
        "velka_uctenka_polozky": velka_uctenka_polozky,
        "chyba_konto": chyba_konto,
    }
    return render(request, "pokladna/pokladna.html", context)


@login_required
def pokladna_zakaznik_search(request, pokladna_id):
    """
    AJAX endpoint pro live search zákazníků v modalu.
    Vrací JSON {results: [{id, text}]}
    """
    q = (request.GET.get("q") or "").strip()
    qs = CustomUser.objects.all()

    if q:
        qs = qs.filter(username__icontains=q) | qs.filter(
            first_name__icontains=q
        ) | qs.filter(last_name__icontains=q) | qs.filter(
            osobni_cislo__icontains=q
        )

    qs = qs.distinct().order_by("last_name", "first_name", "username")[:30]

    results = []
    for u in qs:
        if u.first_name or u.last_name:
            name = f"{u.first_name} {u.last_name}".strip()
        else:
            name = u.username
        label = f"{name} (#{u.id})"
        if getattr(u, "osobni_cislo", None):
            label += f" [{u.osobni_cislo}]"
        results.append({"id": u.id, "text": label})

    return JsonResponse({"results": results})
