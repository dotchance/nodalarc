FROM python:3.14-slim
WORKDIR /app

RUN pip install --no-cache-dir \
    kopf kubernetes pydantic>=2.0 pyyaml jinja2 numpy

COPY lib/ lib/
COPY nodalarc_operator/ nodalarc_operator/
COPY ome/__init__.py ome/__init__.py
COPY ome/constellation_loader.py ome/constellation_loader.py
COPY ome/propagator.py ome/propagator.py
COPY configs/ configs/

ENV PYTHONPATH=/app/lib:/app

CMD ["kopf", "run", "-m", "nodalarc_operator", "--namespace", "nodalarc", "--liveness=http://0.0.0.0:8080/healthz"]
