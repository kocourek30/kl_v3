import json
import logging

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import login
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from users.models import CustomUser


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def rfid_login_api(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Neplatný JSON'}, status=400)

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
