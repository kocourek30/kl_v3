from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
from datetime import datetime
import os

from kliknijidlo.pdf_utils import safe_table


# Registrace českého fontu
def register_fonts():
    """Registruje fonty s podporou češtiny"""
    try:
        # Pokus o registraci DejaVuSans (většinou je k dispozici)
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase import pdfmetrics
        
        # DejaVu Sans - normální
        pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', 'DejaVuSans-Bold.ttf'))
        return 'DejaVuSans'
    except:
        # Fallback - zkus systémové fonty Windows
        try:
            pdfmetrics.registerFont(TTFont('Arial', 'arial.ttf'))
            pdfmetrics.registerFont(TTFont('Arial-Bold', 'arialbd.ttf'))
            return 'Arial'
        except:
            # Poslední fallback - použij Times
            return 'Times-Roman'


def generuj_pdf_uctenka(uctenka):
    """
    Generuje PDF účtenku pro danou VydejniUctenka
    Vrací BytesIO objekt s PDF
    """
    buffer = BytesIO()
    
    # Registruj fonty
    base_font = register_fonts()
    bold_font = f'{base_font}-Bold' if base_font != 'Times-Roman' else 'Times-Bold'
    
    # Vytvoř PDF dokument
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    # Story pro obsah
    story = []
    
    # Styly
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Normal'],
        fontSize=20,
        textColor=colors.HexColor('#1F2121'),
        spaceAfter=10,
        alignment=TA_CENTER,
        fontName=bold_font,
        leading=24
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#1F2121'),
        spaceAfter=6,
        alignment=TA_LEFT,
        fontName=base_font,
        leading=14
    )
    
    bold_style = ParagraphStyle(
        'CustomBold',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#1F2121'),
        spaceAfter=6,
        alignment=TA_LEFT,
        fontName=bold_font,
        leading=14
    )
    
    # Nadpis
    story.append(Paragraph("VÝDEJNÍ ÚČTENKA", title_style))
    story.append(Paragraph(f"#{uctenka.id}", normal_style))
    story.append(Spacer(1, 0.7*cm))
    
    # Základní informace
    zakaznik = uctenka.order.user.get_full_name() or uctenka.order.user.username
    
    # Info tabulka
    info_data = [
        ['Zákazník:', zakaznik],
        ['Datum objednávky:', uctenka.order.created_at.strftime('%d.%m.%Y %H:%M')],
        ['Datum výdeje:', uctenka.datum_vydeje.strftime('%d.%m.%Y %H:%M')],
    ]
    
    if uctenka.vydal:
        vydal_jmeno = uctenka.vydal.get_full_name() or uctenka.vydal.username
        info_data.append(['Vydal:', vydal_jmeno])
    
    info_table = safe_table(
        info_data,
        [4*cm, 12*cm],
        font_name=base_font,
        header=False,
        font_size=10,
        style_commands=[
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ],
    )
    
    story.append(info_table)
    story.append(Spacer(1, 0.8*cm))
    
    # Tabulka s položkami
    data = [
        ['Položka', 'Druh', 'Ks', 'Cena/ks', 'Dotace/ks', 'Celkem']
    ]
    
    for polozka in uctenka.polozky.all():
        data.append([
            polozka.nazev_jidla,
            polozka.druh_jidla,
            str(polozka.mnozstvi),
            f"{polozka.cena_za_kus} Kč",
            f"{polozka.dotace_za_kus} Kč",
            f"{polozka.celkova_cena()} Kč"
        ])
    
    # Prázdný řádek před součty
    data.append(['', '', '', '', '', ''])
    
    # Řádky se součty
    data.append(['', '', '', '', 'CELKEM:', f"{uctenka.celkova_cena} Kč"])
    data.append(['', '', '', '', 'Celková dotace:', f"{uctenka.celkova_dotace} Kč"])
    
    table = safe_table(
        data,
        [5*cm, 2.5*cm, 1.2*cm, 2*cm, 2.5*cm, 2.5*cm],
        font_name=base_font,
        font_size=8,
        style_commands=[
        # Hlavička
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32B8C6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'TOP'),
        
        # Obsah
        ('FONTSIZE', (0, 1), (-1, -4), 8),
        ('ALIGN', (0, 1), (1, -4), 'LEFT'),
        ('ALIGN', (2, 1), (-1, -4), 'CENTER'),
        ('ALIGN', (-1, 1), (-1, -4), 'RIGHT'),
        
        # Prázdný řádek
        ('LINEABOVE', (0, -3), (-1, -3), 1, colors.HexColor('#CCCCCC')),
        
        # Součty
        ('FONTSIZE', (0, -2), (-1, -1), 9),
        ('ALIGN', (-2, -2), (-2, -1), 'RIGHT'),
        ('ALIGN', (-1, -2), (-1, -1), 'RIGHT'),
        ('BACKGROUND', (0, -2), (-1, -1), colors.HexColor('#F5F5F5')),
        
        # Okraje
        ('GRID', (0, 0), (-1, -4), 1, colors.HexColor('#CCCCCC')),
        ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#333333')),
        
        # Padding
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ],
    )
    
    story.append(table)
    story.append(Spacer(1, 0.8*cm))
    
    # Poznámka
    if uctenka.poznamka:
        story.append(Paragraph(f"<b>Poznámka:</b> {uctenka.poznamka}", normal_style))
        story.append(Spacer(1, 0.3*cm))
    
    # Patička
    story.append(Spacer(1, 0.5*cm))
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey,
        alignment=TA_CENTER,
        fontName=base_font,
    )
    story.append(Paragraph(
        f"Vygenerováno: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        footer_style
    ))
    
    # Vybuildi PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

def generuj_pdf_kuchyne(datum_vydeje, stats, total_objednavek, total_porci, uzavirka_info):
    """
    Generuje PDF přehled pro kuchyni
    """
    buffer = BytesIO()
    
    base_font = register_fonts()
    bold_font = f'{base_font}-Bold' if base_font != 'Times-Roman' else 'Times-Bold'
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Normal'],
        fontSize=22,
        textColor=colors.HexColor('#1F2121'),
        spaceAfter=10,
        alignment=TA_CENTER,
        fontName=bold_font,
        leading=26
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=14,
        textColor=colors.HexColor('#666666'),
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName=base_font,
        leading=16
    )
    
    header_style = ParagraphStyle(
        'Header',
        parent=styles['Normal'],
        fontSize=16,
        textColor=colors.HexColor('#1F2121'),
        spaceAfter=10,
        fontName=bold_font,
        leading=20
    )
    
    # Nadpis
    story.append(Paragraph("PŘEHLED OBJEDNANÝCH JÍDEL PRO KUCHYNI", title_style))
    story.append(Paragraph(f"Datum: {datum_vydeje.strftime('%d.%m.%Y (%A)')}", subtitle_style))
    story.append(Spacer(1, 0.5*cm))
    
    # Summary box
    summary_data = [
        ['Celkem objednávek:', str(total_objednavek)],
        ['Celkem porcí:', str(total_porci)],
        ['Stav uzávěrky:', uzavirka_info['uzavreno_text']]
    ]
    
    summary_table = safe_table(
        summary_data,
        [6*cm, 10*cm],
        font_name=base_font,
        header=False,
        font_size=11,
        style_commands=[
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#E8F5F7')),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#32B8C6')),
        ],
    )
    
    story.append(summary_table)
    story.append(Spacer(1, 1*cm))
    
    # Jídla podle druhů
    for druh, jidla in stats.items():
        story.append(Paragraph(druh.upper(), header_style))
        
        table_data = [['Jídlo', 'Počet porcí']]
        
        for jidlo_nazev, data in jidla.items():
            table_data.append([jidlo_nazev, str(data['celkem'])])
        
        jidlo_table = safe_table(
            table_data,
            [12*cm, 4*cm],
            font_name=base_font,
            font_size=10,
            style_commands=[
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#32B8C6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#CCCCCC')),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F9F9F9')]),
            ],
        )
        
        story.append(jidlo_table)
        story.append(Spacer(1, 0.8*cm))
    
    # Patička
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey,
        alignment=TA_CENTER,
        fontName=base_font,
    )
    story.append(Paragraph(
        f"Vygenerováno: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        footer_style
    ))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

