# K8s Operator container — watches ConstellationSpec CRD, manages sessions.
#
# Build context must be the repo root:
#   docker build -f services/nodalarc_operator/Dockerfile -t nodalarc/operator:latest .

FROM python:3.14-slim
WORKDIR /app

# iputils-ping for substrate latency measurement between nodes (M10)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping iputils-tracepath \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    kopf kubernetes pydantic>=2.0 pyyaml jinja2 numpy sgp4 skyfield

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes constellation_loader, orbital, models)
COPY lib/ lib/

# Copy operator service
COPY services/nodalarc_operator/ services/nodalarc_operator/

# Copy configs (FRR templates, satellite types, ground stations, etc.)
COPY configs/ configs/

CMD ["kopf", "run", "-m", "nodalarc_operator", "--namespace", "nodalarc", "--liveness=http://0.0.0.0:8080/healthz"]
