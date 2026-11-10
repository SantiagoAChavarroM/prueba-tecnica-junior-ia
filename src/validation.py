"""Validación determinista de registros (sin IA: son reglas claras que el código resuelve mejor)."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from .parsers.common import ESTADOS_VALIDOS

REQUIRED_BY_SOURCE = {
    "excel": ["fecha", "cliente", "categoria", "cantidad", "valor", "estado"],
    "pdf": ["fecha", "cliente", "referencia", "categoria", "valor", "estado"],
}


def validate_record(rec: dict[str, Any], source_type: str, format_errors: list[str], today: date | None = None) -> list[str]:
    """Devuelve la lista de problemas encontrados en un registro ya normalizado."""
    today = today or date.today()
    issues = list(format_errors)

    for field in REQUIRED_BY_SOURCE.get(source_type, []):
        if rec.get(field) is None and not any(field in e for e in format_errors):
            issues.append(f"falta {field}")

    if rec.get("valor") is not None and rec["valor"] <= 0:
        issues.append("valor menor o igual a cero")
    if rec.get("cantidad") is not None and rec["cantidad"] <= 0:
        issues.append("cantidad menor o igual a cero")
    if rec.get("fecha") is not None:
        if rec["fecha"] > today + timedelta(days=1):
            issues.append("fecha futura")
        if rec["fecha"].year < 2000:
            issues.append("fecha anterior al año 2000")
    if rec.get("estado") is not None and rec["estado"] not in ESTADOS_VALIDOS:
        issues.append(f"estado no reconocido ({rec['estado']})")
    return issues


def find_batch_inconsistencies(records: list[dict[str, Any]]) -> None:
    """Marca referencias repetidas con datos distintos dentro del mismo lote (modifica en sitio)."""
    seen: dict[str, dict[str, Any]] = {}
    for rec in records:
        ref = rec.get("referencia")
        if not ref:
            continue
        if ref in seen and record_key(seen[ref]) != record_key(rec):
            rec["issues"].append(f"referencia {ref} repetida con datos distintos")
        seen.setdefault(ref, rec)


def record_key(rec: dict[str, Any]) -> str:
    """Huella única del registro: evita insertar dos veces el mismo dato
    aunque llegue en otro correo o en otro archivo."""
    parts = [
        str(rec.get("fecha") or ""),
        (rec.get("cliente") or "").lower(),
        (rec.get("referencia") or "").lower(),
        (rec.get("categoria") or "").lower(),
        str(rec.get("cantidad") or ""),
        f"{rec['valor']:.2f}" if rec.get("valor") is not None else "",
        (rec.get("estado") or "").lower(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
