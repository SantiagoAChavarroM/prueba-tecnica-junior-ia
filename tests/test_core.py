"""Tests de las partes deterministas: normalización, validación, parsers, deduplicación y consultas."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import pytest
from openpyxl import Workbook

from src import ai, db
from src.gmail_client import Attachment, attachment_type, sender_is_authorized
from src.parsers.common import normalize_header, normalize_record, parse_amount, parse_date
from src.parsers.excel_parser import parse_excel
from src.pipeline import RunStats, process_attachment
from src.validation import record_key, validate_record


# ------------------------------------------------------------------ normalización
@pytest.mark.parametrize("text,expected", [
    ("1500", 1500.0), ("$1.500.000", 1_500_000.0), ("1,500.50", 1500.5),
    ("1.500,50", 1500.5), ("12,5", 12.5), (2500, 2500.0), ("-250.000", -250_000.0), ("", None),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_parse_amount_invalid():
    with pytest.raises(ValueError):
        parse_amount("mil pesos")


def test_parse_date_formats():
    assert parse_date("2026-10-15") == date(2026, 10, 15)
    assert parse_date("15/10/2026") == date(2026, 10, 15)
    with pytest.raises(ValueError):
        parse_date("2026-13-40")


def test_headers_with_accents_and_synonyms():
    assert normalize_header("Categoría") == "categoria"
    assert normalize_header("Número de referencia") == "referencia"
    assert normalize_header("  VALOR ") == "valor"
    assert normalize_header("Otra cosa") is None


def test_example_row_from_statement():
    """Fila de ejemplo del enunciado: 2026-10-15 | Cliente A | Servicio A | 10 | 1500 | Completado"""
    rec, errors = normalize_record({"fecha": "2026-10-15", "cliente": "Cliente A", "categoria": "Servicio A",
                                    "cantidad": 10, "valor": 1500, "estado": "completado"})
    assert errors == []
    assert rec["estado"] == "Completado" and rec["valor"] == 1500.0 and rec["cantidad"] == 10
    assert validate_record(rec, "excel", errors, today=date(2026, 11, 10)) == []


# ------------------------------------------------------------------ validación
def test_validation_detects_problems():
    rec, errors = normalize_record({"fecha": "2027-05-01", "cliente": None, "categoria": "Servicio A",
                                    "valor": "-5", "estado": "Por revisar"})
    issues = validate_record(rec, "pdf", errors, today=date(2026, 11, 10))
    joined = " | ".join(issues)
    assert "falta cliente" in joined and "falta referencia" in joined
    assert "valor menor o igual a cero" in joined and "fecha futura" in joined
    assert "estado no reconocido" in joined


def test_record_key_is_stable():
    a = {"fecha": date(2026, 10, 1), "cliente": "Cliente A", "valor": 10.0, "estado": "Completado"}
    b = {"fecha": date(2026, 10, 1), "cliente": "cliente a", "valor": 10, "estado": "completado"}
    assert record_key(a) == record_key(b)


# ------------------------------------------------------------------ correo
def test_sender_validation():
    assert sender_is_authorized('"Reportes" <Reportes@Empresa.com>', "reportes@empresa.com")
    assert not sender_is_authorized("reportes@empresa.com.falso.co", "reportes@empresa.com")
    assert not sender_is_authorized("otro@empresa.com", "reportes@empresa.com")


def test_attachment_types():
    assert attachment_type("Reporte.PDF", None) == "pdf"
    assert attachment_type("datos.xlsx", None) == "excel"
    assert attachment_type("foto.png", "image/png") is None


# ------------------------------------------------------------------ parsers + pipeline
def _excel_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["Reporte"])
    ws.append(["Fecha", "Cliente", "Categoría", "Cantidad", "Valor", "Estado"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_finds_header_below_title():
    rows = parse_excel(_excel_bytes([["2026-10-15", "Cliente A", "Servicio A", 10, 1500, "Completado"]]))
    assert rows == [{"fecha": "2026-10-15", "cliente": "Cliente A", "categoria": "Servicio A",
                     "cantidad": 10, "valor": 1500, "estado": "Completado"}]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "dl"))
    monkeypatch.setenv("GEMINI_API_KEY", "")
    from src.config import get_settings
    settings = get_settings()
    return db.get_engine(settings.database_url), settings


def test_no_duplicates_across_files(engine):
    eng, settings = engine
    row = ["2026-10-15", "Cliente A", "Servicio A", 10, 1500, "Completado"]
    stats = RunStats()
    process_attachment(eng, Attachment("a.xlsx", "excel", _excel_bytes([row])), "m1", settings, stats)
    # mismo archivo otra vez -> se omite por hash
    process_attachment(eng, Attachment("a_copia.xlsx", "excel", _excel_bytes([row])), "m2", settings, stats)
    # archivo distinto con la misma fila + una nueva -> solo inserta la nueva
    other = _excel_bytes([row, ["2026-10-16", "Cliente B", "Servicio B", 2, 900, "Pendiente"]])
    process_attachment(eng, Attachment("b.xlsx", "excel", other), "m3", settings, stats)
    assert db.count_records(eng) == 2
    assert stats.records_inserted == 2 and stats.records_duplicated == 1
    assert stats.files_skipped in (0, 1)  # openpyxl puede variar metadatos entre guardados


def test_corrupt_file_is_logged_not_raised(engine):
    eng, settings = engine
    stats = RunStats()
    process_attachment(eng, Attachment("roto.pdf", "pdf", b"esto no es un pdf"), "m1", settings, stats)
    assert stats.errors == 1 and stats.alerts
    files = db.read_table(eng, db.processed_files)
    assert files.iloc[0]["status"] == "error"


# ------------------------------------------------------------------ IA (sin llamar al modelo)
def _df():
    return pd.DataFrame({
        "id": [1, 2, 3, 4],
        "fecha": pd.to_datetime(["2026-10-01", "2026-10-15", "2026-11-02", "2026-11-03"]),
        "cliente": ["Cliente A", "Cliente B", "Cliente A", "Cliente C"],
        "referencia": [None, "REF-1", "REF-2", None],
        "categoria": ["Servicio A", "Servicio B", "Servicio A", "Servicio A"],
        "cantidad": [1, None, 2, 3],
        "valor": [100.0, 200.0, 300.0, 400.0],
        "estado": ["Completado", "Pendiente", "Pendiente", "Completado"],
        "issues": ["", "falta cantidad", "", ""],
        "is_complete": [True, False, True, True],
    })


def test_execute_query_sum_with_filter():
    spec = ai.validate_query({"operation": "sum", "metric_field": "valor",
                              "filters": [{"field": "estado", "op": "eq", "value": "pendiente"}]})
    result, _ = ai.execute_query(spec, _df())
    assert result == 500.0


def test_execute_query_group_by_month():
    spec = ai.validate_query({"operation": "count", "group_by": "mes", "sort": "desc"})
    result, _ = ai.execute_query(spec, _df())
    assert result.to_dict() == {"2026-10": 2, "2026-11": 2}
    assert list(result.index) == ["2026-10", "2026-11"]  # orden cronológico


def test_validate_query_rejects_unknown_fields():
    with pytest.raises(ValueError):
        ai.validate_query({"operation": "sum", "metric_field": "password"})
    with pytest.raises(ValueError):
        ai.validate_query({"operation": "count", "filters": [{"field": "x; DROP TABLE", "op": "eq", "value": 1}]})


def test_verify_summary_detects_invented_numbers():
    stats = {"total_registros": 53, "valor_total": 12_500_000.0, "pct_por_categoria": {"A": 72.0}}
    ok = "Se procesaron 53 registros por $12.500.000; el 72% corresponde a la categoría A."
    bad = "Se procesaron 60 registros por $12.500.000."
    assert ai.verify_summary(ok, stats) == []
    assert ai.verify_summary(bad, stats) == ["60"]


def test_verify_summary_accepts_decimal_comma():
    stats = {"total_registros": 90, "pct_por_categoria": {"Servicio A": 45.6}, "tasa_completado_pct": 66.7}
    assert ai.verify_summary("El 45,6% es Servicio A y la tasa de completado es 66,7%.", stats) == []
    assert ai.verify_summary("El 45.6% es Servicio A.", stats) == []


def test_pdf_without_ai_is_retried_later(engine, tmp_path):
    """Un PDF fuera de formato sin IA disponible no se marca como procesado (se reintenta)."""
    from reportlab.pdfgen import canvas
    eng, settings = engine
    path = tmp_path / "nota.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "El Cliente D cerro la orden REF-3001 por dos millones de pesos.")
    c.save()
    stats = RunStats()
    process_attachment(eng, Attachment("nota.pdf", "pdf", path.read_bytes()), "m1", settings, stats)
    assert stats.pending_retry == 1 and stats.errors == 0
    assert db.read_table(eng, db.processed_files).empty
