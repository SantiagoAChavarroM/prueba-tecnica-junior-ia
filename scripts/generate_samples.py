"""Genera archivos de ejemplo (Excel y PDF) con la estructura definida en la prueba.

Incluye a propósito algunos problemas para demostrar la validación:
registros incompletos, valores negativos, estados no reconocidos, un valor atípico,
filas repetidas entre archivos y un archivo idéntico enviado dos veces.

Uso:  python scripts/generate_samples.py
"""
from __future__ import annotations

import random
import shutil
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "samples" / "inbox"
OTHER = ROOT / "samples" / "fuera_de_formato"

random.seed(42)
CLIENTES = [f"Cliente {c}" for c in "ABCDEFGHIJKL"]
CATEGORIAS = ["Servicio A", "Servicio A", "Servicio A", "Servicio B", "Servicio B", "Producto A", "Producto B"]
ESTADOS = ["Completado"] * 6 + ["Pendiente"] * 2 + ["En proceso", "Cancelado"]
PRECIO = {"Servicio A": 150_000, "Servicio B": 320_000, "Producto A": 85_000, "Producto B": 210_000}


def random_row(day: date) -> list:
    cat = random.choice(CATEGORIAS)
    qty = random.randint(1, 20)
    return [day, random.choice(CLIENTES), cat, qty, qty * PRECIO[cat], random.choice(ESTADOS)]


def rows_between(start: date, end: date, n: int) -> list[list]:
    span = (end - start).days
    return sorted((random_row(start + timedelta(days=random.randint(0, span))) for _ in range(n)), key=lambda r: r[0])


def write_excel(path: Path, rows: list[list], title: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["Fecha", "Cliente", "Categoría", "Cantidad", "Valor", "Estado"])
    for cell in ws[3]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    for col, width in zip("ABCDEF", (12, 14, 14, 10, 14, 14)):
        ws.column_dimensions[col].width = width
    for cell in ws["A"][3:]:
        cell.number_format = "yyyy-mm-dd"
    wb.save(path)


def pdf_doc(path: Path, title: str, story: list) -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=letter, title=title)
    doc.build([Paragraph(title, styles["Title"]), Spacer(1, 12), *story])


def write_pdf_table(path: Path, rows: list[list]) -> None:
    data = [["Fecha", "Cliente", "Número de referencia", "Categoría", "Valor", "Estado"]]
    data += [[r[0].isoformat(), r[1], r[2], r[3], f"${r[4]:,.0f}".replace(",", "."), r[5]] for r in rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    styles = getSampleStyleSheet()
    pdf_doc(path, "Reporte operacional — Consolidado PDF",
            [Paragraph("Remitente: Área de Operaciones", styles["Normal"]), Spacer(1, 8), table])


def write_pdf_fichas(path: Path, fichas: list[dict]) -> None:
    styles = getSampleStyleSheet()
    story = []
    for ficha in fichas:
        for key, value in ficha.items():
            story.append(Paragraph(f"<b>{key}:</b> {value}", styles["Normal"]))
        story.append(Spacer(1, 14))
    pdf_doc(path, "Reporte operacional — Ficha de registro", story)


def main() -> None:
    for folder in (INBOX, OTHER):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)

    # 1. Excel de octubre (con algunos errores intencionales)
    octubre = rows_between(date(2026, 9, 1), date(2026, 10, 31), 45)
    octubre[5][1] = None                    # falta cliente
    octubre[12][4] = -250_000               # valor negativo
    octubre[20][5] = "Por revisar"          # estado no reconocido
    octubre[27][4] = 45_000_000             # valor atípico (sospechoso)
    octubre[33][3] = None                   # falta cantidad
    write_excel(INBOX / "reporte_operacional_2026-10.xlsx", octubre, "Reporte operacional — Octubre 2026")

    # 2. Excel de noviembre: incluye 3 filas repetidas de octubre (deben detectarse como duplicadas)
    noviembre = rows_between(date(2026, 11, 1), date(2026, 11, 9), 22) + octubre[:3]
    write_excel(INBOX / "reporte_operacional_2026-11.xlsx", noviembre, "Reporte operacional — Noviembre 2026")

    # 3. Mismo archivo reenviado con otro nombre (debe omitirse por hash)
    shutil.copy(INBOX / "reporte_operacional_2026-10.xlsx", INBOX / "reenvio_reporte_octubre.xlsx")

    # 4. PDF con tabla (incluye número de referencia, sin cantidad)
    pdf_rows = []
    for i, r in enumerate(rows_between(date(2026, 9, 15), date(2026, 11, 8), 18)):
        pdf_rows.append([r[0], r[1], f"REF-{1000 + i}", r[2], r[4], r[5]])
    pdf_rows[4][2] = ""                     # falta referencia
    write_pdf_table(INBOX / "consolidado_operaciones.pdf", pdf_rows)

    # 5. PDF tipo ficha (campo: valor), varios registros
    write_pdf_fichas(INBOX / "fichas_registro_noviembre.pdf", [
        {"Fecha": "2026-11-03", "Cliente": "Cliente B", "Número de referencia": "REF-2041",
         "Categoría": "Servicio A", "Valor": "$1.350.000", "Estado": "Completado"},
        {"Fecha": "2026-11-05", "Cliente": "Cliente F", "Número de referencia": "REF-2042",
         "Categoría": "Servicio B", "Valor": "$960.000", "Estado": "Pendiente"},
        {"Fecha": "07/11/2026", "Cliente": "Cliente K", "Número de referencia": "REF-2043",
         "Categoría": "Producto A", "Valor": "", "Estado": "En proceso"},
    ])

    # 6. PDF fuera de formato (texto libre): solo se puede leer con IA
    styles = getSampleStyleSheet()
    pdf_doc(OTHER / "nota_operaciones_texto_libre.pdf", "Novedades de la semana", [Paragraph(
        "Buenos días. Les informamos que el 6 de noviembre de 2026 se cerró con el Cliente D la orden "
        "REF-3001 de la línea Servicio B por un total de dos millones cuatrocientos mil pesos ($2.400.000); "
        "quedó completada. Adicionalmente, el Cliente H tiene pendiente la orden REF-3002 (Producto B) "
        "del 8 de noviembre de 2026, por $630.000.", styles["Normal"])])

    print("Archivos generados:")
    for path in sorted([*INBOX.iterdir(), *OTHER.iterdir()]):
        print(" -", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
