"""Configuración central: lee variables de entorno (.env en local, Secrets en la nube)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _normalize_db_url(url: str) -> str:
    """Supabase entrega 'postgresql://...'; SQLAlchemy necesita indicar el driver psycopg 3."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        # Ruta relativa -> relativa a la raíz del proyecto
        rel = url[len("sqlite:///"):]
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    return url


@dataclass(frozen=True)
class Settings:
    authorized_sender: str
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str
    gmail_extra_query: str
    gmail_subject_filter: str
    database_url: str
    gemini_api_key: str
    gemini_model: str
    gemini_fallback_models: tuple[str, ...]
    download_dir: Path
    log_level: str

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key)


def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        authorized_sender=env("AUTHORIZED_SENDER", "").strip().lower(),
        gmail_client_id=env("GMAIL_CLIENT_ID", ""),
        gmail_client_secret=env("GMAIL_CLIENT_SECRET", ""),
        gmail_refresh_token=env("GMAIL_REFRESH_TOKEN", ""),
        gmail_extra_query=env("GMAIL_EXTRA_QUERY") or "newer_than:30d",
        gmail_subject_filter=env("GMAIL_SUBJECT_FILTER") or "Reporte operacional",
        database_url=_normalize_db_url(env("DATABASE_URL", "sqlite:///data/local.db")),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        gemini_model=env("GEMINI_MODEL", "gemini-3.6-flash"),
        gemini_fallback_models=tuple(m.strip() for m in env(
            "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash").split(",") if m.strip()),
        download_dir=ROOT / env("DOWNLOAD_DIR", "data/downloads"),
        log_level=env("LOG_LEVEL", "INFO"),
    )


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
