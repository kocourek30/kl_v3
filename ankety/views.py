from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import AnketniOtazka, HodnoceniJidla, OdpovedHodnoceni
from .services import (
    anketni_prehled_uzivatele,
    mesicni_anketa_kontext,
    odevzdat_hlas_v_mesicni_ankete,
    vydane_polozky_k_hodnoceni,
)


@login_required
def moje_ankety(request):
    today = timezone.localdate()
    return render(request, "ankety/moje_ankety.html", anketni_prehled_uzivatele(request.user, today))


@login_required
def hodnotit_jidlo(request, order_item_id):
    order_item = get_object_or_404(
        vydane_polozky_k_hodnoceni(request.user),
        pk=order_item_id,
    )
    if hasattr(order_item, "hodnoceni"):
        messages.info(request, "Toto jídlo už je ohodnocené.")
        return redirect("ankety:moje_ankety")

    otazky = list(AnketniOtazka.objects.filter(aktivni=True).order_by("poradi", "id"))
    if not otazky:
        messages.warning(request, "Zatím nejsou nastavené žádné aktivní anketní otázky.")
        return redirect("ankety:moje_ankety")

    if request.method == "POST":
        chyby = []
        odpovedi = []
        for otazka in otazky:
            raw = request.POST.get(f"otazka_{otazka.id}")
            if not raw and otazka.povinna:
                chyby.append(f"Vyplň prosím otázku: {otazka.text}")
                continue
            if not raw:
                continue
            try:
                hodnota = int(raw)
            except ValueError:
                chyby.append(f"Neplatné hodnocení u otázky: {otazka.text}")
                continue
            if hodnota < 1 or hodnota > 5:
                chyby.append(f"Hodnocení musí být od 1 do 5: {otazka.text}")
                continue
            odpovedi.append((otazka, hodnota))

        if chyby:
            for chyba in chyby:
                messages.error(request, chyba)
        else:
            with transaction.atomic():
                hodnoceni = HodnoceniJidla.objects.create(
                    user=request.user,
                    order_item=order_item,
                    datum_vydeje=order_item.order.datum_vydeje,
                    jidlo_nazev=order_item.menu_item.jidlo.nazev,
                    poznamka=(request.POST.get("poznamka") or "").strip(),
                )
                OdpovedHodnoceni.objects.bulk_create([
                    OdpovedHodnoceni(hodnoceni_jidla=hodnoceni, otazka=otazka, znamka=hodnota)
                    for otazka, hodnota in odpovedi
                ])
            messages.success(request, "Děkujeme za hodnocení jídla.")
            return redirect("ankety:moje_ankety")

    return render(request, "ankety/hodnotit_jidlo.html", {
        "order_item": order_item,
        "otazky": otazky,
    })


@login_required
def mesicni_volba(request):
    today = timezone.localdate()
    if request.method == "POST":
        varianta_id = request.POST.get("varianta")
        if not varianta_id:
            messages.error(request, "Vyber prosím jednu variantu.")
            return redirect("ankety:mesicni_volba")
        result = odevzdat_hlas_v_mesicni_ankete(
            user=request.user,
            varianta_id=varianta_id,
            target_date=today,
        )
        if not result["ok"]:
            messages.warning(request, result["error"])
        else:
            messages.success(
                request,
                f"Hlas byl úspěšně odeslaný pro variantu: {result['varianta'].nazev}.",
            )
        return redirect("ankety:mesicni_volba")

    return render(
        request,
        "ankety/mesicni_volba.html",
        {"mesicni_anketa": mesicni_anketa_kontext(request.user, today)},
    )
