# PulseWatch — Production Dockerfile for Umbrel
# Builds a minimal image published to ghcr.io
# Multi-arch: linux/amd64 + linux/arm64 (Raspberry Pi / Umbrel hardware)

FROM python:3.11-slim AS base

# Build args for architecture targeting
ARG TARGETPLATFORM
ARG BUILDPLATFORM

LABEL org.opencontainers.image.title="PulseWatch"
LABEL org.opencontainers.image.description="Self-hosted uptime monitoring for Umbrel"
LABEL org.opencontainers.image.version="4.0.0"
LABEL org.opencontainers.image.source="https://github.com/YOUR-GITHUB-USERNAME/umbrel-pulsewatch"
LABEL org.opencontainers.image.licenses="MIT"

# System deps — curl for healthcheck fallback, build-essential for psutil C ext
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY pwa.py .

# Create data directory (will be volume-mounted by Umbrel)
RUN mkdir -p /data && chmod 777 /data

# Non-root user for security
RUN useradd -r -u 1001 -g root pulsewatch && \
    chown -R pulsewatch:root /app /data
USER pulsewatch

EXPOSE 4000

# Healthcheck — Umbrel uses this to detect if the app is ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:4000/favicon.svg', timeout=5)" || exit 1

# Use gunicorn with 2 workers — enough for a home server, low memory footprint
CMD ["gunicorn", \
     "--bind", "0.0.0.0:4000", \
     "--workers", "2", \
     "--threads", "2", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
