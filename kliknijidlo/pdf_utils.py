import csv
import html
import os
from decimal import Decimal, InvalidOperation
from io import StringIO

from django.conf import settings
from django.utils.html import escape

from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Table, TableStyle


def register_czech_font():
    font_candidates = [
        ("DejaVuSans", os.path.join(settings.BASE_DIR, "static", "fonts", "DejaVuSans.ttf")),
        ("Arial", "arial.ttf"),
    ]
    for font_name, font_path in font_candidates:
        try:
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font_name, font_path))
            return font_name
        except Exception:
            continue
    return "Helvetica"


def czech_pdf_styles():
    font_name = register_czech_font()
    styles = getSampleStyleSheet()
    for style_name in ("Normal", "Title", "Heading1", "Heading2", "Heading3"):
        if style_name in styles:
            styles[style_name].fontName = font_name
            styles[style_name].wordWrap = "CJK"
            styles[style_name].splitLongWords = 1
    return styles, font_name


def wrap_style(font_name=None, *, bold=False, size=8, leading=None, color=None, alignment=0):
    base_font = font_name or register_czech_font()
    font = base_font
    if bold and base_font == "Helvetica":
        font = "Helvetica-Bold"
    style = ParagraphStyle(
        "WrappedCellBold" if bold else "WrappedCell",
        fontName=font,
        fontSize=size,
        leading=leading or size + 2,
        wordWrap="CJK",
        splitLongWords=1,
        alignment=alignment,
    )
    if color is not None:
        style.textColor = color
    return style


def pdf_text(value):
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return escape(text).replace("\n", "<br/>")


def pdf_cell(value, style=None):
    return Paragraph(pdf_text(value), style or wrap_style())


def pdf_table_data(rows, *, font_name=None, header=True, font_size=8):
    normal = wrap_style(font_name, size=font_size)
    bold = wrap_style(font_name, bold=True, size=font_size)
    data = []
    for row_index, row in enumerate(rows):
        style = bold if header and row_index == 0 else normal
        data.append([pdf_cell(value, style) for value in row])
    return data


def safe_table(rows, col_widths, *, font_name=None, style_commands=None, header=True, font_size=8, repeat_rows=1):
    table = Table(
        pdf_table_data(rows, font_name=font_name, header=header, font_size=font_size),
        colWidths=col_widths,
        repeatRows=repeat_rows,
    )
    if style_commands:
        table.setStyle(TableStyle(style_commands))
    return table


def html_cell(value):
    return html.escape("" if value is None else str(value))


def decimal_cs(value, *, places=2, trim=True):
    if value is None:
        number = Decimal("0")
    else:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)

    quant = Decimal("1") if places == 0 else Decimal("1").scaleb(-places)
    text = f"{number.quantize(quant):,.{places}f}".replace(",", " ").replace(".", ",")
    if trim and places:
        text = text.rstrip("0").rstrip(",")
    return text


def money_cs(value, *, currency="Kč"):
    return f"{decimal_cs(value, places=2, trim=True)} {currency}".strip()


def percent_cs(value):
    return f"{decimal_cs(value, places=2, trim=True)} %"


def csv_row(values, delimiter=";"):
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(["" if value is None else str(value).replace("\r\n", "\n").replace("\r", "\n") for value in values])
    return buffer.getvalue()
