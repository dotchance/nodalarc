# OME (Orbital Mechanics Engine) container — computes satellite positions,
# ISL visibility, and ground station access schedules.
#
# All session config comes from ConfigMap mounts at /etc/nodalarc/.
# Static reference data (satellite types, ground stations, constellations)
# is baked into the image.
#
# Build context must be the repo root:
#   docker build -f ome/Dockerfile -t nodalarc/ome:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    pydantic>=2.0 pyzmq pyyaml numpy sgp4 skyfield

# Add lib/ to Python path so nodalarc package is importable
ENV PYTHONPATH=/app/lib:/app

# Copy shared library, OME module, and static reference configs
COPY lib/ lib/
COPY ome/ ome/
COPY configs/satellite-types/ configs/satellite-types/
COPY configs/ground-stations/ configs/ground-stations/
COPY configs/constellations/ configs/constellations/

# Health probe port + ZMQ PUB port
EXPOSE 8081
EXPOSE 5560

ENTRYPOINT ["python", "-m", "ome.main"]
