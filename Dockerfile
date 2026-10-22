# syntax=docker/dockerfile:1
# Ravix — تصویر تولید، چندمرحله‌ای. اجرا: docker compose up -d --build   (docs/installer.md)
ARG PYTHON_VERSION=3.13

# ---------- مرحلهٔ ۱: وابستگی‌ها و فایل‌های استاتیک ----------
FROM python:${PYTHON_VERSION}-slim-trixie AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.lock requirements-optional.txt ./
# فقط wheel؛ کامپایلر لازم نیست (psycopg[binary]، Pillow، cryptography و ... wheel دارند)
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --only-binary=:all: -r requirements.lock -r requirements-optional.txt
COPY . .
# collectstatic با مقادیر ساختگیِ فقط-ساخت (هیچ‌کدام وارد تصویر نهایی نمی‌شوند؛ تنظیمات واقعی هنگام اجرا از محیط می‌آید)
RUN DJANGO_SETTINGS_MODULE=config.settings.production DJANGO_SECRET_KEY=build-only-not-a-secret-build-only-not-a-secret \
    DJANGO_ALLOWED_HOSTS=build DATABASE_URL=postgres://build:build@localhost/build \
    /opt/venv/bin/python manage.py collectstatic --noinput

# ---------- مرحلهٔ ۲: تصویر اجرا ----------
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime
# postgresql-client: pg_dump/pg_restore برای deploy/backup.sh (نسخهٔ کلاینت باید ≥ نسخهٔ سرور باشد؛ compose از postgres:17 استفاده می‌کند)
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 ravix \
 && useradd --system --uid 10001 --gid ravix --home-dir /data --shell /usr/sbin/nologin ravix \
 && mkdir -p /data/media /data/shop_private /data/plugins_installed /data/plugin_backups /data/themes_installed \
             /data/registry_packages /data/update_work /data/cache /backups \
 && chown -R ravix:ravix /data /backups && chmod 700 /backups
# کد فقط‌خواندنی برای کاربر برنامه (مالک root)؛ تنها /data و /backups قابل‌نوشتن‌اند.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
RUN ln -s /data/media /app/media && chmod +x /app/docker/*.sh /app/deploy/*.sh
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    GUNICORN_BIND=0.0.0.0:8000 \
    RAVIX_DATA_DIR=/data \
    RAVIX_SECRET_KEY_FILE=/data/secret_key \
    RAVIX_INSTALL_TOKEN_FILE=/data/install_token \
    RAVIX_SHOP_PRIVATE_ROOT=/data/shop_private \
    RAVIX_PLUGIN_INSTALL_DIR=/data/plugins_installed \
    RAVIX_PLUGIN_BACKUP_DIR=/data/plugin_backups \
    RAVIX_THEME_INSTALL_DIR=/data/themes_installed \
    RAVIX_REGISTRY_STORAGE=/data/registry_packages \
    RAVIX_UPDATE_WORK_DIR=/data/update_work \
    RAVIX_CACHE_DIR=/data/cache \
    MEDIA_DIR=/data/media BACKUP_DIR=/backups
WORKDIR /app
USER ravix
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "/app/docker/healthcheck.py"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
