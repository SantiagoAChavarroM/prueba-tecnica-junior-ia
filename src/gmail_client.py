"""Integración con Gmail API (OAuth 2.0, permiso de solo lectura).

Flujo: buscar correos del remitente con adjuntos -> validar remitente en el encabezado From
-> recorrer las partes del mensaje -> descargar adjuntos PDF/XLSX.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Iterator

from .config import Settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".xlsx": "excel"}
ALLOWED_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
}


@dataclass
class Attachment:
    filename: str
    file_type: str  # pdf | excel
    content: bytes


@dataclass
class EmailMessage:
    message_id: str
    sender: str
    subject: str
    received_at: datetime | None
    attachments: list[Attachment] = field(default_factory=list)


def attachment_type(filename: str, mime_type: str | None) -> str | None:
    lower = filename.lower()
    for ext, kind in ALLOWED_EXTENSIONS.items():
        if lower.endswith(ext):
            return kind
    return ALLOWED_MIME.get(mime_type or "")


def sender_is_authorized(from_header: str, authorized: str) -> bool:
    """Compara la dirección exacta (no basta con que el texto la contenga)."""
    _, address = parseaddr(from_header or "")
    return bool(authorized) and address.strip().lower() == authorized.strip().lower()


def build_service(settings: Settings):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    missing = [n for n, v in {
        "GMAIL_CLIENT_ID": settings.gmail_client_id,
        "GMAIL_CLIENT_SECRET": settings.gmail_client_secret,
        "GMAIL_REFRESH_TOKEN": settings.gmail_refresh_token,
        "AUTHORIZED_SENDER": settings.authorized_sender,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Faltan variables de entorno para Gmail: {', '.join(missing)}")

    creds = Credentials(
        token=None,
        refresh_token=settings.gmail_refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk_parts(payload: dict) -> Iterator[dict]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def list_candidate_ids(service, settings: Settings) -> list[str]:
    """Paso 'Nuevo correo': busca correos del remitente que tengan adjuntos."""
    # Identificación del correo relevante: remitente + asunto esperado + adjuntos + recientes.
    # Sin el filtro de asunto se procesarían TODOS los correos con adjuntos de ese remitente.
    subject = f'subject:"{settings.gmail_subject_filter}"' if settings.gmail_subject_filter else ""
    query = f"from:{settings.authorized_sender} {subject} has:attachment {settings.gmail_extra_query}".strip()
    ids: list[str] = []
    request = service.users().messages().list(userId="me", q=query, maxResults=100)
    while request is not None:
        response = request.execute()
        ids.extend(m["id"] for m in response.get("messages", []))
        request = service.users().messages().list_next(request, response)
    log.info("Gmail: %s correos coinciden con la búsqueda '%s'", len(ids), query)
    return ids


def fetch_message(service, message_id: str, settings: Settings) -> EmailMessage | None:
    """Descarga el correo; devuelve None si el remitente no es el autorizado."""
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    sender = headers.get("from", "")
    if not sender_is_authorized(sender, settings.authorized_sender):
        log.info("Correo %s ignorado: remitente no autorizado (%s)", message_id, sender)
        return None

    received = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
    email = EmailMessage(message_id, parseaddr(sender)[1], headers.get("subject", ""), received)

    for part in _walk_parts(msg["payload"]):
        filename = part.get("filename") or ""
        if not filename:
            continue
        kind = attachment_type(filename, part.get("mimeType"))
        if kind is None:
            log.info("Adjunto ignorado (formato no soportado): %s", filename)
            continue
        body = part.get("body", {})
        if "attachmentId" in body:
            data = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=body["attachmentId"]).execute()["data"]
        else:
            data = body.get("data", "")
        email.attachments.append(Attachment(filename, kind, base64.urlsafe_b64decode(data)))
    return email
