"""Dashboard de indicadores (Streamlit).

Ejecutar:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# En Streamlit Community Cloud los secretos llegan por st.secrets; se pasan a variables de entorno.
try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ.setdefault(key, value)
except Exception:  # noqa: BLE001 - en local puede no existir secrets.toml
    pass

from src import ai, db  # noqa: E402
from src.analytics import compute_stats, flag_outliers, fmt_money  # noqa: E402
from src.config import get_settings  # noqa: E402

st.set_page_config(page_title="Indicadores operacionales", page_icon="📊", layout="wide")
settings = get_settings()
engine = db.get_engine(settings.database_url)


@st.cache_data(ttl=60, show_spinner=False)
def load_data() -> dict[str, pd.DataFrame]:
    return {
        "records": db.read_records(engine),
        "files": db.read_table(engine, db.processed_files, order_by=db.processed_files.c.processed_at.desc()),
        "runs": db.read_table(engine, db.run_log, order_by=db.run_log.c.id.desc(), limit=50),
        "messages": db.read_table(engine, db.processed_messages,
                                  order_by=db.processed_messages.c.processed_at.desc()),
    }


data = load_data()
df_all = data["records"]

# ---------------------------------------------------------------- Barra lateral
st.sidebar.title("Filtros")
if st.sidebar.button("🔄 Actualizar datos", width="stretch"):
    st.cache_data.clear()
    st.rerun()

if df_all.empty:
    st.title("📊 Indicadores operacionales")
    st.info("Aún no hay registros. Ejecuta el pipeline: `python -m src.pipeline` "
            "(o `--source local --path samples/inbox` para la demo).")
    st.stop()

min_d, max_d = df_all["fecha"].min().date(), df_all["fecha"].max().date()
rango = st.sidebar.date_input("Periodo", (min_d, max_d), min_value=min_d, max_value=max_d)
cats = st.sidebar.multiselect("Categoría", sorted(df_all["categoria"].dropna().unique()))
estados = st.sidebar.multiselect("Estado", sorted(df_all["estado"].dropna().unique()))
origen = st.sidebar.multiselect("Origen", sorted(df_all["source_type"].dropna().unique()))

df = df_all.copy()
if isinstance(rango, tuple) and len(rango) == 2:
    df = df[(df["fecha"].isna()) | ((df["fecha"].dt.date >= rango[0]) & (df["fecha"].dt.date <= rango[1]))]
if cats:
    df = df[df["categoria"].isin(cats)]
if estados:
    df = df[df["estado"].isin(estados)]
if origen:
    df = df[df["source_type"].isin(origen)]

if not data["runs"].empty:
    last = data["runs"].iloc[0]
    st.sidebar.caption(f"Última ejecución del pipeline: {pd.to_datetime(last['finished_at']):%Y-%m-%d %H:%M} UTC "
                       f"· estado: **{last['status']}**")
st.sidebar.caption(f"Base de datos: {engine.dialect.name} · IA: {'activa' if settings.ai_enabled else 'no configurada'}")

stats = compute_stats(df) if not df.empty else {"total_registros": 0}

# ---------------------------------------------------------------- Encabezado y KPIs
st.title("📊 Indicadores operacionales")
st.caption("Datos consolidados automáticamente desde los PDF y Excel recibidos por correo.")

if not stats["total_registros"]:
    st.warning("No hay registros con los filtros seleccionados.")
    st.stop()

k = st.columns(3) + st.columns(3)
k[0].metric("Registros procesados", f"{stats['total_registros']:,}".replace(",", "."))
k[1].metric("Valor total", fmt_money(stats["valor_total"]))
k[2].metric("Ticket promedio", fmt_money(stats["ticket_promedio"]))
k[3].metric("Tasa de completado", f"{stats['tasa_completado_pct']}%")
k[4].metric("Valor pendiente de cierre", fmt_money(stats["valor_pendiente_de_cierre"]),
            help="Suma del valor en estado Pendiente o En proceso")
k[5].metric("Calidad de datos", f"{stats['pct_calidad_datos']}%",
            help="Porcentaje de registros sin alertas de validación")

tab_res, tab_cal, tab_q, tab_hist, tab_reg = st.tabs(
    ["Resumen", "Calidad de datos", "Pregunta a los datos (IA)", "Historial y logs", "Registros"])

# ---------------------------------------------------------------- Resumen
with tab_res:
    summary = db.latest_summary(engine)
    with st.container(border=True):
        st.subheader("🧠 Resumen ejecutivo")
        if summary:
            st.write(summary["text"])
            st.caption(f"Generado por: {summary['generated_by']} · {pd.to_datetime(summary['created_at']):%Y-%m-%d %H:%M} UTC "
                       "· calculado sobre todos los datos (sin filtros)")
        if st.button("Regenerar resumen"):
            with st.spinner("Generando…"):
                text, by = ai.executive_summary(compute_stats(df_all))
                db.save_summary(engine, text, compute_stats(df_all), by)
            st.rerun()

    c1, c2 = st.columns(2)
    por_cat = df.groupby(df["categoria"].fillna("Sin categoría")).agg(
        registros=("id", "count"), valor=("valor", "sum")).reset_index()
    c1.plotly_chart(px.bar(por_cat.sort_values("registros"), x="registros", y="categoria", orientation="h",
                           title="Registros por categoría", text="registros",
                           hover_data={"valor": ":,.0f"}), width="stretch")
    por_est = df.groupby(df["estado"].fillna("Sin estado")).agg(
        registros=("id", "count"), valor=("valor", "sum")).reset_index()
    c2.plotly_chart(px.pie(por_est, names="estado", values="registros", hole=0.5,
                           title="Registros por estado"), width="stretch")

    c3, c4 = st.columns([3, 2])
    gran = c3.radio("Agrupar por", ["Semana", "Día", "Mes"], horizontal=True)
    freq = {"Día": "D", "Semana": "W-MON", "Mes": "MS"}[gran]
    serie = (df.dropna(subset=["fecha"]).set_index("fecha")
             .resample(freq).agg(registros=("id", "count"), valor=("valor", "sum")).reset_index())
    medida = c3.radio("Medida", ["valor", "registros"], horizontal=True)
    c3.plotly_chart(px.line(serie, x="fecha", y=medida, markers=True,
                            title=f"Evolución de {medida} en el tiempo"), width="stretch")
    top = df.groupby("cliente")["valor"].sum().sort_values(ascending=False).head(5).reset_index()
    c4.plotly_chart(px.bar(top.sort_values("valor"), x="valor", y="cliente", orientation="h",
                           title=f"Top 5 clientes por valor ({stats['concentracion_top5_pct']}% del total)"),
                    width="stretch")

    with st.expander("¿Por qué estas métricas adicionales?"):
        st.markdown("""
- **Tasa de completado y valor pendiente de cierre:** el total procesado no dice cuánto se ha concretado.
  Estas dos cifras muestran el trabajo en curso y el dinero que aún no se ha realizado, que es lo que
  requiere seguimiento.
- **Calidad de datos (% sin alertas):** los indicadores solo son confiables si los datos de origen lo son.
  Mide qué tan bien llegan los documentos del remitente y si hay que pedir correcciones.
- **Ticket promedio:** permite comparar periodos aunque cambie el número de registros.
- **Concentración en el top 5 de clientes:** una alta dependencia de pocos clientes es un riesgo operativo y comercial.
""")

# ---------------------------------------------------------------- Calidad
with tab_cal:
    df_q = df.copy()
    df_q["valor_atipico"] = flag_outliers(df_q).reindex(df_q.index, fill_value=False)
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Incompletos", stats["registros_incompletos"])
    q2.metric("Con alertas de validación", stats["registros_con_alertas"])
    q3.metric("Valor atípico (sospechoso)", stats["registros_valor_atipico"])
    q4.metric("Extraídos por IA", int(df_q["extracted_by_ai"].fillna(False).astype(bool).sum()))
    issues = df_q["issues"].fillna("").str.split("; ").explode()
    issues = issues[issues != ""].str.replace(r"\s*\(.*\)", "", regex=True)
    if not issues.empty:
        st.plotly_chart(px.bar(issues.value_counts().reset_index(), x="count", y="issues", orientation="h",
                               title="Tipos de alerta"), width="stretch")
    problem = df_q[(df_q["issues"].fillna("") != "") | df_q["valor_atipico"]]
    st.subheader("Registros a revisar")
    st.dataframe(problem[["fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado",
                          "issues", "valor_atipico", "source_type"]],
                 width="stretch", hide_index=True)

# ---------------------------------------------------------------- Pregunta en lenguaje natural
with tab_q:
    st.markdown("Haz una pregunta sobre los datos. La IA **traduce** la pregunta a una consulta estructurada; "
                "el cálculo lo hace el código, así que las cifras son exactas.")
    ejemplos = ["¿Cuál es el valor total de los registros pendientes?",
                "¿Qué 3 clientes tienen más valor en Servicio A?",
                "¿Cuántos registros hubo por mes?",
                "¿Cuántos registros tienen alertas?"]
    pregunta = st.text_input("Pregunta", placeholder=ejemplos[0])
    st.caption("Ejemplos: " + " · ".join(ejemplos))
    if pregunta:
        if not settings.ai_enabled:
            st.error("Configura GEMINI_API_KEY para usar esta función.")
        else:
            try:
                with st.spinner("Interpretando la pregunta…"):
                    spec = ai.question_to_query(pregunta, df_all)
                result, rows = ai.execute_query(spec, df_all)
                st.success(spec.get("explanation", ""))
                if isinstance(result, pd.Series):
                    res_df = result.reset_index()
                    res_df.columns = [spec["group_by"], spec.get("metric_field") or "registros"]
                    st.dataframe(res_df, hide_index=True, width="stretch")
                elif spec["operation"] == "list":
                    st.write(f"{result} registros cumplen la condición (se muestran {len(rows)}).")
                    st.dataframe(rows[["fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado"]],
                                 hide_index=True, width="stretch")
                elif spec.get("metric_field") == "valor" and result is not None:
                    st.metric("Resultado", fmt_money(result))
                else:
                    st.metric("Resultado", result if result is not None else "Sin datos")
                with st.expander("Ver consulta generada (JSON)"):
                    st.code(json.dumps(spec, ensure_ascii=False, indent=2), language="json")
            except Exception as exc:  # noqa: BLE001
                st.warning(f"No pude responder esa pregunta: {exc}")

# ---------------------------------------------------------------- Historial
with tab_hist:
    st.subheader("Archivos procesados")
    st.dataframe(data["files"][["processed_at", "filename", "file_type", "parser", "status", "n_records",
                                "n_inserted", "n_duplicates", "n_with_issues", "error"]],
                 width="stretch", hide_index=True)
    st.subheader("Correos revisados")
    st.dataframe(data["messages"], width="stretch", hide_index=True)
    st.subheader("Ejecuciones del pipeline")
    st.dataframe(data["runs"], width="stretch", hide_index=True)

# ---------------------------------------------------------------- Registros
with tab_reg:
    cols = ["fecha", "cliente", "referencia", "categoria", "cantidad", "valor", "estado", "source_type", "issues"]
    st.dataframe(df[cols], width="stretch", hide_index=True)
    st.download_button("Descargar CSV", df[cols].to_csv(index=False).encode("utf-8"),
                       "registros_consolidados.csv", "text/csv")
