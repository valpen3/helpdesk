FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY libs/common ./libs/common
COPY services/sla ./services/sla
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ./libs/common ./services/sla

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends netcat-openbsd \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10003 app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/sla/alembic.ini /home/app/alembic.ini
COPY --chown=app:app services/sla/alembic /home/app/alembic
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /home/app
EXPOSE 8000
CMD ["uvicorn", "helpdesk_sla.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
