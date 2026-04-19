from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Pokladna, PLUPolozka, PokladniDoklad, PokladnaTile
from .services import (
    konto_nastaveni_uzivatele,
    decimal_z_postu,
    pridej_polozku,
    smaz_polozku,
    stornuj_doklad,
    uzavri_doklad,
    vytvor_doklad,
)
from jidelnicek.models import Jidelnicek, Jidlo
from users.models import CustomUser

SESSION_KEY_TEMPLATE = "pokladna_doklad_{pokladna_id}"


@login_required
def pokladna_view(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)

    session_key = SESSION_KEY_TEMPLATE.format(pokladna_id=pokladna_id)
    doklad_id = request.session.get(session_key)
    doklad = (
        PokladniDoklad.objects.filter(pk=doklad_id, stav=PokladniDoklad.STAV_ROZPRACOVANO).first()
        if doklad_id else None
    )

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
            try:
                smaz_polozku(doklad, pol_id)
                if not doklad.polozky.exists():
                    request.session.pop(session_key, None)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # storno poslední namarkované položky
        if akce == "storno_posledni" and doklad:
            posledni = doklad.polozky.order_by("-id").first()
            if not posledni:
                messages.warning(request, "Na účtence není žádná položka ke stornu.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            try:
                smaz_polozku(doklad, posledni.id)
                if not doklad.polozky.exists():
                    request.session.pop(session_key, None)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # uzavření účtenky
        if akce == "uzavrit" and doklad:
            zpusob_platby = request.POST.get("zpusob_platby") or (
                PokladniDoklad.PLATBA_KONTO if doklad.zakaznik_id else PokladniDoklad.PLATBA_HOTOVOST
            )
            try:
                uzavri_doklad(doklad, zpusob_platby, user=request.user)
                request.session.pop(session_key, None)
                messages.success(request, "Účtenka byla uzavřena.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            except ValidationError as exc:
                chyba_konto = exc.messages[0]
                messages.error(request, chyba_konto)

        if akce == "stornovat_doklad":
            storno_id = request.POST.get("doklad_id")
            storno_doklad = (
                PokladniDoklad.objects
                .select_related("pokladna", "obsluha")
                .filter(pk=storno_id, pokladna=pokladna)
                .first()
            )
            if not storno_doklad:
                messages.error(request, "Doklad pro storno nebyl nalezen.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            if storno_doklad.obsluha_id != request.user.id and not request.user.is_superuser:
                messages.error(request, "Stornovat může pouze obsluha dokladu nebo správce.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            try:
                stornuj_doklad(storno_doklad, user=request.user, duvod="Storno z pokladny")
                messages.success(request, "Doklad byl stornován.")
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # AJAX přiřazení zákazníka z modalu – bez reloadu
        if akce == "pripojit_zakaznika_bez_karty_ajax":
            try:
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
                    zak = CustomUser.objects.get(pk=zakaznik_id, is_active=True)
                except CustomUser.DoesNotExist:
                    return JsonResponse(
                        {"ok": False, "error": "Zákazník nebyl nalezen."},
                        status=404,
                    )

                doklad.zakaznik = zak
                doklad.save(update_fields=["zakaznik"])

                stav = zak.aktualni_zustatek
                konto_nastaveni = konto_nastaveni_uzivatele(zak)
                limit = konto_nastaveni["minimalni_zustatek"]
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
                        "limit_konta": f"{limit:.2f}",
                        "nema_dostatek": bool(prekrocen_limit),
                        "chybi_castka": f"{chybi:.2f}",
                    }
                )
            except Exception:
                return JsonResponse(
                    {"ok": False, "error": "Při načtení zákazníka došlo k chybě serveru."},
                    status=500,
                )

        # přidání položky (markování)
        plu_id = request.POST.get("plu_id")
        jidlo_id = request.POST.get("jidlo_id")
        try:
            mnozstvi = decimal_z_postu(request.POST.get("mnozstvi", "1"))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        if not doklad:
            doklad = vytvor_doklad(pokladna=pokladna, obsluha=request.user)
            request.session[session_key] = doklad.id

        plu = None

        if plu_id:
            plu = get_object_or_404(PLUPolozka, pk=plu_id, aktivni=True)

        elif jidlo_id:
            jidlo = get_object_or_404(Jidlo, pk=jidlo_id)

            plu = PLUPolozka.objects.filter(jidlo=jidlo, aktivni=True).first()
            if not plu:
                messages.error(request, "Jídlo nemá aktivní PLU položku.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        else:
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        try:
            pridej_polozku(doklad, plu, mnozstvi=mnozstvi)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
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
    kreditni_limit = None
    if aktivni_zakaznik:
        kreditni_limit = konto_nastaveni_uzivatele(aktivni_zakaznik)["minimalni_zustatek"]
    aktualni_ucet_celkem = doklad.celkem_s_dph if doklad else None

    moznosti_prepnuti = True

    velka_uctenka = doklad
    velka_uctenka_polozky = polozky
    posledni_doklady = (
        PokladniDoklad.objects
        .filter(pokladna=pokladna)
        .select_related("zakaznik")
        .order_by("-datum")[:8]
    )
    uzavrene_doklady = (
        PokladniDoklad.objects
        .filter(pokladna=pokladna, stav=PokladniDoklad.STAV_UZAVRENO)
        .select_related("zakaznik", "obsluha")
        .order_by("-uzavren_at", "-id")[:25]
    )

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
        "posledni_doklady": posledni_doklady,
        "uzavrene_doklady": uzavrene_doklady,
    }
    return render(request, "pokladna/pokladna.html", context)


@login_required
def pokladna_zakaznik_search(request, pokladna_id):
    """
    AJAX endpoint pro live search zákazníků v modalu.
    Vrací JSON {results: [{id, text}]}
    """
    try:
        q = (request.GET.get("q") or "").strip()
        qs = CustomUser.objects.filter(is_active=True)

        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(osobni_cislo__icontains=q)
            )

        qs = qs.order_by("last_name", "first_name", "username")[:30]

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
    except Exception:
        return JsonResponse({"results": [], "error": "Vyhledávání zákazníků selhalo."}, status=500)
