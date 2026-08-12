# syntax=docker/dockerfile:1

# ---- Build stage: resolve deps in an isolated layer so the final image
# doesn't carry pip's cache / build tools ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

# Keep Python from writing .pyc files / buffering stdout — both matter for
# clean container logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY --from=builder /install /usr/local

# Non-root user: don't run the app as root inside the container.
RUN groupadd --system echomind && useradd --system --gid echomind --home-dir /app echomind

COPY --chown=echomind:echomind . .

# Data/log dirs are meant to be volume-mounted (see docker-compose.yml), but
# create them with the right ownership so a bare `docker run` still works.
RUN mkdir -p /app/data /app/logs && chown -R echomind:echomind /app/data /app/logs

USER echomind

EXPOSE 5000

# Hits the app's own liveness probe — no extra OS packages (curl) needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).status == 200 else 1)"

COPY --chown=echomind:echomind docker-entrypoint.sh /app/docker-entrypoint.sh
ENTRYPOINT ["/app/docker-entrypoint.sh"]
