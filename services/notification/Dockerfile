FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY libs/common ./libs/common
COPY services/notification ./services/notification
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install ./libs/common ./services/notification

FROM python:3.12-slim
RUN useradd --create-home --uid 10002 app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /home/app
EXPOSE 8000
CMD ["uvicorn", "helpdesk_notification.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
