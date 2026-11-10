"""Lectura de archivos Excel (.xlsx) con estructura estandarizada."""
from __future__ import annotations

import io
from typing import Any

from openpyxl import load_workbook

from .common import is_blank, normalize_header

REQUIRED_HEADERS = {"fecha", "cliente", "valor"}


def parse_excel(content: bytes) -> list[dict[str, Any]]:
    """Devuelve registros crudos (columna canónica -> valor) de todas las hojas.

    El encabezado se busca en las primeras 15 filas, así no importa si el archivo
    trae un título o logo antes de la tabla.
    """
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    records: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        header_idx, mapping = None, {}
        for i, row in enumerate(rows[:15]):
            candidate = {idx: normalize_header(cell) for idx, cell in enumerate(row)}
            candidate = {idx: name for idx, name in candidate.items() if name}
            if REQUIRED_HEADERS.issubset(candidate.values()):
                header_idx, mapping = i, candidate
                break
        if header_idx is None:
            continue
        for row in rows[header_idx + 1:]:
            if all(is_blank(c) for c in row):
                continue
            raw = {name: row[idx] if idx < len(row) else None for idx, name in mapping.items()}
            # Fila de totales: sin fecha ni cliente
            if is_blank(raw.get("fecha")) and is_blank(raw.get("cliente")) and str(row[0] or "").lower().startswith("total"):
                continue
            records.append(raw)
    wb.close()
    if not records:
        raise ValueError("No se encontró una tabla con encabezados Fecha/Cliente/Valor en el Excel")
    return records
