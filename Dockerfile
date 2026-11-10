# syntax=docker/dockerfile:1
# One image for the whole app: FastAPI serves the API and the built React frontend.

FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# VITE_BASE / VITE_API_BASE_URL stay unset: the app is served from "/" on the API's own origin.
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    FINSIGHT_HOME=/data \
    FINSIGHT_WEB_DIST=/app/web/dist \
    HOST=0.0.0.0 \
    PORT=8000
WORKDIR /app
RUN useradd --system --create-home --uid 10001 finsight && mkdir -p /data && chown finsight:finsight /data
COPY pyproject.toml README.md ./
COPY finsight ./finsight
COPY config ./config
COPY fixtures ./fixtures
# Editable install: the engine reads config/ and fixtures/ next to the package.
RUN pip install -e ".[web]"
COPY --from=web /src/web/dist ./web/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# The SQLite database, the SEC response cache and generated reports live here. Mount a
# persistent disk at /data, or runs are lost when the container is replaced.
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)"

ENTRYPOINT ["docker-entrypoint.sh"]
# One process on purpose: background analyses run inside it, so do not add workers or replicas.
CMD ["python", "-m", "finsight.api"]
