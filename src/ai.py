"""Usos de IA (Google Gemini). Cada uno resuelve un problema que el código tradicional no resuelve bien:

1. interpret_pdf_text  -> Interpretar PDF que NO siguen la estructura esperada (texto libre, campos
                          con otros nombres). Solo se usa como respaldo cuando el parser determinista falla.
2. executive_summary   -> Redactar un resumen ejecutivo en lenguaje natural. Los números los calcula el
                          código; la IA solo redacta e interpreta, y se verifica que no invente cifras.
3. question_to_query   -> Traducir una pregunta en lenguaje natural a una consulta estructurada (JSON)
                          que luego ejecuta pandas. La IA nunca calcula ni ejecuta código.

Lo que NO se hace con IA (porque el código es más eficiente y confiable): leer Excel, leer tablas del PDF,
validar campos obligatorios, detectar duplicados, calcular métricas y detectar valores atípicos.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any

import pandas as pd

from .analytics import fmt_money, template_summary
from .config import get_settings

log = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


last_model_used: str | None = None  # modelo que respondió la última llamada (principal o respaldo)


def _generate(prompt: str, json_mode: bool) -> str:
    settings = get_settings()
    if not settings.ai_enabled:
        raise AIUnavailable("GEMINI_API_KEY no configurada")
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=settings.gemini_api_key,
        # Límite de 90 s por llamada y sin reintentos internos del SDK: los reintentos los controla este módulo
        http_options=types.HttpOptions(timeout=90_000, retry_options=types.HttpRetryOptions(attempts=1)),
    )
    config = types.GenerateContentConfig(
        temperature=0,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    last_exc: Exception | None = None
    # El free tier puede responder 429 (cuota) o 503 (modelo saturado): se reintenta y,
    # si el modelo principal sigue sin responder, se pasa a los modelos de respaldo.
    for model in [settings.gemini_model, *settings.gemini_fallback_models]:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
                global last_model_used
                last_model_used = model
                if model != settings.gemini_model:
                    log.info("Respuesta obtenida con el modelo de respaldo %s", model)
                return (response.text or "").strip()
            except Exception as exc:  # noqa: BLE001 - errores de red/cuota del proveedor
                last_exc = exc
                log.warning("Gemini %s falló (intento %s): %s", model, attempt + 1, str(exc)[:160])
                if any(code in str(exc)[:40] for code in ("404", "NOT_FOUND", "429", "RESOURCE_EXHAUSTED")):
                    break  # modelo inexistente o sin cuota: se pasa directo al siguiente modelo
                time.sleep(3 * (attempt + 1))
    raise AIUnavailable(f"Gemini no respondió: {str(last_exc)[:200]}")


def _parse_json(text: str) -> Any:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# 1. Interpretar PDF fuera de formato
# ---------------------------------------------------------------------------
PDF_PROMPT = """Eres un asistente que extrae datos de documentos operacionales.
Del siguiente texto extraído de un PDF, identifica TODOS los registros y devuelve SOLO un JSON con la forma:
{{"registros": [{{"fecha": "YYYY-MM-DD", "cliente": str, "referencia": str|null, "categoria": str|null,
"cantidad": int|null, "valor": number|null, "estado": str|null}}]}}
Reglas:
- No inventes datos: si un campo no aparece en el texto, usa null.
- "valor" es un número sin símbolos ni separadores de miles (ej. 1500000.0).
- Si no hay registros, devuelve {{"registros": []}}.

TEXTO DEL PDF:
\"\"\"
{text}
\"\"\"
"""


def interpret_pdf_text(text: str) -> list[dict[str, Any]]:
    data = _parse_json(_generate(PDF_PROMPT.format(text=text[:15000]), json_mode=True))
    rows = data.get("registros", []) if isinstance(data, dict) else []
    allowed = {"fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado"}
    # Verificación: cada cliente/referencia devuelto debe aparecer literalmente en el texto del PDF
    normalized_text = text.lower()
    verified = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = {k: v for k, v in row.items() if k in allowed}
        cliente = str(row.get("cliente") or "").lower()
        if cliente and cliente not in normalized_text:
            log.warning("IA devolvió un cliente que no está en el PDF (%s); se descarta", cliente)
            continue
        verified.append(row)
    return verified


# ---------------------------------------------------------------------------
# 2. Resumen ejecutivo
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """Eres analista de operaciones. Redacta un resumen ejecutivo en español (máximo 5 frases,
un solo párrafo, sin viñetas ni markdown) para la gerencia a partir de estos indicadores ya calculados.
Reglas estrictas:
- Usa ÚNICAMENTE las cifras que aparecen abajo; no calcules ni inventes cifras nuevas.
- Escribe los montos con formato $12.500.000 y los porcentajes como 72%.
- Menciona: periodo, total de registros, valor total, categoría principal, problemas de calidad de datos
  y un punto de atención o recomendación concreta.

INDICADORES:
{stats}
"""


def _numbers_in(text: str) -> list[tuple[str, set[float]]]:
    """Cada cifra del texto con sus lecturas posibles: '45,6' puede ser 45.6 (decimal) o 456 (miles)."""
    found = []
    for token in re.findall(r"\d[\d.,]*", text):
        token = token.rstrip(".,")
        readings = set()
        for candidate in (token.replace(".", "").replace(",", "."), token.replace(",", "")):
            try:
                readings.add(round(float(candidate), 1))
            except ValueError:
                pass
        if readings:
            found.append((token, readings))
    return found


def _allowed_numbers(stats: dict[str, Any]) -> set[float]:
    allowed: set[float] = {5.0}  # "top 5"
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (int, float)):
            allowed.update({round(float(value), 1), float(round(value)), round(float(value), 0)})
        elif isinstance(value, str):
            allowed.update(float(p) for p in re.findall(r"\d+", value))
    walk(stats)
    return allowed


def verify_summary(text: str, stats: dict[str, Any]) -> list[str]:
    """Devuelve las cifras del texto que NO están en los indicadores (posibles alucinaciones)."""
    allowed = _allowed_numbers(stats)
    unknown = []
    def ok(n: float) -> bool:
        return any(abs(n - a) <= max(0.051, abs(a) * 0.001) for a in allowed)
    for token, readings in _numbers_in(text):
        if not any(ok(n) for n in readings):  # basta con que UNA lectura sea un indicador real
            unknown.append(token)
    return unknown


def executive_summary(stats: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (texto, generado_por)."""
    if not stats.get("total_registros"):
        return template_summary(stats), "plantilla"
    pretty = dict(stats)
    pretty["valor_total"] = fmt_money(stats["valor_total"])
    pretty["valor_pendiente_de_cierre"] = fmt_money(stats["valor_pendiente_de_cierre"])
    pretty["ticket_promedio"] = fmt_money(stats["ticket_promedio"])
    try:
        text = _generate(SUMMARY_PROMPT.format(stats=json.dumps(pretty, ensure_ascii=False, indent=2)), json_mode=False)
    except AIUnavailable as exc:
        log.info("Resumen con plantilla (IA no disponible: %s)", exc)
        return template_summary(stats), "plantilla"
    unknown = verify_summary(text, stats)
    if unknown:
        log.warning("Resumen IA descartado: cifras no presentes en los indicadores %s. Texto: %s", unknown, text)
        return template_summary(stats), "plantilla (IA rechazada por cifras no verificables)"
    return text, last_model_used or get_settings().gemini_model


# ---------------------------------------------------------------------------
# 3. Consulta en lenguaje natural
# ---------------------------------------------------------------------------
FIELDS = ["fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado", "tiene_alertas", "incompleto"]
OPS = {"eq", "neq", "contains", "gt", "gte", "lt", "lte"}
OPERATIONS = {"count", "sum", "avg", "max", "min", "list"}
GROUPS = {None, "categoria", "estado", "cliente", "mes"}

QUERY_PROMPT = """Traduce la pregunta del usuario a una consulta JSON sobre una tabla de registros operacionales.
Columnas: fecha (date), cliente (texto), referencia (texto), categoria (texto), cantidad (entero),
valor (número, pesos), estado (texto), tiene_alertas (booleano), incompleto (booleano).
Valores existentes -> categorias: {categorias}; estados: {estados}; clientes (muestra): {clientes};
rango de fechas: {desde} a {hasta}. Hoy es {hoy}.

Devuelve SOLO este JSON:
{{"operation": "count"|"sum"|"avg"|"max"|"min"|"list",
  "metric_field": "valor"|"cantidad"|null,
  "filters": [{{"field": <columna>, "op": "eq"|"neq"|"contains"|"gt"|"gte"|"lt"|"lte", "value": <valor>}}],
  "date_from": "YYYY-MM-DD"|null, "date_to": "YYYY-MM-DD"|null,
  "group_by": "categoria"|"estado"|"cliente"|"mes"|null,
  "sort": "asc"|"desc"|null, "limit": entero|null,
  "explanation": "frase corta en español de lo que vas a calcular"}}
Si la pregunta no se puede responder con estos datos, devuelve {{"operation": null, "explanation": "motivo"}}.

PREGUNTA: {question}
"""


def validate_query(spec: dict[str, Any]) -> dict[str, Any]:
    """La salida del LLM se valida contra una lista blanca antes de ejecutarse."""
    if spec.get("operation") not in OPERATIONS:
        raise ValueError(spec.get("explanation") or "La pregunta no se puede responder con los datos disponibles")
    if spec.get("metric_field") not in {None, "valor", "cantidad"}:
        raise ValueError("Campo de métrica no permitido")
    if spec["operation"] in {"sum", "avg", "max", "min"} and not spec.get("metric_field"):
        spec["metric_field"] = "valor"
    if spec.get("group_by") not in GROUPS:
        raise ValueError("Agrupación no permitida")
    for flt in spec.get("filters") or []:
        if flt.get("field") not in FIELDS or flt.get("op") not in OPS:
            raise ValueError(f"Filtro no permitido: {flt}")
    if spec.get("limit") is not None:
        spec["limit"] = max(1, min(int(spec["limit"]), 100))
    return spec


def question_to_query(question: str, df: pd.DataFrame) -> dict[str, Any]:
    fechas = df["fecha"].dropna()
    prompt = QUERY_PROMPT.format(
        categorias=sorted(df["categoria"].dropna().unique().tolist()),
        estados=sorted(df["estado"].dropna().unique().tolist()),
        clientes=sorted(df["cliente"].dropna().unique().tolist())[:40],
        desde=fechas.min().date() if not fechas.empty else None,
        hasta=fechas.max().date() if not fechas.empty else None,
        hoy=date.today().isoformat(),
        question=question,
    )
    return validate_query(_parse_json(_generate(prompt, json_mode=True)))


def execute_query(spec: dict[str, Any], df: pd.DataFrame) -> tuple[Any, pd.DataFrame]:
    """Ejecuta la consulta con pandas (determinista). Devuelve (resultado, filas_filtradas)."""
    data = df.copy()
    data["tiene_alertas"] = data["issues"].fillna("") != ""
    data["incompleto"] = ~data["is_complete"].astype(bool)
    data["mes"] = data["fecha"].dt.to_period("M").astype(str)

    if spec.get("date_from"):
        data = data[data["fecha"] >= pd.to_datetime(spec["date_from"])]
    if spec.get("date_to"):
        data = data[data["fecha"] <= pd.to_datetime(spec["date_to"])]
    for flt in spec.get("filters") or []:
        col, op, value = data[flt["field"]], flt["op"], flt["value"]
        if flt["field"] == "fecha":
            value = pd.to_datetime(value)
        if op in {"eq", "neq", "contains"} and col.dtype == object:
            col_cmp, value_cmp = col.fillna("").str.lower(), str(value).lower()
            mask = col_cmp.str.contains(value_cmp, regex=False) if op == "contains" else (col_cmp == value_cmp)
            if op == "neq":
                mask = ~mask
        else:
            mask = {"eq": col == value, "neq": col != value, "gt": col > value, "gte": col >= value,
                    "lt": col < value, "lte": col <= value, "contains": col == value}[op]
        data = data[mask]

    op, field, group = spec["operation"], spec.get("metric_field"), spec.get("group_by")
    if op == "list":
        rows = data.sort_values("valor", ascending=spec.get("sort") == "asc") if spec.get("sort") else data
        rows = rows.head(spec.get("limit") or 20)
        return len(data), rows
    if group:
        grouped = data.groupby(group)
        result = grouped.size() if op == "count" else getattr(grouped[field], {"avg": "mean"}.get(op, op))()
        if group == "mes" and not spec.get("limit"):
            result = result.sort_index()  # serie de tiempo: orden cronológico
        else:
            result = result.sort_values(ascending=spec.get("sort") == "asc")
        if spec.get("limit"):
            result = result.head(spec["limit"])
        return result, data
    if op == "count":
        return int(len(data)), data
    value = getattr(data[field], {"avg": "mean"}.get(op, op))()
    return (None if pd.isna(value) else float(value)), data
