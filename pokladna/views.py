from decimal import Decimal
import re

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth.decorators import login_required as django_login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import Pokladna, PLUPolozka, PokladniDoklad, PokladniPolozka, PokladnaTile, PokladniUzaverka
from .reports import dph_souhrn, doklady_za_obdobi, plu_obraty, trzby_podle_druhu, trzby_podle_plateb
from .services import (
    konto_nastaveni_uzivatele,
    decimal_z_postu,
    pridej_polozku,
    potvrdit_qr_platbu,
    qr_payload_data_uri,
    qr_platba_url,
    smaz_polozku,
    stornuj_doklad,
    uzavri_denni_uzaverku,
    uzavri_doklad,
    vytvor_doklad,
    vytvor_vklad_konta,
    zahaj_qr_platbu,
    zrus_rozpracovany_doklad,
)
from jidelnicek.models import Jidelnicek, Jidlo
from users.models import CustomUser, Vklad

SESSION_KEY_TEMPLATE = "pokladna_doklad_{pokladna_id}"


def login_required(view_func):
    return django_login_required(view_func, login_url="pokladna:pokladna_login")


def _pokladna_session_key(pokladna_id):
    return SESSION_KEY_TEMPLATE.format(pokladna_id=pokladna_id)


def _odhlas_zakaznika_pokladny(request, pokladna_id):
    session_key = _pokladna_session_key(pokladna_id)
    doklad_id = request.session.pop(session_key, None)
    if not doklad_id:
        return

    PokladniDoklad.objects.filter(
        pk=doklad_id,
        stav=PokladniDoklad.STAV_ROZPRACOVANO,
    ).update(zakaznik=None)


def _pokladna_start_url_from_next(next_url):
    match = re.search(r"/pokladna/(\d+)/", next_url or "")
    if match:
        pokladna = Pokladna.objects.filter(pk=match.group(1), aktivni=True).first()
        if pokladna:
            return reverse("pokladna:pokladna_view", kwargs={"pokladna_id": pokladna.id})

    pokladna = Pokladna.objects.filter(aktivni=True).order_by("id").first()
    if pokladna:
        return reverse("pokladna:pokladna_view", kwargs={"pokladna_id": pokladna.id})
    return reverse("admin:index")


def pokladna_login(request):
    next_url = request.GET.get("next") or request.POST.get("next") or ""
    redirect_url = _pokladna_start_url_from_next(next_url)

    if request.user.is_authenticated:
        return redirect(redirect_url)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is None:
            messages.error(request, "Přihlášení se nepovedlo. Zkontroluj uživatelské jméno a heslo.")
        elif not user.is_active:
            messages.error(request, "Tento účet není aktivní.")
        else:
            auth_login(request, user)
            messages.success(request, "Přihlášení proběhlo úspěšně.")
            return redirect(redirect_url)

    return render(
        request,
        "pokladna/login.html",
        {
            "next": next_url,
            "redirect_url": redirect_url,
        },
    )


@login_required
def pokladna_view(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    today = timezone.localdate()
    dnes_uzavreno = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav=PokladniDoklad.STAV_UZAVRENO,
        datum__date=today,
    )
    dnes_trzba = dnes_uzavreno.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    hotovost_dnes = dnes_uzavreno.filter(
        zpusob_platby=PokladniDoklad.PLATBA_HOTOVOST
    ).aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    vklady_kont = _vklady_kont_podle_uhrady(pokladna, today, today)
    ceka_qr = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav=PokladniDoklad.STAV_CEKA_NA_QR,
    ).count()
    return render(request, "pokladna/home.html", {
        "pokladna": pokladna,
        "dnes_trzba": dnes_trzba,
        "dnes_pocet": dnes_uzavreno.count(),
        "ceka_qr": ceka_qr,
        "pokladni_hotovost": (pokladna.hotovostni_zustatek or Decimal("0")) + hotovost_dnes,
    })


@login_required
def pokladna_ucet(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)

    session_key = _pokladna_session_key(pokladna_id)
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
                smaz_polozku(doklad, pol_id, user=request.user)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

        # storno poslední namarkované položky
        if akce == "storno_posledni" and doklad:
            posledni = doklad.polozky.order_by("-id").first()
            if not posledni:
                messages.warning(request, "Na účtence není žádná položka ke stornu.")
                return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)
            try:
                smaz_polozku(doklad, posledni.id, user=request.user, duvod="Storno poslední položky")
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

        if akce == "zrusit_ucet":
            if not doklad:
                messages.info(request, "Není otevřený žádný účet ke zrušení.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            try:
                zrus_rozpracovany_doklad(doklad, user=request.user)
                request.session.pop(session_key, None)
                messages.info(request, "Účet byl zrušen a zůstává uložený v přehledu dokladů.")
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

        # uzavření účtenky
        if akce == "uzavrit" and doklad:
            zpusob_platby = request.POST.get("zpusob_platby") or (
                PokladniDoklad.PLATBA_KONTO if doklad.zakaznik_id else PokladniDoklad.PLATBA_HOTOVOST
            )
            try:
                if zpusob_platby == PokladniDoklad.PLATBA_QR:
                    qr_doklad = zahaj_qr_platbu(doklad, user=request.user)
                    _odhlas_zakaznika_pokladny(request, pokladna.id)
                    messages.info(request, "QR platba byla připravena. Účet uzavři až po kontrole platby.")
                    return redirect("pokladna:pokladna_qr_platba", pokladna_id=pokladna.id, doklad_id=qr_doklad.id)
                uzavri_doklad(doklad, zpusob_platby, user=request.user)
                _odhlas_zakaznika_pokladny(request, pokladna.id)
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
                return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)
            if storno_doklad.obsluha_id != request.user.id and not request.user.is_superuser:
                messages.error(request, "Stornovat může pouze obsluha dokladu nebo správce.")
                return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)
            try:
                stornuj_doklad(storno_doklad, user=request.user, duvod="Storno z pokladny")
                messages.success(request, "Doklad byl stornován.")
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

        # AJAX přiřazení zákazníka z modalu – bez reloadu
        if akce == "pripojit_zakaznika_bez_karty_ajax":
            try:
                if not doklad:
                    doklad = vytvor_doklad(pokladna=pokladna, obsluha=request.user)
                    request.session[session_key] = doklad.id

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
            return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

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
                return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

        else:
            return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

        try:
            pridej_polozku(doklad, plu, mnozstvi=mnozstvi)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)

    # --- GET: data pro šablonu ---

    tiles = (
        PokladnaTile.objects
        .filter(pokladna=pokladna, aktivni=True)
        .select_related("plu", "plu__dph_skupina", "plu__kategorie", "plu__jidlo", "plu__jidlo__druh")
        .order_by("poradi", "id")
    )

    today = timezone.localdate()
    dnesni_jidelnicek = (
        Jidelnicek.objects
        .filter(
            platnost_od__lte=today,
            platnost_do__gte=today,
        )
        .prefetch_related("polozky__jidlo", "polozky__jidlo__druh")
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


@login_required
def pokladna_vklad_konto(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)

    if request.method == "POST":
        zakaznik_id = request.POST.get("zakaznik_id")
        poznamka = (request.POST.get("poznamka") or "").strip()
        zpusob_uhrady = request.POST.get("zpusob_uhrady") or Vklad.ZPUSOB_HOTOVOST

        if not zakaznik_id:
            messages.error(request, "Nejprve vyber zákazníka, kterému chceš dobít konto.")
            return redirect("pokladna:pokladna_vklad_konto", pokladna_id=pokladna.id)

        if zpusob_uhrady not in dict(Vklad.ZPUSOBY_UHRADY):
            messages.error(request, "Vyber platný způsob úhrady vkladu.")
            return redirect("pokladna:pokladna_vklad_konto", pokladna_id=pokladna.id)

        try:
            castka = decimal_z_postu(request.POST.get("castka"))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_vklad_konto", pokladna_id=pokladna.id)

        if castka <= 0:
            messages.error(request, "Částka vkladu musí být větší než nula.")
            return redirect("pokladna:pokladna_vklad_konto", pokladna_id=pokladna.id)

        zakaznik = get_object_or_404(CustomUser, pk=zakaznik_id, is_active=True)
        popis = poznamka or (
            f"Vklad přes pokladnu {pokladna.nazev} "
            f"({dict(Vklad.ZPUSOBY_UHRADY)[zpusob_uhrady]}, {request.user.get_username()})"
        )
        try:
            vklad, doklad = vytvor_vklad_konta(
                pokladna=pokladna,
                zakaznik=zakaznik,
                castka=castka,
                zpusob_uhrady=zpusob_uhrady,
                obsluha=request.user,
                poznamka=popis,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("pokladna:pokladna_vklad_konto", pokladna_id=pokladna.id)

        jmeno = zakaznik.get_full_name() or zakaznik.username
        messages.success(
            request,
            (
                f"Vklad {vklad.castka:.2f} Kč pro {jmeno} byl uložen "
                f"({dict(Vklad.ZPUSOBY_UHRADY)[zpusob_uhrady]}, doklad {doklad.cislo_dokladu})."
            ),
        )
        _odhlas_zakaznika_pokladny(request, pokladna.id)
        return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)

    posledni_vklady = (
        Vklad.objects
        .filter(status="standard", castka__gt=0)
        .select_related("uzivatel")
        .order_by("-datum", "-id")[:12]
    )
    return render(request, "pokladna/vklad_konto.html", {
        "pokladna": pokladna,
        "posledni_vklady": posledni_vklady,
        "zpusoby_uhrady": Vklad.ZPUSOBY_UHRADY,
        "vychozi_zpusob_uhrady": Vklad.ZPUSOB_HOTOVOST,
    })


def _trzby_dne_podle_plateb(pokladna, den):
    doklady = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav=PokladniDoklad.STAV_UZAVRENO,
        datum__date=den,
    )
    soucty = {
        platba: Decimal("0")
        for platba, _ in PokladniDoklad.ZPUSOBY_PLATBY
    }
    for platba, suma in doklady.values_list("zpusob_platby").annotate(suma=Sum("celkem_s_dph")):
        if platba in soucty:
            soucty[platba] = suma or Decimal("0")
    celkem = sum(soucty.values(), Decimal("0"))
    polozky = []
    barvy = {
        PokladniDoklad.PLATBA_KONTO: "#18a046",
        PokladniDoklad.PLATBA_HOTOVOST: "#f28f28",
        PokladniDoklad.PLATBA_KARTA: "#0d6efd",
        PokladniDoklad.PLATBA_QR: "#7c3aed",
    }
    for platba, nazev in PokladniDoklad.ZPUSOBY_PLATBY:
        castka = soucty.get(platba, Decimal("0"))
        procento = int((castka / celkem) * 100) if celkem else 0
        polozky.append({
            "kod": platba,
            "nazev": nazev,
            "castka": castka,
            "procento": procento,
            "barva": barvy.get(platba, "#6c757d"),
        })
    return polozky, celkem, doklady.count()


def _vklady_kont_podle_uhrady(pokladna, datum_od, datum_do):
    vklady = Vklad.objects.filter(
        pokladna=pokladna,
        datum__date__gte=datum_od,
        datum__date__lte=datum_do,
        status="standard",
        castka__gt=0,
    )
    polozky = []
    for kod, nazev in Vklad.ZPUSOBY_UHRADY:
        qs = vklady.filter(zpusob_uhrady=kod)
        polozky.append({
            "kod": kod,
            "nazev": nazev,
            "pocet": qs.count(),
            "castka": qs.aggregate(suma=Sum("castka"))["suma"] or Decimal("0"),
        })
    return {
        "celkem": vklady.aggregate(suma=Sum("castka"))["suma"] or Decimal("0"),
        "hotovost": vklady.filter(zpusob_uhrady=Vklad.ZPUSOB_HOTOVOST).aggregate(suma=Sum("castka"))["suma"] or Decimal("0"),
        "polozky": polozky,
    }


@login_required
def pokladna_prehled(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    today = timezone.localdate()
    otevrene = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav__in=[PokladniDoklad.STAV_ROZPRACOVANO, PokladniDoklad.STAV_CEKA_NA_QR],
    ).select_related("zakaznik", "obsluha").order_by("-datum")
    uzavrene = PokladniDoklad.objects.filter(
        pokladna=pokladna,
        stav=PokladniDoklad.STAV_UZAVRENO,
        datum__date=today,
    ).select_related("zakaznik", "obsluha").order_by("-uzavren_at", "-id")[:15]
    trzby_podle_plateb, trzba_celkem, pocet_uctu = _trzby_dne_podle_plateb(pokladna, today)
    return render(request, "pokladna/prehled.html", {
        "pokladna": pokladna,
        "otevrene": otevrene,
        "uzavrene": uzavrene,
        "trzby_podle_plateb": trzby_podle_plateb,
        "trzba_celkem": trzba_celkem,
        "pocet_uctu": pocet_uctu,
        "today": today,
    })


@login_required
def pokladna_uzavrene_ucty(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    doklady = (
        PokladniDoklad.objects
        .filter(pokladna=pokladna)
        .exclude(stav=PokladniDoklad.STAV_ROZPRACOVANO)
        .select_related("zakaznik", "obsluha", "uzavrel")
        .order_by("-uzavren_at", "-datum", "-id")[:200]
    )
    return render(request, "pokladna/uzavrene_ucty.html", {
        "pokladna": pokladna,
        "doklady": doklady,
    })


@login_required
def pokladna_doklad_detail(request, pokladna_id, doklad_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    doklad = get_object_or_404(
        PokladniDoklad.objects
        .select_related("pokladna", "zakaznik", "obsluha", "uzavrel", "stornoval", "uzaverka")
        .prefetch_related("polozky__plu"),
        pk=doklad_id,
        pokladna=pokladna,
    )
    return render(request, "pokladna/doklad_detail.html", {
        "pokladna": pokladna,
        "doklad": doklad,
        "polozky": doklad.polozky.all(),
        "smazane_polozky": doklad.smazane_polozky.select_related("plu", "smazal").all(),
    })


@login_required
def pokladna_stornovat_doklad(request, pokladna_id, doklad_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    if request.method != "POST":
        return redirect("pokladna:pokladna_doklad_detail", pokladna_id=pokladna.id, doklad_id=doklad_id)

    doklad = get_object_or_404(
        PokladniDoklad.objects.select_related("pokladna", "obsluha"),
        pk=doklad_id,
        pokladna=pokladna,
    )
    if doklad.obsluha_id != request.user.id and not request.user.is_superuser:
        messages.error(request, "Stornovat může pouze obsluha dokladu nebo správce.")
        return redirect("pokladna:pokladna_doklad_detail", pokladna_id=pokladna.id, doklad_id=doklad.id)
    try:
        stornuj_doklad(doklad, user=request.user, duvod="Storno z pokladny")
        messages.success(request, "Doklad byl stornován.")
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    next_url = request.POST.get("next")
    if next_url == "uzavrene":
        return redirect("pokladna:pokladna_uzavrene_ucty", pokladna_id=pokladna.id)
    return redirect("pokladna:pokladna_doklad_detail", pokladna_id=pokladna.id, doklad_id=doklad.id)


@login_required
def pokladna_financni_report(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    today = timezone.localdate()
    datum_od = parse_date(request.GET.get("od") or "") or today
    datum_do = parse_date(request.GET.get("do") or "") or datum_od
    if datum_do < datum_od:
        datum_od, datum_do = datum_do, datum_od

    doklady = doklady_za_obdobi(pokladna, datum_od, datum_do)
    prodejni_doklady = doklady.filter(typ_dokladu=PokladniDoklad.TYP_PRODEJ)
    vkladove_doklady = doklady.filter(typ_dokladu=PokladniDoklad.TYP_VKLAD_KONTA)
    prodej_celkem = prodejni_doklady.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    vklady_celkem = vkladove_doklady.aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    celkem = prodej_celkem + vklady_celkem
    hotovost = doklady.filter(
        zpusob_platby=PokladniDoklad.PLATBA_HOTOVOST
    ).aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    vklady_kont = _vklady_kont_podle_uhrady(pokladna, datum_od, datum_do)

    return render(request, "pokladna/financni_report.html", {
        "pokladna": pokladna,
        "datum_od": datum_od,
        "datum_do": datum_do,
        "trzby_podle_plateb": trzby_podle_plateb(doklady),
        "trzby_podle_druhu": trzby_podle_druhu(prodejni_doklady),
        "dph_souhrn": dph_souhrn(prodejni_doklady),
        "plu_obraty": plu_obraty(prodejni_doklady),
        "prodej_celkem": prodej_celkem,
        "vklady_celkem": vklady_celkem,
        "celkem": celkem,
        "pocet_dokladu": doklady.count(),
        "pocet_prodejnich_dokladu": prodejni_doklady.count(),
        "pocet_vkladovych_dokladu": vkladove_doklady.count(),
        "vklady_kont": vklady_kont,
        "pokladni_hotovost": (pokladna.hotovostni_zustatek or Decimal("0")) + hotovost,
    })


@login_required
def pokladna_uzaverka(request, pokladna_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    today = timezone.localdate()
    datum = parse_date(request.GET.get("datum") or "") or today
    doklady = doklady_za_obdobi(pokladna, datum, datum)
    hotovost = doklady.filter(
        zpusob_platby=PokladniDoklad.PLATBA_HOTOVOST
    ).aggregate(suma=Sum("celkem_s_dph"))["suma"] or Decimal("0")
    vklady_kont = _vklady_kont_podle_uhrady(pokladna, datum, datum)
    k_odevzdani = hotovost
    predpokladana_hotovost = (pokladna.hotovostni_zustatek or Decimal("0")) + k_odevzdani
    uzaverka = PokladniUzaverka.objects.filter(pokladna=pokladna, datum=datum).first()

    if request.method == "POST":
        datum = parse_date(request.POST.get("datum") or "") or today
        hotovost_spoctena = request.POST.get("hotovost_spoctena")
        poznamka = request.POST.get("poznamka", "")
        try:
            hotovost_spoctena_dec = Decimal(str(hotovost_spoctena or "0").replace(",", "."))
        except Exception:
            messages.error(request, "Spočtená hotovost není platné číslo.")
            return redirect("pokladna:pokladna_uzaverka", pokladna_id=pokladna.id)
        try:
            uzaverka = uzavri_denni_uzaverku(
                pokladna,
                datum,
                user=request.user,
                hotovost_spoctena=hotovost_spoctena_dec,
                poznamka=poznamka,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("pokladna:pokladna_uzaverka", pokladna_id=pokladna.id)
        messages.success(request, "Denní uzávěrka byla vytvořena.")
        return redirect("pokladna:pokladna_uzaverka_detail", pokladna_id=pokladna.id, uzaverka_id=uzaverka.id)

    return render(request, "pokladna/uzaverka.html", {
        "pokladna": pokladna,
        "datum": datum,
        "uzaverka": uzaverka,
        "trzby_podle_plateb": trzby_podle_plateb(doklady),
        "dph_souhrn": dph_souhrn(doklady),
        "pocet_dokladu": doklady.count(),
        "hotovost": hotovost,
        "vklady_kont": vklady_kont,
        "k_odevzdani": k_odevzdani,
        "predpokladana_hotovost": predpokladana_hotovost,
        "pokladni_zaklad": pokladna.hotovostni_zustatek or Decimal("0"),
    })


@login_required
def pokladna_uzaverka_detail(request, pokladna_id, uzaverka_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    uzaverka = get_object_or_404(PokladniUzaverka, pk=uzaverka_id, pokladna=pokladna)
    doklady = doklady_za_obdobi(pokladna, uzaverka.datum, uzaverka.datum).filter(uzaverka=uzaverka)
    hotovost_v_pokladne = (pokladna.hotovostni_zustatek or Decimal("0")) + (uzaverka.hotovost or Decimal("0"))
    bezhotovostne = (uzaverka.karta or Decimal("0")) + (uzaverka.qr or Decimal("0")) + (uzaverka.konto or Decimal("0"))
    return render(request, "pokladna/uzaverka_detail.html", {
        "pokladna": pokladna,
        "uzaverka": uzaverka,
        "doklady": doklady.order_by("uzavren_at", "id"),
        "trzby_podle_plateb": trzby_podle_plateb(doklady),
        "dph_souhrn": dph_souhrn(doklady),
        "hotovost_v_pokladne": hotovost_v_pokladne,
        "k_odevzdani": uzaverka.hotovost,
        "pokladni_zaklad": pokladna.hotovostni_zustatek or Decimal("0"),
        "bezhotovostne": bezhotovostne,
    })


@login_required
def pokladna_qr_platba(request, pokladna_id, doklad_id):
    pokladna = get_object_or_404(Pokladna, pk=pokladna_id, aktivni=True)
    doklad = get_object_or_404(
        PokladniDoklad.objects.select_related("pokladna", "zakaznik", "obsluha"),
        pk=doklad_id,
        pokladna=pokladna,
    )
    if request.method == "POST":
        akce = request.POST.get("akce")
        try:
            if akce == "potvrdit_qr":
                potvrdit_qr_platbu(doklad, user=request.user)
                messages.success(request, "QR platba byla potvrzena a účet uzavřen.")
                return redirect("pokladna:pokladna_view", pokladna_id=pokladna.id)
            if akce == "zrusit_qr":
                stornuj_doklad(doklad, user=request.user, duvod="Zrušená QR platba")
                messages.info(request, "QR platba byla zrušena.")
                return redirect("pokladna:pokladna_ucet", pokladna_id=pokladna.id)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])

    payload = doklad.qr_payload or ""
    return render(request, "pokladna/qr_platba.html", {
        "pokladna": pokladna,
        "doklad": doklad,
        "payload": payload,
        "qr_data_uri": qr_payload_data_uri(payload) if payload else "",
        "qr_deeplink": qr_platba_url(payload) if payload else "",
    })
