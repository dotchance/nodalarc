FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iperf3 \
    tcpdump \
    traceroute \
    iputils-ping \
    runit \
    && rm -rf /var/lib/apt/lists/*

CMD ["/bin/bash"]
