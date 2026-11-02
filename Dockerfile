# Imagem base com versao fixada
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Cria usuario nao-root
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/static/uploads /app/uploads /app/data /backups /tmp/gunicorn \
    && chown -R app:app /app /backups /tmp/gunicorn

# Instala dependencias primeiro (cache de camadas)
COPY --chown=app:app requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copia codigo
COPY --chown=app:app *.py ./
COPY --chown=app:app api/ ./api/
COPY --chown=app:app tools/ ./tools/
COPY --chown=app:app static/ ./static/
COPY --chown=app:app gunicorn.conf.py ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

# Gunicorn estavel com configuracao dedicada
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "app:app"]
