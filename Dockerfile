# УРМ — образ веб-платформы. Сборка: docker compose build
FROM python:3.12-slim

ARG URM_VERSION=dev
LABEL org.opencontainers.image.title="urm-platform" \
      org.opencontainers.image.description="УРМ — учёт расходных материалов (Streamlit на витринах urm2)" \
      org.opencontainers.image.version="${URM_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Almaty \
    URM_SNAPSHOT_DIR=/app/snapshots URM_VERSION=${URM_VERSION}

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py VERSION ./
COPY .streamlit ./.streamlit
COPY urm ./urm
COPY views ./views
COPY tools ./tools
COPY sql ./sql
COPY assets ./assets

RUN useradd --create-home --uid 10001 urm && mkdir -p /app/snapshots && chown -R urm:urm /app
USER urm
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD curl -fsS http://localhost:8501/_stcore/health || exit 1
# Сначала снимок (если витрины не обновлялись — 1 с), затем интерфейс.
# Не собрался снимок — интерфейс всё равно стартует и покажет причину.
CMD ["sh", "-c", "python tools/build_snapshot.py || true; exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501"]
