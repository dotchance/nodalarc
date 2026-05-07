FROM debian:bookworm-slim@sha256:f9c6a2fd2ddbc23e336b6257a5245e31f996953ef06cd13a59fa0a1df2d5c252

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iperf3 \
    tcpdump \
    traceroute \
    iputils-ping \
    iputils-tracepath \
    runit \
    && rm -rf /var/lib/apt/lists/*

CMD ["/bin/bash"]
