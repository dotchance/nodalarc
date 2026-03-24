# Scheduler container — consumes OME ZMQ events, dispatches topology
# changes to Node Agents via gRPC, publishes TO events on port 5561.
#
# All session config comes from ConfigMap mounts at /etc/nodalarc/.
# Static reference data (satellite types, ground stations, constellations)
# is baked into the image.
#
# Build context must be the repo root:
#   docker build -f scheduler/Dockerfile -t nodalarc/scheduler:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyzmq pyyaml numpy grpcio>=1.74 protobuf>=6.31.1 \
    kubernetes jinja2 sgp4 skyfield httpx>=0.28.1

# Add lib/ to Python path so nodalarc package is importable
ENV PYTHONPATH=/app/lib:/app

# Copy shared library
COPY lib/ lib/

# Copy scheduler module
COPY scheduler/ scheduler/

# Copy node_agent proto stubs (needed by agent_pool gRPC client)
COPY node_agent/__init__.py node_agent/__init__.py
COPY node_agent/proto/ node_agent/proto/

# Copy OME module (constellation_loader for interface_map building)
COPY ome/ ome/

# Copy static reference configs
COPY configs/satellite-types/ configs/satellite-types/
COPY configs/ground-stations/ configs/ground-stations/
COPY configs/constellations/ configs/constellations/

# ZMQ PUB port + scenario inject + playback control + link catchup
EXPOSE 5561 5564 5566 5569

ENTRYPOINT ["python", "-m", "scheduler"]
