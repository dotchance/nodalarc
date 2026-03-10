FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/ proto/
COPY fwd_server.py .
COPY netconf_stub.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 50051
EXPOSE 830

ENTRYPOINT ["/app/entrypoint.sh"]
