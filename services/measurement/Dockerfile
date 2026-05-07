# MI (Measurement & Instrumentation) container — protocol adapters,
# convergence detection, probe management, trace endpoint.
#
# Build context must be the repo root:
#   docker build -f services/measurement/Dockerfile -t nodalarc/measurement:latest .

FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033

# kubectl needed for pod discovery
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
       -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    pydantic>=2.0 nats-py pyyaml fastapi uvicorn kubernetes

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library
COPY lib/ lib/

# Copy measurement service
COPY services/measurement/ services/measurement/

# Data directory for SQLite
RUN mkdir -p /data

ENTRYPOINT ["python", "-m", "measurement.mi_main"]
