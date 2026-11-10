"""Persistencia con SQLAlchemy Core: el mismo código funciona con SQLite (local) y PostgreSQL (Supabase).

Tablas:
- processed_messages: correos ya revisados (evita reprocesar el mismo correo).
- processed_files:    archivos ya procesados por su hash SHA-256 (evita reprocesar el mismo archivo
                      aunque llegue adjunto en otro correo). También es el historial de archivos.
- records:            registros consolidados; record_key UNIQUE evita registros duplicados.
- run_log:            una fila por ejecución del pipeline (logs del proceso).
- summaries:          resúmenes ejecutivos generados.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, MetaData, String, Table, Text,
    create_engine, select, func,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

processed_messages = Table(
    "processed_messages", metadata,
    Column("message_id", String(255), primary_key=True),
    Column("sender", String(255)),
    Column("subject", Text),
    Column("received_at", DateTime(timezone=True)),
    Column("n_attachments", Integer),
    Column("status", String(30)),          # ok | error | sin_adjuntos_validos
    Column("detail", Text),
    Column("processed_at", DateTime(timezone=True)),
)

processed_files = Table(
    "processed_files", metadata,
    Column("file_hash", String(64), primary_key=True),
    Column("filename", Text),
    Column("message_id", String(255)),
    Column("file_type", String(10)),       # pdf | excel
    Column("parser", String(20)),          # tabla | ficha | excel | ia
    Column("n_records", Integer),
    Column("n_inserted", Integer),
    Column("n_duplicates", Integer),
    Column("n_with_issues", Integer),
    Column("status", String(20)),          # ok | con_alertas | error
    Column("error", Text),
    Column("processed_at", DateTime(timezone=True)),
)

records = Table(
    "records", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("record_key", String(64), nullable=False, unique=True),
    Column("fecha", Date),
    Column("cliente", Text),
    Column("referencia", Text),
    Column("categoria", Text),
    Column("cantidad", Integer),
    Column("valor", Float),
    Column("estado", Text),
    Column("source_type", String(10)),
    Column("file_hash", String(64)),
    Column("message_id", String(255)),
    Column("issues", Text),                # problemas separados por "; "
    Column("is_complete", Boolean),
    Column("extracted_by_ai", Boolean, default=False),
    Column("created_at", DateTime(timezone=True)),
)

run_log = Table(
    "run_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("source", String(20)),
    Column("messages_found", Integer),
    Column("messages_new", Integer),
    Column("files_processed", Integer),
    Column("files_skipped", Integer),
    Column("records_inserted", Integer),
    Column("records_duplicated", Integer),
    Column("errors", Integer),
    Column("pending_retry", Integer),
    Column("status", String(20)),
    Column("detail", Text),
)

summaries = Table(
    "summaries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True)),
    Column("text", Text),
    Column("stats_json", Text),
    Column("generated_by", String(50)),    # modelo de IA o "plantilla"
)


def now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    metadata.create_all(engine)
    return engine


def _insert_ignore(engine: Engine, table: Table):
    """INSERT ... ON CONFLICT DO NOTHING para SQLite y PostgreSQL."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table).on_conflict_do_nothing()


def message_processed(engine: Engine, message_id: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(processed_messages.c.message_id).where(
            processed_messages.c.message_id == message_id)).first() is not None


def file_processed(engine: Engine, file_hash: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(select(processed_files.c.file_hash).where(
            processed_files.c.file_hash == file_hash)).first() is not None


def save_message(engine: Engine, **values: Any) -> None:
    values.setdefault("processed_at", now())
    with engine.begin() as conn:
        conn.execute(_insert_ignore(engine, processed_messages), [values])


def save_file_and_records(engine: Engine, file_row: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Guarda el archivo y sus registros en UNA transacción. Devuelve (insertados, duplicados)."""
    inserted = 0
    with engine.begin() as conn:
        for row in rows:
            # RETURNING devuelve una fila solo si se insertó; si era duplicado, no devuelve nada.
            # (rowcount no es confiable: con psycopg/pooler de Supabase vale -1.)
            stmt = _insert_ignore(engine, records).returning(records.c.id)
            inserted += len(conn.execute(stmt, {**row, "created_at": now()}).fetchall())
        duplicates = len(rows) - inserted
        file_row = {**file_row, "n_inserted": inserted, "n_duplicates": duplicates, "processed_at": now()}
        conn.execute(_insert_ignore(engine, processed_files), [file_row])
    return inserted, duplicates


def save_run(engine: Engine, values: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(run_log.insert(), [values])


def save_summary(engine: Engine, text: str, stats: dict[str, Any], generated_by: str) -> None:
    with engine.begin() as conn:
        conn.execute(summaries.insert(), [{
            "created_at": now(), "text": text,
            "stats_json": json.dumps(stats, ensure_ascii=False, default=str), "generated_by": generated_by,
        }])


def read_table(engine: Engine, table: Table, order_by=None, limit: int | None = None) -> pd.DataFrame:
    query = select(table)
    if order_by is not None:
        query = query.order_by(order_by)
    if limit:
        query = query.limit(limit)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def read_records(engine: Engine) -> pd.DataFrame:
    df = read_table(engine, records, order_by=records.c.fecha)
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def latest_summary(engine: Engine) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(select(summaries).order_by(summaries.c.id.desc()).limit(1)).mappings().first()
    return dict(row) if row else None


def count_records(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(records)).scalar_one()
