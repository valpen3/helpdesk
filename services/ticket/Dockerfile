# Сборка из корня репозитория: docker build -f services/ticket/Dockerfile .
# (корень нужен, чтобы в образ попал общий пакет libs/common)

# --- 1. Сборочный этап: ставим зависимости в отдельное окружение ---
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY libs/common ./libs/common
COPY services/ticket/pyproject.toml ./services/ticket/pyproject.toml
COPY services/ticket/app ./services/ticket/app
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ./libs/common ./services/ticket

# --- 2. Итоговый образ: только окружение, без сборочного мусора ---
FROM python:3.12-slim
# netcat понадобится на этапе 3 для проверки сегментации сети
RUN apt-get update \
 && apt-get install -y --no-install-recommends netcat-openbsd \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/ticket/alembic.ini /home/app/alembic.ini
COPY --chown=app:app services/ticket/alembic /home/app/alembic
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /home/app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
