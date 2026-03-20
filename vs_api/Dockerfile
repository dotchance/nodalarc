# VS-API container — FastAPI server for visualization state snapshots,
# REST endpoints, WebSocket streaming, and session management.
#
# All runtime config comes from ConfigMap mounts at /etc/nodalarc/.
# Static reference data (satellite types, ground stations, constellations,
# sessions, nodalpath config) is baked into the image at /app/configs/.
#
# Build context must be the repo root:
#   docker build -f vs_api/Dockerfile -t nodalarc/vs-api:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyzmq pyyaml fastapi uvicorn httpx>=0.28.1 \
    websockets>=16.0 grpcio>=1.74 protobuf>=6.31.1 numpy \
    kubernetes

# Add lib/ to Python path so nodalarc package is importable
ENV PYTHONPATH=/app/lib:/app
ENV PYTHONUNBUFFERED=1

# Copy shared library
COPY lib/ lib/

# Copy VS-API module
COPY vs_api/ vs_api/

# Copy dependencies used by VS-API at runtime:
# - ome/constellation_loader.py for session context loading
# - nodalpath/ for session_loader, platform config, live path tracer
# - node_agent/proto/ for gRPC stubs used in live trace
COPY ome/ ome/
COPY nodalpath/ nodalpath/
COPY node_agent/__init__.py node_agent/__init__.py
COPY node_agent/proto/ node_agent/proto/

# Bake in all config files (satellite types, ground stations,
# constellations, sessions, nodalpath config)
COPY configs/ configs/

# HTTP + WebSocket port
EXPOSE 8080

ENTRYPOINT ["python", "-m", "vs_api.main"]
