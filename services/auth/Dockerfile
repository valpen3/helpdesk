FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY libs/common ./libs/common
COPY services/auth/pyproject.toml ./services/auth/pyproject.toml
COPY services/auth/app ./services/auth/app
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ./libs/common ./services/auth

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10002 app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/auth/alembic.ini /home/app/alembic.ini
COPY --chown=app:app services/auth/alembic /home/app/alembic
COPY services/auth/entrypoint.sh /usr/local/bin/auth-entrypoint.sh
RUN chmod 755 /usr/local/bin/auth-entrypoint.sh
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /home/app
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/auth-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
