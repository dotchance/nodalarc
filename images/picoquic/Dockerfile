# NodalArc picoquic image: a generic picoquic endpoint engine.
#
# This image builds upstream picoquic (MIT) at a pinned commit, with the
# picotls its build fetches, and ships the picoquicdemo binary as a
# zero-capability, non-root, read-only-rootfs QUIC endpoint. It carries no
# NodalArc code and no tuning: transport parameters, roles, and addresses
# arrive as container arguments from the consuming workload profile. The
# Debian base is pinned by digest, the same one images/base uses.
#
# picoquicdemo links picotls's core and its minicrypto library. picotls is MIT
# and states its license in each source file's header, not in a license file,
# so the header of picotls.h is kept as its license text. minicrypto contains
# cifra (CC0-1.0) and micro-ecc (BSD-2-Clause), whose license files are kept
# beside it. All of them are under /usr/share/doc in the image.

ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:f9c6a2fd2ddbc23e336b6257a5245e31f996953ef06cd13a59fa0a1df2d5c252

FROM ${DEBIAN_IMAGE} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake gcc g++ make pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG PICOQUIC_COMMIT=467cb81bcafc652bae2a1c8824b70f12f470039c
RUN git clone https://github.com/private-octopus/picoquic.git /src/picoquic \
    && cd /src/picoquic \
    && git checkout "$PICOQUIC_COMMIT"
RUN cd /src/picoquic \
    && cmake -DCMAKE_BUILD_TYPE=Release -DPICOQUIC_FETCH_PTLS=Y . \
    && make -j"$(nproc)" picoquicdemo
RUN ptls=/src/picoquic/_deps/picotls-src \
    && install -D -m 0644 /src/picoquic/LICENSE /licenses/picoquic/LICENSE \
    && mkdir -p /licenses/picotls \
    && sed -n '1,/\*\//p' "$ptls/include/picotls.h" > /licenses/picotls/LICENSE \
    && grep -q "Permission is hereby granted" /licenses/picotls/LICENSE \
    && install -m 0644 "$ptls/deps/cifra/COPYING" /licenses/picotls/cifra-COPYING \
    && install -m 0644 "$ptls/deps/micro-ecc/LICENSE.txt" /licenses/picotls/micro-ecc-LICENSE.txt

FROM ${DEBIAN_IMAGE}
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc picoquic" \
      org.opencontainers.image.description="picoquic QUIC endpoint (picoquicdemo) for NodalArc emulation nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="MIT AND BSD-2-Clause AND CC0-1.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}
# iproute2/iputils/traceroute: endpoint diagnostics (ip, tracepath, ping,
# traceroute) so a host node is inspectable from inside its own container.
# Debian stamps a cap_net_raw file capability on ping at install; a
# NoNewPrivs container refuses to exec any file-capability binary, so the
# capability and the setuid fallback are both stripped here. ping instead
# uses unprivileged ICMP datagram sockets, which the consuming platform
# enables via the net.ipv4.ping_group_range sysctl.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libssl3 ca-certificates iproute2 iputils-ping iputils-tracepath traceroute libcap2-bin \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --user-group quic \
    && if getcap /usr/bin/ping | grep -q .; then setcap -r /usr/bin/ping; fi \
    && chmod u-s /usr/bin/ping \
    && test -z "$(getcap /usr/bin/ping)"
COPY --from=build /src/picoquic/picoquicdemo /usr/local/bin/picoquicdemo
# Demo-grade test certificates from the upstream tree; real deployments
# mount their own.
COPY --from=build /src/picoquic/certs/cert.pem /etc/picoquic/cert.pem
COPY --from=build /src/picoquic/certs/key.pem /etc/picoquic/key.pem
COPY --from=build /licenses/ /usr/share/doc/
USER 10001
ENTRYPOINT ["/usr/local/bin/picoquicdemo"]
