# KEEL — production image. Builds the wheel, installs it, runs `keel serve`.
FROM python:3.11-slim AS build
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.11-slim
LABEL org.opencontainers.image.title="KEEL" \
      org.opencontainers.image.description="Runtime trust layer for agentic AI"
RUN useradd -m -u 10001 keel
WORKDIR /app
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl stripe
ENV KEEL_DATA_DIR=/data \
    KEEL_AUTH_REQUIRED=1 \
    KEEL_PORT=8347 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown keel:keel /data
USER keel
VOLUME ["/data"]
EXPOSE 8347
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8347/healthz')"
CMD ["keel", "serve", "--host", "0.0.0.0", "--port", "8347", "--log-level", "info"]
