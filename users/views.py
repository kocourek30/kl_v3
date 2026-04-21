import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
import os

from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.http import HttpResponse
from django.conf import settings
from django.utils import timezone

from objednavky.models import OrderItem, Order
from jidelnicek.models import Alergen
from dotace.models import SkupinoveNastaveni

logger = logging.getLogger(__name__)

# ✅ REPORTLAB IMPORTY
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_CENTER, TA_RIGHT


ORDER_TOTAL_EXPR = ExpressionWrapper(
    F('quantity') * F('cena'),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


def get_user_settings(user):
    """Nastavení konta podle první skupiny s nastavením."""
    try:
        nastaveni = SkupinoveNastaveni.objects.filter(
            skupina__in=user.groups.all()
        ).first()

        if nastaveni:
            return {
                'cerpani_debit': nastaveni.cerpani_debit,
                'debit_limit': nastaveni.debit_limit or Decimal('0'),
            }
    except Exception:
        logger.exception("Chyba při načítání nastavení konta uživatele.")

    return {
        'cerpani_debit': False,
        'debit_limit': Decimal('0'),
    }


def get_balance_breakdown(user):
    """Rychlý souhrn konta používaný v profilu a historiích."""
    try:
        vklady_celkem = user.vklady.filter(castka__gt=0).aggregate(
            soucet=Sum('castka')
        )['soucet'] or Decimal('0')
        platby_uctu = user.vklady.filter(castka__lt=0).aggregate(
            soucet=Sum('castka')
        )['soucet'] or Decimal('0')

        vydane_objednavky = OrderItem.objects.filter(
            order__user=user,
            order__status__in=['vydano', 'nevyzvednuto']
        ).aggregate(
            total=Sum(ORDER_TOTAL_EXPR)
        )['total'] or Decimal('0')

        zalohy_objednavky = OrderItem.objects.filter(
            order__user=user,
            order__status__in=['objednano', 'zalozena-obsluhou']
        ).aggregate(
            total=Sum(ORDER_TOTAL_EXPR)
        )['total'] or Decimal('0')

        celkem = vklady_celkem + platby_uctu - vydane_objednavky - zalohy_objednavky

        return {
            'vklady': vklady_celkem,
            'platby_uctu': platby_uctu,
            'vydane': vydane_objednavky,
            'zalohy': zalohy_objednavky,
            'celkem': celkem,
        }
    except Exception:
        logger.exception("Chyba při výpočtu rozpisu konta uživatele.")
        return {
            'vklady': Decimal('0'),
            'platby_uctu': Decimal('0'),
            'vydane': Decimal('0'),
            'zalohy': Decimal('0'),
            'celkem': Decimal('0'),
        }


def get_user_page_context(user):
    balance_breakdown = get_balance_breakdown(user)
    return {
        'balance_breakdown': balance_breakdown,
        'user_balance': balance_breakdown['celkem'],
        'user_settings': get_user_settings(user),
    }


def get_period_from_request(request):
    """Vrátí filtrovací období pro zákaznické přehledy."""
    filter_type = request.GET.get('filter') or 'current_month'
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    now = timezone.now()

    if filter_type == 'all':
        return filter_type, date_from, date_to, None, None

    if filter_type == 'current_month':
        return filter_type, date_from, date_to, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now

    if filter_type == 'last_month':
        first_day_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_last_month = first_day_current_month - timedelta(days=1)
        start_date = last_day_last_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = last_day_last_month.replace(hour=23, minute=59, second=59)
        return filter_type, date_from, date_to, start_date, end_date

    if filter_type == 'current_year':
        return filter_type, date_from, date_to, now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), now

    if filter_type == 'custom':
        try:
            start_date = timezone.make_aware(datetime.strptime(date_from, '%Y-%m-%d'))
            end_date = timezone.make_aware(datetime.strptime(date_to, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
            if start_date > end_date:
                raise ValueError("Začátek období je po konci období.")
            return filter_type, date_from, date_to, start_date, end_date
        except Exception:
            messages.warning(request, "Zadané vlastní období není platné, zobrazuje se aktuální měsíc.")

    return 'current_month', '', '', now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now

@login_required
def user_profile_view(request):
    user = request.user
    all_alergeny = Alergen.objects.order_by('nazev')
    selected_alergen_ids = set(user.alergeny.values_list('id', flat=True))
    
    if request.method == 'POST':
        has_errors = False
        new_email = (request.POST.get('email') or '').strip()
        alergeny_ids = request.POST.getlist('alergeny')
        valid_alergen_ids = set(all_alergeny.filter(id__in=alergeny_ids).values_list('id', flat=True))

        try:
            validate_email(new_email)
        except ValidationError:
            messages.error(request, 'Zadejte prosím platný email.')
            has_errors = True

        if len(valid_alergen_ids) != len(set(alergeny_ids)):
            messages.error(request, 'Některý z vybraných alergenů není platný.')
            has_errors = True

        current_password = request.POST.get('current_password')
        new_password1 = request.POST.get('new_password1')
        new_password2 = request.POST.get('new_password2')

        if current_password or new_password1 or new_password2:
            if not current_password:
                messages.error(request, 'Zadejte prosím aktuální heslo.')
            elif not user.check_password(current_password):
                messages.error(request, 'Aktuální heslo není správné.')
                has_errors = True
            elif not new_password1 or not new_password2:
                messages.error(request, 'Vyplňte prosím obě pole pro nové heslo.')
                has_errors = True
            elif new_password1 != new_password2:
                messages.error(request, 'Nová hesla se neshodují.')
                has_errors = True
            else:
                try:
                    validate_password(new_password1, user=user)
                except ValidationError as exc:
                    for error in exc.messages:
                        messages.error(request, error)
                    has_errors = True

        if has_errors:
            context = {
                'user': user,
                'all_alergeny': all_alergeny,
                'selected_alergen_ids': valid_alergen_ids or selected_alergen_ids,
                **get_user_page_context(user),
            }
            return render(request, 'users/profile.html', context)

        user.email = new_email
        user.alergeny.set(valid_alergen_ids)

        user.save()
        if current_password or new_password1 or new_password2:
            user.set_password(new_password1)
            user.save(update_fields=['password'])
            update_session_auth_hash(request, user)
            messages.success(request, 'Heslo bylo úspěšně změněno.')

        messages.success(request, 'Profil byl uložen.')
        return redirect('users:user-profile')

    context = {
        'user': user,
        'all_alergeny': all_alergeny,
        'selected_alergen_ids': selected_alergen_ids,
        **get_user_page_context(user),
    }
    return render(request, 'users/profile.html', context)


@login_required
def consumption_history_view(request):
    """Historie konzumace s filtrováním."""
    user = request.user
    filter_type, date_from, date_to, start_date, end_date = get_period_from_request(request)

    orders_query = Order.objects.filter(
        user=user,
        status__in=['vydano', 'nevyzvednuto']
    ).select_related(
        'user'
    ).prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    )

    if start_date and end_date:
        orders_query = orders_query.filter(
            datum_vydeje__gte=start_date.date(),
            datum_vydeje__lte=end_date.date(),
        )

    orders = orders_query.order_by('-datum_vydeje', '-created_at')
    orders_with_totals = []
    total_amount = Decimal('0')

    for order in orders:
        order_items = order.items.all()
        total = sum(item.quantity * item.cena for item in order_items)
        total_amount += total

        orders_with_totals.append({
            'order': order,
            'items': order_items,
            'total': total
        })

    context = {
        'user': user,
        'orders_with_totals': orders_with_totals,
        'filter_type': filter_type,
        'date_from': date_from,
        'date_to': date_to,
        'total_amount': total_amount,
        **get_user_page_context(user),
    }
    return render(request, 'users/consumption_history.html', context)

@login_required
def receipt_pdf_view(request, order_id):
    """Zobrazení HTML účtenky."""
    user = request.user
    order = get_object_or_404(
        Order.objects.prefetch_related(
            'items__menu_item__jidlo',
            'items__menu_item__druh_jidla',
        ),
        id=order_id,
        user=user,
        status__in=['vydano', 'nevyzvednuto'],
    )
    
    order_items_with_totals = []
    total = Decimal('0')
    
    for item in order.items.all():
        item_total = item.quantity * item.cena
        order_items_with_totals.append({
            'item': item,
            'total': item_total
        })
        total += item_total
    
    context = {
        'order': order,
        'order_items_with_totals': order_items_with_totals,
        'total': total,
        'user': user,
    }
    
    return render(request, 'users/receipt.html', context)


@login_required
def receipt_pdf_download(request, order_id):
    """Stažení účtenky jako PDF s ReportLab a českou podporou."""
    user = request.user
    order = get_object_or_404(
        Order.objects.prefetch_related(
            'items__menu_item__jidlo',
            'items__menu_item__druh_jidla',
        ),
        id=order_id,
        user=user,
        status__in=['vydano', 'nevyzvednuto'],
    )
    
    # Připrav data
    order_items_with_totals = []
    total = Decimal('0')
    
    for item in order.items.all():
        item_total = item.quantity * item.cena
        order_items_with_totals.append({
            'item': item,
            'total': item_total
        })
        total += item_total
    
    # Vytvoř PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm)
    
    # ✅ Registruj DejaVu Sans fonty z projektu
    font_path = os.path.join(settings.BASE_DIR, 'static', 'fonts')
    
    try:
        # Zkus načíst fonty z projektu
        dejavu_path = os.path.join(font_path, 'DejaVuSans.ttf')
        dejavu_bold_path = os.path.join(font_path, 'DejaVuSans-Bold.ttf')
        
        if os.path.exists(dejavu_path):
            pdfmetrics.registerFont(TTFont('DejaVu', dejavu_path))
        if os.path.exists(dejavu_bold_path):
            pdfmetrics.registerFont(TTFont('DejaVu-Bold', dejavu_bold_path))
        
        font_name = 'DejaVu'
        font_bold = 'DejaVu-Bold'
    except Exception:
        logger.exception("Chyba při načítání fontů pro PDF účtenku.")
        # Fallback - použij Times-Roman (má lepší podporu než Helvetica)
        font_name = 'Times-Roman'
        font_bold = 'Times-Bold'
    
    # Styly
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=font_bold,
        fontSize=18,
        textColor=colors.HexColor('#54ae43'),
        alignment=TA_CENTER,
        spaceAfter=10
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10,
        textColor=colors.gray,
        alignment=TA_CENTER,
        spaceAfter=20
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10
    )
    
    # Obsah PDF
    elements = []
    
    # Hlavička
    elements.append(Paragraph("Výdejní účtenka", title_style))
    elements.append(Paragraph("Školní jídelna", subtitle_style))
    elements.append(Spacer(1, 0.5*cm))
    
    # Informace o objednávce
    info_data = [
        ['Uživatel:', f"{user.first_name} {user.last_name}"],
        ['Osobní číslo:', str(user.osobni_cislo)],
        ['Datum výdeje:', order.datum_vydeje.strftime('%d.%m.%Y')],
        ['Datum objednání:', order.created_at.strftime('%d.%m.%Y %H:%M')],
        ['Status:', 'Vydáno' if order.status == 'vydano' else 'Nevyzvednuto'],
    ]
    
    info_table = Table(info_data, colWidths=[5*cm, 10*cm])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), font_bold),
        ('FONTNAME', (1, 0), (1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    
    elements.append(info_table)
    elements.append(Spacer(1, 0.8*cm))
    
    # Tabulka položek
    items_data = [['Druh jídla', 'Název', 'Počet', 'Cena/ks', 'Celkem']]
    
    for item_data in order_items_with_totals:
        item = item_data['item']
        items_data.append([
            item.menu_item.druh_jidla.nazev,
            item.menu_item.jidlo.nazev,
            str(item.quantity),
            f"{item.cena:.2f} Kč",
            f"{item_data['total']:.2f} Kč"
        ])
    
    # Řádek CELKEM
    items_data.append(['', '', '', 'CELKEM K ÚHRADĚ:', f"{total:.2f} Kč"])
    
    items_table = Table(items_data, colWidths=[3*cm, 6*cm, 2*cm, 3*cm, 3*cm])
    items_table.setStyle(TableStyle([
        # Hlavička
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#54ae43')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), font_bold),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        
        # Řádky s daty
        ('FONTNAME', (0, 1), (-1, -2), font_name),
        ('FONTSIZE', (0, 1), (-1, -2), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 8),
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.lightgrey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#f9f9f9')]),
        
        # Řádek CELKEM
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e8f5e9')),
        ('FONTNAME', (0, -1), (-1, -1), font_bold),
        ('FONTSIZE', (0, -1), (-1, -1), 11),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.HexColor('#54ae43')),
        ('TOPPADDING', (0, -1), (-1, -1), 12),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 12),
        
        # Obecné
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    
    elements.append(items_table)
    elements.append(Spacer(1, 1*cm))
    
    # Patička
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=8,
        textColor=colors.gray,
        alignment=TA_CENTER
    )
    
    elements.append(Paragraph(f"Vytištěno: {order.created_at.strftime('%d.%m.%Y %H:%M')}", footer_style))
    elements.append(Paragraph("Děkujeme za Vaši návštěvu!", footer_style))
    
    # Vygeneruj PDF
    doc.build(elements)
    
    # Vrať response
    pdf_value = buffer.getvalue()
    buffer.close()
    
    response = HttpResponse(pdf_value, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="uctenka_{order.datum_vydeje.strftime("%Y%m%d")}_{order.id}.pdf"'
    
    return response

@login_required
def account_history_view(request):
    """Historie konta s filtrováním."""
    user = request.user
    filter_type, date_from, date_to, start_date, end_date = get_period_from_request(request)

    vklady_query = user.vklady.prefetch_related('pokladni_doklady')
    if start_date and end_date:
        vklady_query = vklady_query.filter(datum__gte=start_date, datum__lte=end_date)
    vklady = vklady_query.order_by('-datum')

    orders_query = Order.objects.filter(
        user=user,
        status__in=['vydano', 'nevyzvednuto']
    ).select_related(
        'user'
    ).prefetch_related(
        'items__menu_item__jidlo',
        'items__menu_item__druh_jidla'
    )

    if start_date and end_date:
        orders_query = orders_query.filter(
            datum_vydeje__gte=start_date.date(),
            datum_vydeje__lte=end_date.date(),
        )

    orders = orders_query.order_by('-datum_vydeje', '-created_at')
    transactions = []
    total_vklady = Decimal('0')
    total_platby_uctu = Decimal('0')
    total_cerpani = Decimal('0')

    for vklad in vklady:
        pokladni_doklad = vklad.pokladni_doklady.filter(stav='UZAVRENO').first()
        je_platba_uctu = vklad.castka < 0
        if je_platba_uctu:
            total_platby_uctu += abs(vklad.castka)
        else:
            total_vklady += vklad.castka

        transactions.append({
            'datum': vklad.datum,
            'typ': 'platba_uctu' if je_platba_uctu else 'vklad',
            'popis': vklad.poznamka or 'Vklad na konto',
            'castka': vklad.castka,
            'delta': vklad.castka,
            'order': None,
            'pokladni_doklad': pokladni_doklad,
        })

    for order in orders:
        order_items = order.items.all()
        total = sum(item.quantity * item.cena for item in order_items)
        total_cerpani += total

        items_list = [f"{item.menu_item.jidlo.nazev} ({item.quantity}×)" for item in order_items]
        popis = ', '.join(items_list) if items_list else 'Objednávka'

        if order.datum_vydeje:
            if isinstance(order.datum_vydeje, datetime):
                datum = order.datum_vydeje
            else:
                datum = datetime.combine(order.datum_vydeje, time.min)
                if timezone.is_aware(order.created_at):
                    datum = timezone.make_aware(datum)
        else:
            datum = order.created_at
        
        transactions.append({
            'datum': datum,
            'typ': 'cerpani',
            'popis': popis,
            'castka': total,
            'delta': -total,
            'order': order,
            'pokladni_doklad': None,
        })

    transactions.sort(key=lambda x: x['datum'], reverse=True)
    current_balance = user.aktualni_zustatek

    for transaction in transactions:
        transaction['zustatek_po'] = current_balance

        current_balance -= transaction['delta']

    bilance = total_vklady - total_platby_uctu - total_cerpani

    context = {
        'user': user,
        'transactions': transactions,
        'filter_type': filter_type,
        'date_from': date_from,
        'date_to': date_to,
        'total_vklady': total_vklady,
        'total_platby_uctu': total_platby_uctu,
        'total_cerpani': total_cerpani,
        'bilance': bilance,
        **get_user_page_context(user),
    }
    return render(request, 'users/account_history.html', context)


def logout_view(request):
    from django.contrib.auth import logout
    logout(request)
    return redirect('/')
