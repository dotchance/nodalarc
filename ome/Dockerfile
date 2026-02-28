FROM python:3.14-slim

WORKDIR /app

# Install shared library first (PRD 13.13)
COPY lib/ /app/lib/
RUN pip install --no-cache-dir /app/lib/

# Install OME dependencies
RUN pip install --no-cache-dir \
    pyzmq \
    pyyaml \
    pydantic>=2.0 \
    numpy \
    sgp4 \
    skyfield

# Copy OME code
COPY ome/ /app/ome/
COPY configs/ /app/configs/

CMD ["python", "-m", "ome.main"]
