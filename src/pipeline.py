"""Orquestador del flujo completo:

Nuevo correo -> Validar remitente -> Detectar adjuntos -> Descargar documentos -> Procesar PDF/Excel
-> Validar información -> Consolidar datos -> Actualizar dashboard (resumen + datos en la BD)

Uso:
    python -m src.pipeline                       # lee Gmail (modo producción)
    python -m src.pipeline --source local --path samples/inbox   # demo sin correo
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import ai, db
from .analytics import compute_stats
from .config import Settings, get_settings, setup_logging
from .gmail_client import Attachment, EmailMessage, attachment_type
from .parsers.common import normalize_record
from .parsers.excel_parser import parse_excel
from .parsers.pdf_parser import parse_pdf
from .validation import REQUIRED_BY_SOURCE, find_batch_inconsistencies, record_key, validate_record

log = logging.getLogger("pipeline")


@dataclass
class RunStats:
    messages_found: int = 0
    messages_new: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    records_inserted: int = 0
    records_duplicated: int = 0
    errors: int = 0
    pending_retry: int = 0
    alerts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Procesamiento de un archivo
# ---------------------------------------------------------------------------
def extract_raw_records(att: Attachment, settings: Settings) -> tuple[list[dict[str, Any]], str]:
    """Devuelve (registros_crudos, parser_usado)."""
    if att.file_type == "excel":
        return parse_excel(att.content), "excel"
    raw, text = parse_pdf(att.content)
    if raw:
        return raw, "pdf"
    if not text.strip():
        raise ValueError("El PDF no contiene texto extraíble (¿es un escaneo?)")
    if not settings.ai_enabled:
        raise ai.AIUnavailable("El PDF no sigue la estructura esperada y la IA no está configurada")
    log.info("PDF fuera de formato: se interpreta con IA")
    raw = ai.interpret_pdf_text(text)  # puede lanzar AIUnavailable (cuota/red): se reintenta luego
    if not raw:
        raise ValueError("Ni el parser ni la IA encontraron registros en el PDF")
    return raw, "ia"


def build_rows(raw_records: list[dict[str, Any]], att: Attachment, file_hash: str, message_id: str,
               parser: str) -> list[dict[str, Any]]:
    rows = []
    for raw in raw_records:
        rec, format_errors = normalize_record(raw)
        issues = validate_record(rec, att.file_type, format_errors)
        if parser == "ia":
            issues.append("extraído por IA: verificar")
        rec["issues"] = issues
        rows.append(rec)
    find_batch_inconsistencies(rows)
    required = REQUIRED_BY_SOURCE[att.file_type]
    result = []
    for rec in rows:
        result.append({
            **{k: rec[k] for k in ("fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado")},
            "record_key": record_key(rec),
            "source_type": att.file_type,
            "file_hash": file_hash,
            "message_id": message_id,
            "issues": "; ".join(rec["issues"]),
            "is_complete": all(rec.get(f) is not None for f in required),
            "extracted_by_ai": parser == "ia",
        })
    return result


def process_attachment(engine, att: Attachment, message_id: str, settings: Settings, stats: RunStats) -> None:
    file_hash = hashlib.sha256(att.content).hexdigest()
    if db.file_processed(engine, file_hash):
        log.info("Archivo ya procesado antes (mismo contenido): %s", att.filename)
        stats.files_skipped += 1
        return

    # Descargar documento (historial local de adjuntos)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    (settings.download_dir / f"{file_hash[:10]}_{Path(att.filename).name}").write_bytes(att.content)

    file_row = {"file_hash": file_hash, "filename": att.filename, "message_id": message_id,
                "file_type": att.file_type}
    try:
        raw, parser = extract_raw_records(att, settings)
        rows = build_rows(raw, att, file_hash, message_id, parser)
    except ai.AIUnavailable as exc:
        # Problema temporal o de configuración: NO se marca como procesado para reintentarlo después
        log.warning("Se reintentará %s en la próxima ejecución: %s", att.filename, exc)
        stats.pending_retry += 1
        stats.alerts.append(f"{att.filename}: pendiente de reintento ({exc})")
        return
    except Exception as exc:  # noqa: BLE001 - un archivo dañado no debe detener el resto
        log.error("Error procesando %s: %s", att.filename, exc)
        stats.errors += 1
        stats.alerts.append(f"{att.filename}: {exc}")
        db.save_file_and_records(engine, {**file_row, "parser": None, "n_records": 0, "n_with_issues": 0,
                                          "status": "error", "error": str(exc)}, [])
        return

    with_issues = sum(1 for r in rows if r["issues"])
    status = "con_alertas" if with_issues else "ok"
    inserted, duplicates = db.save_file_and_records(engine, {
        **file_row, "parser": parser, "n_records": len(rows), "n_with_issues": with_issues,
        "status": status, "error": None}, rows)
    stats.files_processed += 1
    stats.records_inserted += inserted
    stats.records_duplicated += duplicates
    if with_issues:
        stats.alerts.append(f"{att.filename}: {with_issues} de {len(rows)} registros con alertas de validación")
    log.info("%s -> %s registros (%s nuevos, %s duplicados, %s con alertas) [parser=%s]",
             att.filename, len(rows), inserted, duplicates, with_issues, parser)


def process_message(engine, email: EmailMessage, settings: Settings, stats: RunStats) -> None:
    stats.messages_new += 1
    if not email.attachments:
        db.save_message(engine, message_id=email.message_id, sender=email.sender, subject=email.subject,
                        received_at=email.received_at, n_attachments=0, status="sin_adjuntos_validos",
                        detail="El correo no trae PDF ni XLSX")
        return
    errors_before, retry_before = stats.errors, stats.pending_retry
    for att in email.attachments:
        process_attachment(engine, att, email.message_id, settings, stats)
    if stats.pending_retry > retry_before:
        return  # el correo queda sin marcar; los adjuntos ya procesados se omiten por hash al reintentar
    db.save_message(engine, message_id=email.message_id, sender=email.sender, subject=email.subject,
                    received_at=email.received_at, n_attachments=len(email.attachments),
                    status="error" if stats.errors > errors_before else "ok", detail=None)


# ---------------------------------------------------------------------------
# Fuentes de correos
# ---------------------------------------------------------------------------
def run_gmail(engine, settings: Settings, stats: RunStats) -> None:
    from . import gmail_client

    service = gmail_client.build_service(settings)
    ids = gmail_client.list_candidate_ids(service, settings)
    stats.messages_found = len(ids)
    for message_id in ids:
        if db.message_processed(engine, message_id):
            continue  # correo ya procesado en una ejecución anterior
        email = gmail_client.fetch_message(service, message_id, settings)
        if email is None:  # remitente no autorizado (p. ej. reenvíos con el nombre en el asunto)
            db.save_message(engine, message_id=message_id, sender=None, subject=None, received_at=None,
                            n_attachments=0, status="remitente_no_autorizado", detail=None)
            continue
        process_message(engine, email, settings, stats)


def run_local(engine, settings: Settings, stats: RunStats, folder: Path) -> None:
    """Simula la bandeja de entrada: cada archivo de la carpeta es un 'correo' con un adjunto."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and attachment_type(p.name, None))
    stats.messages_found = len(files)
    for path in files:
        message_id = f"local:{path.name}"
        if db.message_processed(engine, message_id):
            continue
        att = Attachment(path.name, attachment_type(path.name, None), path.read_bytes())
        email = EmailMessage(message_id, settings.authorized_sender or "local", f"Archivo local {path.name}",
                             datetime.now(timezone.utc), [att])
        process_message(engine, email, settings, stats)


# ---------------------------------------------------------------------------
# Actualizar dashboard: resumen ejecutivo
# ---------------------------------------------------------------------------
def refresh_summary(engine) -> str:
    df = db.read_records(engine)
    stats = compute_stats(df)
    text, generated_by = ai.executive_summary(stats)
    db.save_summary(engine, text, stats, generated_by)
    log.info("Resumen ejecutivo actualizado (%s)", generated_by)
    return text


def report_to_github(stats: RunStats, status: str) -> None:
    """En GitHub Actions: anotaciones visibles (alertas) y resumen de la ejecución."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for alert in stats.alerts:
        print(f"::warning title=Documento con problemas::{alert}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(f"## Pipeline de correos: {status}\n\n| Métrica | Valor |\n|---|---|\n")
            for key, value in vars(stats).items():
                if key != "alerts":
                    fh.write(f"| {key} | {value} |\n")
            if stats.alerts:
                fh.write("\n### Alertas\n" + "\n".join(f"- {a}" for a in stats.alerts) + "\n")


def run(source: str = "gmail", path: str | None = None, force_summary: bool = False) -> RunStats:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = db.get_engine(settings.database_url)
    stats, started, status, detail = RunStats(), db.now(), "ok", None
    log.info("Inicio del pipeline (fuente=%s, base=%s, IA=%s)", source, engine.dialect.name,
             "sí" if settings.ai_enabled else "no")
    try:
        if source == "gmail":
            run_gmail(engine, settings, stats)
        else:
            run_local(engine, settings, stats, Path(path or "samples/inbox"))
        if stats.records_inserted or force_summary or db.latest_summary(engine) is None:
            refresh_summary(engine)
        if stats.errors:
            status = "con_errores"
    except Exception as exc:
        status, detail = "fallo", str(exc)
        log.exception("El pipeline falló")
        raise
    finally:
        db.save_run(engine, {"started_at": started, "finished_at": db.now(), "source": source,
                             **{k: v for k, v in vars(stats).items() if k != "alerts"},
                             "status": status, "detail": detail or "\n".join(stats.alerts) or None})
        report_to_github(stats, status)
        log.info("Fin: %s", {k: v for k, v in vars(stats).items() if k != "alerts"})
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Procesa correos con adjuntos PDF/Excel")
    parser.add_argument("--source", choices=["gmail", "local"], default="gmail")
    parser.add_argument("--path", help="Carpeta de archivos cuando --source local")
    parser.add_argument("--force-summary", action="store_true", help="Regenera el resumen ejecutivo")
    args = parser.parse_args(argv)
    try:
        run(args.source, args.path, args.force_summary)
    except Exception:  # noqa: BLE001
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
