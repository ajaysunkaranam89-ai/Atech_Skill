# A TechSkills Academy – Django application image
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

# DejaVu fonts give PDFs (invoices, brochures) a real ₹ symbol.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Directories that become Docker volumes must exist (owned by app) in the image.
RUN mkdir -p /app/staticfiles /app/media /app/private_media /app/backups \
 && chown app:app /app && chown -R app:app /app/staticfiles /app/media /app/private_media /app/backups \
 && tr -d "\r" < /app/docker/entrypoint.sh > /tmp/ep && cat /tmp/ep > /app/docker/entrypoint.sh && chmod +x /app/docker/entrypoint.sh

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz/', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn"]
