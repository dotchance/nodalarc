# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
# NodalArc HDTN image: NASA Glenn's High-rate Delay Tolerant Networking (HDTN)
# built from its pinned upstream release.
#
# This image packages HDTN unmodified (Apache-2.0): the hdtn-one-process
# node and the bundle tools (bpgen-async, bpsink-async, bping, bpsendfile,
# bpreceivefile, bpsendpacket, bpreceivepacket, bpfiletransfer), plus
# hdtn-node, a launcher that writes one node's HDTN configuration and
# contact plan from its environment and runs it. The image carries no node
# identity, no network and no tuning: a consuming workload profile supplies
# the node number, the contacts and their light times, and peer addresses
# arrive from the platform's address resolution.
#
# Build choices, each with its reason:
# - The release is fetched by tag and checked against its commit, so the
#   image is the published v3.0.0 and nothing else.
# - Every option is HDTN's default except USE_X86_HARDWARE_ACCELERATION and
#   LTP_RNG_USE_RDSEED, which are off. With them on, HDTN's configure step
#   tests the build machine's CPU and compiles for what it finds (RDSEED,
#   BMI1/BMI2, SSE4.2, AVX2): the image then stops with an illegal
#   instruction on a server whose CPU lacks one of them, and the build fails
#   on a machine where the one RDSEED call of the test returns no entropy,
#   which the instruction is allowed to do and did on a 13th-generation Intel
#   build machine. With them off, the image is built for the x86-64 baseline
#   and runs on any server; the bundle codecs and CRC use HDTN's portable
#   code. The web interface stays built (with Boost Beast, which downloads
#   nothing): bpfiletransfer includes its WebsocketServer header
#   unconditionally, and v3.0.0 does not compile with USE_WEB_INTERFACE=OFF.
#   A consuming profile turns the interface off at run time
#   (userInterfaceOn: false).
# - The Debian base is pinned by digest, the same one images/base uses, so a
#   rebuild starts from the same packages.
# - The runtime image adds bash (profiles write configs with it), iproute2
#   and iputils (inspection from the node's terminal). Debian stamps a
#   cap_net_raw file capability on ping; a NoNewPrivs container refuses to
#   exec a file-capability binary, so the capability and the setuid bit are
#   stripped and ping uses unprivileged ICMP sockets.

ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:f9c6a2fd2ddbc23e336b6257a5245e31f996953ef06cd13a59fa0a1df2d5c252

FROM ${DEBIAN_IMAGE} AS builder
ARG HDTN_TAG=v3.0.0
ARG HDTN_COMMIT=7fbe90cdbe3c8c737efaacb5985f881f95fc7d2a
ARG CORES=8

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git cmake build-essential \
        libboost-all-dev libssl-dev libzmq3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${HDTN_TAG}" https://github.com/nasa/HDTN.git /opt/hdtn-src \
    && test "$(git -C /opt/hdtn-src rev-parse HEAD)" = "${HDTN_COMMIT}"

RUN cmake -S /opt/hdtn-src -B /opt/hdtn-build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/hdtn \
        -DUSE_X86_HARDWARE_ACCELERATION=OFF \
        -DLTP_RNG_USE_RDSEED=OFF \
    && cmake --build /opt/hdtn-build -j "${CORES}" \
    && cmake --install /opt/hdtn-build \
    && install -D -m 0644 /opt/hdtn-src/LICENSE.md /opt/hdtn/share/doc/hdtn/LICENSE.md \
    && rm -f /opt/hdtn/bin/unit-tests /opt/hdtn/bin/integrated-tests
# HDTN installs its unit and integrated test programs beside the node; the
# runtime image carries only what a node runs, so they stay in this stage.

FROM ${DEBIAN_IMAGE}
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc HDTN" \
      org.opencontainers.image.description="NASA Glenn HDTN v3.0.0 bundle node and tools, with the hdtn-node launcher, for NodalArc emulation nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="AGPL-3.0-only AND Apache-2.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash libzmq5 libssl3 \
        libboost-filesystem1.74.0 libboost-program-options1.74.0 \
        libboost-thread1.74.0 libboost-regex1.74.0 libboost-random1.74.0 \
        libboost-timer1.74.0 libboost-iostreams1.74.0 libboost-date-time1.74.0 \
        libboost-log1.74.0 libboost-system1.74.0 libboost-chrono1.74.0 \
        libboost-atomic1.74.0 libboost-serialization1.74.0 \
        iproute2 iputils-ping iputils-tracepath libcap2-bin \
    && rm -rf /var/lib/apt/lists/* \
    && if getcap /usr/bin/ping | grep -q .; then setcap -r /usr/bin/ping; fi \
    && chmod u-s /usr/bin/ping \
    && test -z "$(getcap /usr/bin/ping)"

COPY --from=builder /opt/hdtn /opt/hdtn
# hdtn-node writes a node's HDTN configuration and contact plan from its
# environment and runs it; see the script's header for the contract.
COPY hdtn-node /usr/local/bin/hdtn-node
RUN chmod 0755 /usr/local/bin/hdtn-node
# A login shell (a node's terminal) resets PATH through /etc/profile, so the
# tools are added there as well as to the image environment.
ENV PATH="/opt/hdtn/bin:${PATH}"
RUN printf 'export PATH="/opt/hdtn/bin:$PATH"\n' > /etc/profile.d/hdtn.sh

# Every installed program must find its shared libraries in this image.
RUN for f in /opt/hdtn/bin/*; do \
        if ldd "$f" | grep -q "not found"; then echo "missing libraries for $f"; ldd "$f"; exit 1; fi; \
    done \
    && hdtn-one-process --help > /dev/null

# NodalArc's license, its additional terms, the third-party notices and the
# license texts they name (LICENSES/) go with every image whose label names
# AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The last instructions, so a change
# to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
COPY --from=nodalarc-legal LICENSES/ /usr/share/doc/nodalarc/LICENSES/
