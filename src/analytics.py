"""Cálculo de indicadores (programación tradicional: los números siempre los calcula el código)."""
from __future__ import annotations

from typing import Any

import pandas as pd


def flag_outliers(df: pd.DataFrame) -> pd.Series:
    """Registros sospechosos por valor atípico dentro de su categoría (regla IQR de Tukey).

    Es estadística simple y explicable; no requiere IA.
    """
    if df.empty or "valor" not in df:
        return pd.Series(dtype=bool)
    result = pd.Series(False, index=df.index)
    for _, group in df.dropna(subset=["valor"]).groupby(df["categoria"].fillna("Sin categoría")):
        if len(group) < 5:
            continue
        q1, q3 = group["valor"].quantile([0.25, 0.75])
        iqr = q3 - q1
        upper, lower = q3 + 3 * iqr, q1 - 3 * iqr
        result.loc[group.index] = (group["valor"] > upper) | (group["valor"] < lower)
    return result


def compute_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Indicadores que alimentan el dashboard y el resumen ejecutivo."""
    if df.empty:
        return {"total_registros": 0}
    valor = df["valor"].fillna(0)
    by_cat = df.groupby(df["categoria"].fillna("Sin categoría")).agg(registros=("id", "count"), valor=("valor", "sum"))
    by_estado = df.groupby(df["estado"].fillna("Sin estado")).agg(registros=("id", "count"), valor=("valor", "sum"))
    total = len(df)
    con_alertas = int((df["issues"].fillna("") != "").sum())
    incompletos = int((~df["is_complete"].astype(bool)).sum())
    completados = int((df["estado"] == "Completado").sum())
    pendiente_valor = float(df.loc[df["estado"].isin(["Pendiente", "En proceso"]), "valor"].sum())
    top_clientes = (df.groupby("cliente")["valor"].sum().sort_values(ascending=False).head(5))
    concentracion_top5 = float(top_clientes.sum() / valor.sum() * 100) if valor.sum() else 0.0
    fechas = df["fecha"].dropna()
    return {
        "periodo_desde": fechas.min().date().isoformat() if not fechas.empty else None,
        "periodo_hasta": fechas.max().date().isoformat() if not fechas.empty else None,
        "total_registros": total,
        "valor_total": float(valor.sum()),
        "ticket_promedio": float(df["valor"].mean()) if df["valor"].notna().any() else 0.0,
        "registros_por_categoria": {k: int(v) for k, v in by_cat["registros"].items()},
        "pct_por_categoria": {k: round(v / total * 100, 1) for k, v in by_cat["registros"].items()},
        "valor_por_categoria": {k: float(v) for k, v in by_cat["valor"].items()},
        "registros_por_estado": {k: int(v) for k, v in by_estado["registros"].items()},
        "tasa_completado_pct": round(completados / total * 100, 1),
        "valor_pendiente_de_cierre": pendiente_valor,
        "registros_incompletos": incompletos,
        "registros_con_alertas": con_alertas,
        "pct_calidad_datos": round((total - con_alertas) / total * 100, 1),
        "registros_valor_atipico": int(flag_outliers(df).sum()),
        "top5_clientes_valor": {k: float(v) for k, v in top_clientes.items()},
        "concentracion_top5_pct": round(concentracion_top5, 1),
    }


def fmt_money(value: float) -> str:
    """Formato colombiano: $12.500.000"""
    return "$" + f"{value:,.0f}".replace(",", ".")


def template_summary(stats: dict[str, Any]) -> str:
    """Resumen de respaldo sin IA (cuando no hay API key o la IA falla)."""
    if not stats.get("total_registros"):
        return "Aún no hay registros procesados."
    cat, pct = max(stats["pct_por_categoria"].items(), key=lambda kv: kv[1])
    return (
        f"Durante el periodo {stats['periodo_desde']} a {stats['periodo_hasta']} se procesaron "
        f"{stats['total_registros']} registros por un valor total de {fmt_money(stats['valor_total'])}. "
        f"El {pct}% corresponde a la categoría {cat}. La tasa de completado es {stats['tasa_completado_pct']}% "
        f"y hay {fmt_money(stats['valor_pendiente_de_cierre'])} pendientes de cierre. "
        f"Existen {stats['registros_incompletos']} registros con información incompleta, "
        f"{stats['registros_con_alertas']} con alguna alerta de validación y "
        f"{stats['registros_valor_atipico']} con valores atípicos."
    )
