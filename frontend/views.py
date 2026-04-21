import json
import logging
import secrets

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import login
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings

from users.models import CustomUser


logger = logging.getLogger(__name__)


def _rfid_token_ok(request, data=None):
    expected = getattr(settings, "RFID_API_TOKEN", "")
    if not expected:
        return True
    supplied = (
        request.headers.get("X-RFID-Token")
        or request.headers.get("X-API-Key")
        or (data or {}).get("token")
        or ""
    )
    return secrets.compare_digest(str(supplied), str(expected))


@csrf_exempt
@require_POST
def rfid_login_api(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Neplatný JSON'}, status=400)

    if not _rfid_token_ok(request, data):
        logger.warning("Odmítnuté RFID přihlášení kvůli neplatnému tokenu.")
        return JsonResponse({'success': False, 'error': 'Neplatné oprávnění RFID terminálu.'}, status=403)

    rfid = (data.get('rfid') or '').strip()
    if not rfid:
        return JsonResponse({'success': False, 'error': 'RFID chybí'}, status=400)

    user = CustomUser.objects.filter(
        identifikacni_medium__iexact=rfid,
        is_active=True,
    ).first()
    if not user:
        logger.warning("Neúspěšný RFID login pro neznámý tag.")
        return JsonResponse({'success': False, 'error': 'Uživatel nenalezen'}, status=404)

    login(request, user)
    return JsonResponse({'success': True, 'username': user.username})


def rfid_login_page(request):
    if request.user.is_authenticated:
        return redirect('jidelnicek:dashboard')
    return render(request, 'rfid_login.html')
