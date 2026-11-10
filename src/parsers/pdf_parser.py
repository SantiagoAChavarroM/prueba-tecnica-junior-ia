"""Lectura de PDF estructurados.

Estrategia (de lo más barato y confiable a lo más flexible):
1. Tablas del PDF (pdfplumber) con encabezados reconocibles.
2. Formato "ficha": líneas 'Campo: valor' (un registro o varios seguidos).
3. Si nada de lo anterior funciona, el pipeline usa IA (src/ai.py) para interpretar
   el texto. Solo se llega aquí cuando el PDF se sale de la estructura esperada.
"""
from __future__ import annotations

import io
import re
from typing import Any

import pdfplumber

from .common import is_blank, normalize_header

REQUIRED = {"fecha", "cliente", "valor"}

KV_PATTERN = re.compile(
    r"^\s*(fecha|cliente|n[uú]mero de referencia|nro\.? de referencia|referencia|"
    r"categor[ií]a|cantidad|valor|monto|estado)\s*[:\-–]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def extract_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _from_tables(pdf: pdfplumber.PDF) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    mapping: dict[int, str] | None = None  # se conserva entre páginas (tabla continuada)
    for page in pdf.pages:
        for table in page.extract_tables():
            for row in table:
                candidate = {i: normalize_header(c) for i, c in enumerate(row)}
                candidate = {i: n for i, n in candidate.items() if n}
                if REQUIRED.issubset(candidate.values()):
                    mapping = candidate
                    continue
                if mapping is None or all(is_blank(c) for c in row):
                    continue
                raw = {name: row[i] if i < len(row) else None for i, name in mapping.items()}
                if str(row[0] or "").strip().lower().startswith("total"):
                    continue
                records.append(raw)
    return records


def _from_key_values(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        match = KV_PATTERN.match(line)
        if not match:
            continue
        field = normalize_header(match.group(1))
        if field is None:
            continue
        if field in current:  # empieza un nuevo registro
            records.append(current)
            current = {}
        current[field] = match.group(2)
    if current:
        records.append(current)
    return [r for r in records if len(REQUIRED & r.keys()) >= 2]


def parse_pdf(content: bytes) -> tuple[list[dict[str, Any]], str]:
    """Devuelve (registros crudos, texto completo). Lista vacía = estructura no reconocida."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        records = _from_tables(pdf)
    if not records:
        records = _from_key_values(text)
    return records, text
