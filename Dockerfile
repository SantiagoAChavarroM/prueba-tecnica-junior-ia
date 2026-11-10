# Imagen única para el dashboard y el pipeline.
#   docker build -t prueba-tecnica .
#   docker run --env-file .env -p 8501:8501 prueba-tecnica                         # dashboard
#   docker run --env-file .env prueba-tecnica python -m src.pipeline --source gmail  # pipeline
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
