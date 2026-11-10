# Prueba Técnica Junior — IA & Automatización

Solución que **automatiza la recepción, el procesamiento y la visualización** de reportes operacionales
que llegan por correo electrónico en PDF y Excel.

```
Correo → Identificación → Descarga de archivos → Procesamiento → Consolidación de datos → Dashboard
```

- **Aplicación desplegada (dashboard):** `<URL de Streamlit Community Cloud>`
- **Automatización:** GitHub Actions, pestaña *Actions* → "Pipeline de correos" (cada 15 min)

---

## 1. Descripción de la solución

1. Un **workflow de GitHub Actions** se ejecuta cada 15 minutos (o manualmente).
2. Se conecta a **Gmail API** (OAuth, solo lectura) y busca correos del **remitente autorizado** que tengan adjuntos.
3. Valida que el remitente sea exactamente el autorizado, descarga los adjuntos **PDF y XLSX** y omite
   los correos y archivos que ya se procesaron.
4. Extrae los datos con parsers deterministas (Excel: `openpyxl`; PDF: `pdfplumber`) y los transforma a una
   **estructura común**. Si un PDF no sigue la estructura esperada, la **IA (Gemini)** lo interpreta.
5. **Valida** cada registro (campos obligatorios, valores, fechas, estados, referencias repetidas) y lo guarda en
   **Supabase (PostgreSQL)** sin duplicados.
6. La **IA genera un resumen ejecutivo**. El código verifica que el resumen no traiga cifras inventadas.
7. El **dashboard en Streamlit** muestra los indicadores, la calidad de los datos, el historial, los logs y una
   **consulta en lenguaje natural**.

## 2. Arquitectura

```
Email (remitente autorizado)
  │
  ▼
Gmail API (OAuth, gmail.readonly)
  │
  ▼
GitHub Actions (cron */15) ── python -m src.pipeline
  │
  ├── Validar remitente + deduplicar correo (message_id) y archivo (SHA-256)
  ├── Excel Parser (openpyxl)
  ├── PDF Parser   (pdfplumber: tablas o "Campo: valor")
  └── AI Processing (Gemini)
        ├── interpretar PDF fuera de formato (respaldo)
        └── resumen ejecutivo con verificación de cifras
          │
          ▼
       Database (Supabase PostgreSQL · SQLite en local)
          │
          ▼
       Dashboard (Streamlit Community Cloud)
          └── IA: pregunta en lenguaje natural → consulta JSON → pandas
```

En [docs/arquitectura.md](docs/arquitectura.md) están el diagrama detallado (también en Mermaid), el modelo
de datos y la estrategia contra duplicados.

### Estructura del repositorio

```
src/
  config.py            Variables de entorno
  gmail_client.py      Gmail API: búsqueda, validación de remitente, descarga de adjuntos
  parsers/
    common.py          Estructura común + normalización (fechas, montos, estados, encabezados)
    excel_parser.py    Lectura de .xlsx
    pdf_parser.py      Lectura de PDF (tabla o ficha)
  validation.py        Reglas de validación y huella única de registro
  analytics.py         Indicadores, valores atípicos, resumen de respaldo
  ai.py                Usos de IA (Gemini) y su verificación
  db.py                Tablas y persistencia (SQLAlchemy)
  pipeline.py          Orquestador del flujo completo (CLI)
dashboard/app.py       Dashboard Streamlit
scripts/
  gmail_auth.py        Obtiene el refresh token de Gmail (una sola vez)
  generate_samples.py  Genera PDF/XLSX de ejemplo
samples/               Archivos de ejemplo para la demo
tests/                 Tests automatizados (pytest)
.github/workflows/     pipeline.yml (cron) y tests.yml
```

## 3. Tecnologías seleccionadas

| Necesidad | Tecnología | Por qué |
|---|---|---|
| Lenguaje | Python 3.12 | Buen ecosistema para datos, PDF/Excel e IA; un solo lenguaje en toda la solución |
| Correo | Gmail API + OAuth 2.0 | API oficial; permiso de solo lectura; búsqueda por remitente y adjuntos en el servidor |
| Excel | openpyxl | Lee .xlsx sin Excel instalado |
| PDF | pdfplumber | Extrae tablas y texto de PDF digitales |
| Persistencia | SQLAlchemy + Supabase (PostgreSQL) | Gratis y accesible desde Actions y Streamlit; en local el mismo código usa SQLite |
| IA | Google Gemini (`gemini-3.6-flash`) | Free tier suficiente; salida JSON nativa |
| Automatización | GitHub Actions (cron) | Gratis, sin servidor que mantener encendido, con logs y alertas incluidas |
| Dashboard | Streamlit + Plotly | Dashboard interactivo en Python; despliegue gratis en Streamlit Community Cloud |
| Calidad | pytest, Docker | Tests en cada push; imagen reproducible |

## 4. Cómo ejecutar el proyecto

### 4.1 Demo local en 2 minutos (sin correo ni nube)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # por defecto usa SQLite local

python scripts/generate_samples.py         # crea PDF/XLSX de ejemplo en samples/
python -m src.pipeline --source local --path samples/inbox
streamlit run dashboard/app.py             # abre http://localhost:8501
```

Si vuelves a ejecutar el pipeline, no se inserta nada nuevo: así se ve la deduplicación.
Para ver la IA interpretando un PDF fuera de formato (requiere `GEMINI_API_KEY` en `.env`):

```bash
python -m src.pipeline --source local --path samples/fuera_de_formato
```

### 4.2 Modo real (Gmail)

```bash
python -m src.pipeline            # usa Gmail (ver sección 5)
```

### 4.3 Tests

```bash
pytest -q
```

### 4.4 Docker (opcional)

```bash
docker build -t prueba-tecnica .
docker run --env-file .env -p 8501:8501 prueba-tecnica                            # dashboard
docker run --env-file .env prueba-tecnica python -m src.pipeline --source gmail   # pipeline
```

## 5. Cómo configurar la integración con correo (Gmail API)

1. Entra a [Google Cloud Console](https://console.cloud.google.com/) y crea un proyecto.
2. Ve a **APIs y servicios → Biblioteca** y habilita **Gmail API**.
3. Configura la **pantalla de consentimiento OAuth**: tipo *Externo*; en *Usuarios de prueba* agrega la cuenta
   de Gmail que recibe los reportes. La app queda en modo **Prueba** (ver Limitaciones).
4. En **Credenciales**, crea un **ID de cliente OAuth** de tipo **App de escritorio** y descarga el JSON
   como `credentials.json` en la raíz del proyecto (está en `.gitignore`).
5. Ejecuta `python scripts/gmail_auth.py`, inicia sesión y acepta el permiso de **solo lectura**.
   El script escribe `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` y `GMAIL_REFRESH_TOKEN` en `.env`.
6. Define `AUTHORIZED_SENDER` en `.env` con la dirección del remitente autorizado.

**Para la nube**, configura los mismos valores como *Secrets* del repositorio
(Settings → Secrets and variables → Actions):
`AUTHORIZED_SENDER`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `DATABASE_URL` y `GEMINI_API_KEY`.

### Base de datos (Supabase)

1. Crea un proyecto gratis en [supabase.com](https://supabase.com).
2. Ve a **Connect → Session pooler** y copia la cadena `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Usa el *pooler* porque la conexión directa es solo IPv6 y GitHub Actions no tiene IPv6.
3. Ponla en `DATABASE_URL`. Las tablas se crean solas en la primera ejecución.

### Dashboard (Streamlit Community Cloud)

1. En [share.streamlit.io](https://share.streamlit.io) crea una app desde este repositorio; el archivo principal es `dashboard/app.py`
   y la versión de Python, 3.12.
2. En **Settings → Secrets** pega `DATABASE_URL` y `GEMINI_API_KEY` (ver `.streamlit/secrets.toml.example`).

## 6. Cómo funciona el flujo de automatización

| Paso del enunciado | Implementación |
|---|---|
| Nuevo correo | `pipeline.yml` corre cada 15 min; `list_candidate_ids` busca `from:<remitente> has:attachment` |
| Validar remitente | `sender_is_authorized`: compara la dirección **exacta** del encabezado `From` (no basta con que el texto la contenga) |
| Detectar adjuntos | Recorre todas las partes MIME; solo acepta `.pdf` y `.xlsx` |
| Descargar documentos | `attachments().get` de Gmail; copia en `data/downloads` (y se guarda como *artifact* de Actions por 7 días) |
| Procesar PDF / Excel | `excel_parser`, `pdf_parser`; IA como respaldo para PDF fuera de formato |
| Validar información | `validation.py`: obligatorios, valor > 0, fechas, catálogo de estados, referencias repetidas |
| Consolidar datos | `db.save_file_and_records`: una transacción e `INSERT … ON CONFLICT DO NOTHING` |
| Actualizar dashboard | Se genera el resumen ejecutivo; Streamlit relee la base cada 60 s o con el botón "Actualizar" |

**Manejo de errores y alertas.** Si un archivo está dañado o no se puede leer, no detiene el resto: queda
registrado con estado `error` en el historial, aparece como **anotación de advertencia** en la ejecución de
GitHub Actions y en el resumen del job. Si falla el sistema (por ejemplo, credenciales vencidas), el job
termina en rojo y GitHub envía un correo al dueño del repositorio.
Cada ejecución queda en `run_log` y se ve en la pestaña "Historial y logs" del dashboard.

## 7. Uso de Inteligencia Artificial

**Criterio:** la IA se usa solo donde la programación tradicional no basta. Leer Excel, leer las tablas del PDF,
validar campos, detectar duplicados, calcular métricas y detectar valores atípicos (regla IQR) se hacen con
código, porque es más rápido, gratis, exacto y fácil de probar.

| Uso | Problema que resuelve | Cómo se controla |
|---|---|---|
| **Interpretar PDF fuera de formato** (`ai.interpret_pdf_text`) | A veces el remitente manda una nota en texto libre en vez del formato estándar. Una expresión regular no entiende "dos millones cuatrocientos mil pesos" ni frases libres. | Solo se usa si el parser determinista falla. El modelo devuelve JSON, que pasa por la **misma validación** que los demás. Se descarta el registro si el cliente no aparece literalmente en el PDF, y los registros quedan marcados como "extraído por IA: verificar". |
| **Resumen ejecutivo** (`ai.executive_summary`) | La gerencia quiere un párrafo interpretado con prioridades y recomendaciones, no solo gráficos. | El código calcula **todas** las cifras y el modelo solo redacta. `verify_summary` compara cada número del texto con los indicadores; si hay una cifra inventada, se descarta el texto y se usa una plantilla. |
| **Pregunta en lenguaje natural** (`ai.question_to_query`) | Un usuario no técnico pregunta, por ejemplo, "¿Qué 3 clientes tienen más valor en Servicio A?" sin saber SQL ni usar filtros. | El modelo **no calcula ni ejecuta código**: traduce la pregunta a un JSON con operaciones permitidas, `validate_query` lo revisa contra una lista blanca y pandas hace el cálculo. El JSON se muestra al usuario para que lo pueda revisar. |

Sin `GEMINI_API_KEY` todo sigue funcionando: el resumen se genera con una plantilla y la consulta en lenguaje
natural se deshabilita. Los prompts que usa la aplicación con Gemini están en `src/ai.py`
(`PDF_PROMPT`, `SUMMARY_PROMPT`, `QUERY_PROMPT`), con `temperature=0`.

## 8. Dashboard: métricas

**Obligatorias:** total de registros procesados, valor total, registros por categoría, registros por estado y
evolución en el tiempo (por día, semana o mes, en valor o en número de registros).

**Adicionales y por qué:**
- **Tasa de completado y valor pendiente de cierre:** el valor total no dice cuánto se concretó. Estas dos métricas
  muestran el trabajo en curso y el dinero aún no realizado, que es lo que necesita seguimiento.
- **Calidad de datos (% de registros sin alertas)**, más incompletos y valores atípicos: los indicadores solo valen
  si los datos de origen son confiables. Esta métrica dice si hay que pedirle correcciones al remitente.
- **Ticket promedio:** permite comparar periodos aunque cambie la cantidad de registros.
- **Concentración en el top 5 de clientes:** depender de pocos clientes es un riesgo comercial.

También incluye filtros por periodo, categoría, estado y origen; historial de archivos y correos; log de
ejecuciones; y descarga en CSV.

## 9. Limitaciones conocidas

- **Token de Gmail:** la app OAuth está en modo *Prueba*, así que Google vence el refresh token a los **7 días**.
  Pasarla a *Producción* exige publicar una página principal y una política de privacidad, algo fuera del alcance
  de la prueba. Cuando el token vence, el job de Actions falla en rojo (GitHub envía un correo de aviso); se
  renueva en 1 minuto con `python scripts/gmail_auth.py` y actualizando el secret `GMAIL_REFRESH_TOKEN`.
  El dashboard sigue funcionando con los datos ya cargados.
- **GitHub Actions cron:** no es tiempo real. GitHub puede retrasar la ejecución algunos minutos, y en repos
  públicos **desactiva los cron tras 60 días sin actividad** (se reactiva con un clic o con un commit).
- **Streamlit Community Cloud:** la app se "duerme" tras unos días sin visitas; la primera carga tarda ~30 s en despertar.
- **Supabase free:** pausa el proyecto tras 1 semana sin actividad. El cron cada 15 min lo mantiene activo; si se
  pausa, se reactiva desde el panel.
- **Gemini free tier:** tiene límites de solicitudes por minuto y por día. Ante un error 429 se reintenta y, si
  sigue fallando, se usa la plantilla. Los datos enviados en el free tier pueden usarse para mejorar los modelos
  de Google; con datos reales sensibles convendría el plan de pago o anonimizar.
- **Disponibilidad de modelos Gemini:** Google retira modelos y en horas pico responde 503. Por eso el modelo y
  sus respaldos son configurables (`GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`) y ninguna falla de IA detiene el pipeline.
- **PDF escaneados (imagen):** no tienen texto extraíble; se registran como error. Haría falta OCR.
- **Deduplicación por contenido:** si llega el mismo registro con el **estado cambiado** (de Pendiente a
  Completado), se guarda como un registro nuevo. Para tratarlo como actualización habría que definir con negocio
  si el *Número de referencia* es una clave única.
- **Remitente:** se valida la dirección del encabezado `From`. Para blindarlo contra suplantación se podrían
  validar también SPF/DKIM (`Authentication-Results`).
- El historial de adjuntos descargados se conserva 7 días como *artifact* de GitHub; los datos extraídos quedan
  permanentemente en la base.

## 10. Seguridad

- Ningún secreto está en el repositorio: `.env`, `credentials.json` y `.streamlit/secrets.toml` están en `.gitignore`;
  en la nube se usan los *Secrets* de GitHub y de Streamlit.
- Gmail se usa con permiso de **solo lectura**.
- La salida del LLM nunca se ejecuta como código: se valida contra una lista blanca.
