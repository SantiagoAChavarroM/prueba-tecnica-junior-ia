"""Autorización OAuth de Gmail (se ejecuta UNA sola vez en tu computador).

1. Descarga el JSON del cliente OAuth (tipo "App de escritorio") desde Google Cloud Console
   y guárdalo en la raíz del proyecto como credentials.json (está en .gitignore).
2. Ejecuta:  python scripts/gmail_auth.py
3. Se abre el navegador, inicias sesión con la cuenta que recibe los correos y aceptas el permiso
   de SOLO LECTURA. El script escribe GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET y GMAIL_REFRESH_TOKEN
   directamente en .env (no se muestran en pantalla).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.gmail_client import SCOPES  # noqa: E402

CREDENTIALS = ROOT / "credentials.json"
ENV = ROOT / ".env"


def set_env_value(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
        return re.sub(rf"^{key}=.*$", line, text, flags=re.MULTILINE)
    return text.rstrip("\n") + f"\n{line}\n"


def main() -> None:
    if not CREDENTIALS.exists():
        sys.exit(f"No encuentro {CREDENTIALS}. Descárgalo de Google Cloud Console (ver README).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        sys.exit("Google no devolvió refresh token. Revoca el acceso en myaccount.google.com/permissions y repite.")

    client = json.loads(CREDENTIALS.read_text())
    client = client.get("installed") or client.get("web")
    text = ENV.read_text() if ENV.exists() else (ROOT / ".env.example").read_text()
    for key, value in {"GMAIL_CLIENT_ID": client["client_id"], "GMAIL_CLIENT_SECRET": client["client_secret"],
                       "GMAIL_REFRESH_TOKEN": creds.refresh_token}.items():
        text = set_env_value(text, key, value)
    ENV.write_text(text)
    print("Listo: credenciales de Gmail guardadas en .env (no se muestran por seguridad).")


if __name__ == "__main__":
    main()
