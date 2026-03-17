# NodalPath container — MPLS path computation and forwarding table push
#
# All config comes from ConfigMap mounts at /etc/nodalarc/.
# No config files baked into the image.
#
# Build context must be the repo root:
#   docker build -f nodalpath/Dockerfile -t nodalarc/nodalpath:latest .

FROM python:3.14-slim

WORKDIR /app

# Install Python dependencies (subset of project deps needed by NodalPath)
RUN pip install --no-cache-dir \
    pydantic>=2.0 \
    pyzmq \
    pyyaml \
    numpy \
    grpcio>=1.74 \
    protobuf>=6.31.1 \
    fastapi \
    uvicorn \
    httpx>=0.28.1

# Copy shared library, NodalPath module, and static reference configs
COPY lib/ lib/
COPY nodalpath/ nodalpath/
COPY configs/satellite-types/ configs/satellite-types/
COPY configs/ground-stations/ configs/ground-stations/
COPY configs/constellations/ configs/constellations/

# Add lib/ to Python path so nodalarc package is importable
ENV PYTHONPATH=/app/lib:/app

# Console HTTP port
EXPOSE 3100
# ZMQ PUB port (almanac events)
EXPOSE 5567

ENTRYPOINT ["python", "-m", "nodalpath"]
