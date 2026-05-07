# K8s Operator container — watches ConstellationSpec CRD, manages sessions.
#
# Build context must be the repo root:
#   docker build -f services/nodalarc_operator/Dockerfile -t nodalarc/operator:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
WORKDIR /app

# iputils-ping for substrate latency measurement between nodes (M10)
# openssh-client for ssh-keygen (terminal access SSH keypair generation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    iputils-ping iputils-tracepath openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    kopf kubernetes pydantic>=2.0 pyyaml jinja2 numpy sgp4 skyfield nats-py

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes constellation_loader, orbital, models)
COPY lib/ lib/

# Copy operator service
COPY services/nodalarc_operator/ services/nodalarc_operator/

# Copy configs (FRR templates, satellite types, ground stations, etc.)
COPY configs/ configs/

CMD ["kopf", "run", "-m", "nodalarc_operator", "--namespace", "nodalarc", "--liveness=http://0.0.0.0:8090/healthz"]
