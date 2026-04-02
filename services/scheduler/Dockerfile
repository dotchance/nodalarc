# Scheduler container — consumes OME NATS events, dispatches topology
# changes to Node Agents via NATS request/reply.
#
# All session config comes from ConfigMap mounts at /etc/nodalarc/.
# Static reference data (satellite types, ground stations, constellations)
# is baked into the image.
#
# Build context must be the repo root:
#   docker build -f services/scheduler/Dockerfile -t nodalarc/scheduler:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyzmq nats-py pyyaml numpy grpcio>=1.74 protobuf>=6.31.1 \
    kubernetes jinja2 sgp4 skyfield httpx>=0.28.1

# Add lib/ and services/ to Python path
ENV PYTHONPATH=/app/lib:/app/services:/app

# Copy shared library (includes proto/, constellation_loader, geo, models)
COPY lib/ lib/

# Copy scheduler service
COPY services/scheduler/ services/scheduler/

# Copy static reference configs
COPY configs/satellite-types/ configs/satellite-types/
COPY configs/ground-stations/ configs/ground-stations/
COPY configs/constellations/ configs/constellations/

ENTRYPOINT ["python", "-m", "scheduler"]
