"""Reporte semanal en PDF (una página) con reportlab. app.py le pasa los números ya calculados."""
import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import motor


def clean_text(text) -> str:
    """Quita lo que Helvetica no puede dibujar (emojis) y escapa XML."""
    return escape(str(text).encode("cp1252", errors="ignore").decode("cp1252")).strip()


def rich(text) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", clean_text(text))


def build_pdf(marca: dict, semana: str, k: dict, kprev: dict, T, alertas: list, resumen: str) -> bytes:
    C = {n: colors.HexColor(v) for n, v in marca["colores"].items()}
    cfg = marca["kpis"]
    buf = io.BytesIO()
    W, H = A4
    M = 16 * mm

    def banda(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(C["acento"])
        canvas.rect(0, H - 34 * mm, W, 34 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 26)
        canvas.drawString(M, H - 18 * mm, "MARS")
        canvas.setFillColor(C["rosa"])
        canvas.drawString(M + canvas.stringWidth("MARS", "Helvetica-Bold", 26), H - 18 * mm, ".")
        canvas.setFillColor(C["acento_claro"])
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(M, H - 26 * mm, clean_text(f"REPORTE SEMANAL DE INVENTARIO EN TIENDA · {semana}"))
        canvas.setFillColor(C["gris"])
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(M, 10 * mm, clean_text(f"{marca['titulo_app']} · {marca['subtitulo']}"))
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=42 * mm, bottomMargin=16 * mm)
    base = dict(fontName="Helvetica", textColor=C["oscuro"])
    st_body = ParagraphStyle("b", fontSize=9.5, leading=13.5, **base)
    st_h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=C["acento"], spaceBefore=8, spaceAfter=4)
    st_td = ParagraphStyle("td", fontSize=8.5, leading=11, **base)
    st_th = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.white)
    st_al_t = ParagraphStyle("at", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=C["oscuro"])

    story = [Paragraph(rich(resumen), st_body), Spacer(1, 4)]

    story.append(Paragraph("Los 7 indicadores", st_h))
    filas = [[Paragraph(x, st_th) for x in ["Indicador", "Esta semana", "Semana anterior", "Meta", "Estado"]]]
    color_estado = {"verde": C["verde"], "amarillo": C["amarillo"], "rojo": C["rojo"], "gris": C["gris"]}
    estilos = []
    for i, kp in enumerate(motor.ORDEN_KPIS, start=1):
        e, txt = motor.estado(kp, k.get(kp), cfg[kp], (kprev or {}).get(kp))
        meta = "Sin caer" if kp == "venta" else ("máx. " if not cfg[kp]["mas_es_mejor"] else "mín. ") + motor.fmt(kp, cfg[kp]["meta"])
        filas.append([Paragraph(clean_text(cfg[kp]["nombre"]), st_td), motor.fmt(kp, k.get(kp)),
                      motor.fmt(kp, (kprev or {}).get(kp)), clean_text(meta), clean_text(txt)])
        estilos.append(("TEXTCOLOR", (4, i), (4, i), color_estado[e]))
    t = Table(filas, colWidths=[58 * mm, 28 * mm, 30 * mm, 28 * mm, 34 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C["acento"]), ("FONTNAME", (1, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (1, 1), (-1, -1), 9), ("FONTNAME", (4, 1), (4, -1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C["claro"]]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, C["linea"]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ] + estilos))
    story.append(t)

    story.append(Paragraph("Las 8 tiendas más urgentes", st_h))
    f2 = [[Paragraph(x, st_th) for x in ["Tienda", "Cadena", "Estado", "Se visita", "Urgencia", "Piezas estancadas"]]]
    for _, r in T.head(8).iterrows():
        f2.append([Paragraph(clean_text(r["nombre_tienda"]), st_td), clean_text(r["cadena"]), clean_text(r["estado"]),
                   clean_text(r["se_visita"]), f"{r['urgencia']:.0f}", f"{r['estancado']:,.0f}"])
    t2 = Table(f2, colWidths=[52 * mm, 25 * mm, 35 * mm, 20 * mm, 20 * mm, 26 * mm])
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C["acento"]), ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C["claro"]]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, C["linea"]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t2)

    story.append(Paragraph("Qué atender primero", st_h))
    etiqueta = {"🔴": "URGENTE", "🟠": "IMPORTANTE", "🟢": "BIEN"}
    for prio, titulo, texto in alertas[:3]:
        story.append(Paragraph(clean_text(f"{etiqueta[motor.nivel(prio)]} · {titulo}"), st_al_t))
        story.append(Paragraph(clean_text(texto), st_body))
        story.append(Spacer(1, 4))

    doc.build(story, onFirstPage=banda, onLaterPages=banda)
    return buf.getvalue()
