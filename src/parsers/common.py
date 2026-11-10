"""Estructura común y normalización de los datos que llegan en PDF o Excel.

Estructura común (un registro):
    fecha, cliente, referencia, categoria, cantidad, valor, estado

- El Excel trae: Fecha, Cliente, Categoría, Cantidad, Valor, Estado (sin referencia).
- El PDF trae:   Fecha, Cliente, Número de referencia, Categoría, Valor, Estado (sin cantidad).
Los campos que un formato no trae quedan en None.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

FIELDS = ["fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado"]

# Sinónimos aceptados en los encabezados (ya sin tildes y en minúscula)
HEADER_ALIASES: dict[str, str] = {
    "fecha": "fecha",
    "fecha de registro": "fecha",
    "cliente": "cliente",
    "nombre cliente": "cliente",
    "numero de referencia": "referencia",
    "nro de referencia": "referencia",
    "no de referencia": "referencia",
    "n de referencia": "referencia",
    "referencia": "referencia",
    "ref": "referencia",
    "categoria": "categoria",
    "cantidad": "cantidad",
    "unidades": "cantidad",
    "valor": "valor",
    "monto": "valor",
    "valor total": "valor",
    "estado": "estado",
}

# Catálogo de estados válidos y sus variantes conocidas
ESTADOS_CANONICOS: dict[str, str] = {
    "completado": "Completado",
    "completada": "Completado",
    "finalizada": "Completado",
    "pagada": "Completado",
    "cancelada": "Cancelado",
    "anulada": "Cancelado",
    "rechazada": "Rechazado",
    "completo": "Completado",
    "finalizado": "Completado",
    "pagado": "Completado",
    "pendiente": "Pendiente",
    "en proceso": "En proceso",
    "en progreso": "En proceso",
    "cancelado": "Cancelado",
    "anulado": "Cancelado",
    "rechazado": "Rechazado",
}
ESTADOS_VALIDOS = sorted(set(ESTADOS_CANONICOS.values()))


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_header(header: Any) -> str | None:
    if header is None:
        return None
    key = strip_accents(str(header)).lower()
    key = re.sub(r"[^a-z0-9 ]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return HEADER_ALIASES.get(key)


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return isinstance(value, str) and value.strip() in {"", "-", "N/A", "n/a", "NA", "nan", "None"}


def parse_date(value: Any) -> date | None:
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):  # pandas Timestamp
        return value.to_pydatetime().date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha inválida: {text!r}")


def parse_amount(value: Any) -> float | None:
    """Convierte '1500', '$1.500.000', '1,500.50', '1.500,50' o números a float."""
    if is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace("$", "").replace("COP", "").replace(" ", "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-()")
    if not re.fullmatch(r"[0-9.,]+", text):
        raise ValueError(f"valor no numérico: {value!r}")
    if "." in text and "," in text:
        # El último separador que aparece es el decimal
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        parts = text.split(sep)
        if len(parts) > 2 or all(len(p) == 3 for p in parts[1:]):
            text = text.replace(sep, "")  # separador de miles
        else:
            text = text.replace(sep, ".")  # separador decimal
    number = float(text)
    return -number if negative else number


def parse_quantity(value: Any) -> int | None:
    number = parse_amount(value)
    if number is None:
        return None
    if number != int(number):
        raise ValueError(f"cantidad no entera: {value!r}")
    return int(number)


def clean_text(value: Any) -> str | None:
    if is_blank(value):
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_estado(value: Any) -> str | None:
    text = clean_text(value)
    if text is None:
        return None
    key = strip_accents(text).lower()
    return ESTADOS_CANONICOS.get(key, text.title())


def normalize_record(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Lleva un registro crudo a la estructura común. Devuelve (registro, errores_de_formato)."""
    errors: list[str] = []
    rec: dict[str, Any] = {f: None for f in FIELDS}

    for field, parser in (("fecha", parse_date), ("valor", parse_amount), ("cantidad", parse_quantity)):
        try:
            rec[field] = parser(raw.get(field))
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))

    rec["cliente"] = clean_text(raw.get("cliente"))
    rec["referencia"] = clean_text(raw.get("referencia"))
    rec["categoria"] = clean_text(raw.get("categoria"))
    rec["estado"] = normalize_estado(raw.get("estado"))
    return rec, errors
