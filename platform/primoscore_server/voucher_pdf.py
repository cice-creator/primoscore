"""Primoscore 55 mm front/back card, 3 mm bleed; no arbitrary QR destinations."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

BRAND=Path(__file__).parent/'brand'
pdfmetrics.registerFont(TTFont('PS',str(BRAND/'Manrope.ttf')))
pdfmetrics.registerFont(TTFont('PS-Bold',str(BRAND/'Manrope-Bold.ttf')))
NAVY=HexColor('#142D4E');TEAL=HexColor('#087F8C');AQUA=HexColor('#DDF4F1');PAPER=HexColor('#F6F8FB')


def card(label,url,*,local=False):
    output=BytesIO();size=61*mm;c=canvas.Canvas(output,pagesize=(size,size))
    c.setTitle('Primoscore · Voucher');c.setAuthor('Primoscore')
    def background():
        c.setFillColor(PAPER);c.rect(0,0,size,size,fill=1,stroke=0)
    def text(value,x,y,font='PS',points=8,color=NAVY):
        c.setFillColor(color);c.setFont(font,points);c.drawString(x*mm,y*mm,value)
    background();c.drawImage(str(BRAND/'logo.png'),7*mm,47*mm,width=34*mm,height=8*mm,preserveAspectRatio=True,anchor='sw',mask='auto')
    c.setFillColor(AQUA);c.roundRect(38*mm,-8*mm,32*mm,40*mm,8*mm,fill=1,stroke=0)
    c.setFillColor(TEAL);c.roundRect(47*mm,-14*mm,30*mm,37*mm,8*mm,fill=1,stroke=0)
    text('Il primo',7,37,'PS-Bold',17);text('passo, insieme.',7,29,'PS-Bold',15,TEAL)
    text('Le opportunità iniziano',7,20,points=7);text('dalle persone.',7,16,points=7)
    text('CONSULENTI, PIÙ OPPORTUNITÀ.',7,8,points=4.9)
    if local:text('ANTEPRIMA LOCALE · NON DISTRIBUIRE',7,5,points=4.1)
    c.showPage();background()
    text('Un nuovo inizio.',7,51,'PS-Bold',12)
    # Fit two lines even for long names without intruding into the QR quiet zone.
    style=ParagraphStyle('label',fontName='PS',fontSize=6.5,leading=7,textColor=NAVY)
    shortened=label
    while True:
        display=shortened+('…' if shortened!=label else '')
        p=Paragraph(escape(display),style)
        _,h=p.wrap(47*mm,10*mm)
        if h<=14 or not shortened:break
        shortened=shortened.rsplit(' ',1)[0] if ' ' in shortened else shortened[:-1]
    p.drawOn(c,7*mm,47*mm-h)
    widget=QrCodeWidget(url,barLevel='M',barBorder=4);x0,y0,x1,y1=widget.getBounds();qrsize=30*mm
    drawing=Drawing(qrsize,qrsize,transform=[qrsize/(x1-x0),0,0,qrsize/(y1-y0),0,0]);drawing.add(widget)
    renderPDF.draw(drawing,c,15.5*mm,11*mm)
    text('Inquadra e contatta il tuo consulente.',7,8,points=5.5)
    text('QR LOCALE · SOLO PER LA PROVA' if local else 'PRIMOSCORE · VOUCHER',7,5,points=4.4,color=TEAL)
    c.save();output.seek(0);writer=PdfWriter()
    for page in PdfReader(output).pages:
        page.trimbox=RectangleObject([3*mm,3*mm,58*mm,58*mm]);page.bleedbox=RectangleObject([0,0,size,size]);writer.add_page(page)
    final=BytesIO();writer.write(final);return final.getvalue()
