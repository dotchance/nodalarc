FROM nodalarc/base:latest

# Install FRR 10.x from official repo
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    lsb-release \
    && curl -s https://deb.frrouting.org/frr/keys.gpg \
       | tee /usr/share/keyrings/frrouting.gpg > /dev/null \
    && echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr bookworm frr-stable" \
       > /etc/apt/sources.list.d/frr.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    frr \
    frr-pythontools \
    && rm -rf /var/lib/apt/lists/*

# Copy daemons enable file
COPY daemons /etc/frr/daemons

# Create runit service directories
RUN mkdir -p /etc/service/zebra /etc/service/isisd /etc/service/ospfd /etc/service/pathd /etc/service/staticd

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
