# VS-API container — FastAPI server for visualization state snapshots,
# REST endpoints, WebSocket streaming, and session management.
#
# All runtime config comes from ConfigMap mounts at /etc/nodalarc/.
# Static reference data (satellite types, ground stations, constellations,
# sessions, nodalpath config) is baked into the image at /app/configs/.
#
# Build context must be the repo root:
#   docker build -f services/vs_api/Dockerfile -t nodalarc/vs-api:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 nats-py pyyaml fastapi uvicorn httpx>=0.28.1 \
    websockets>=16.0 grpcio>=1.74 protobuf>=6.31.1 numpy \
    kubernetes sgp4 skyfield

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app
ENV PYTHONUNBUFFERED=1

# Copy shared library (includes proto/, constellation_loader, models)
COPY lib/ lib/

# Copy VS-API service
COPY services/vs_api/ services/vs_api/

# Copy OME service (coverage_preview used by wizard)
COPY services/ome/ services/ome/

# Copy nodalpath (session_loader, live path tracer, console state)
COPY nodalpath/ nodalpath/

# Bake in all config files
COPY configs/ configs/

# HTTP + WebSocket port
EXPOSE 8080

ENTRYPOINT ["python", "-m", "vs_api.main"]
