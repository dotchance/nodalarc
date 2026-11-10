# NodalArc ION image: NASA JPL's Interplanetary Overlay Network (ION-DTN)
# built from its pinned upstream release.
#
# This image packages ION unmodified (Caltech/JPL BSD-style license, see
# /opt/ion/share/doc/ion/license.txt in the image): the ION daemons and
# administration programs, its convergence layers (LTP over UDP among them)
# and its bundle applications (bpsendfile, bprecvfile, bping, bpsource,
# bpsink, bpstats and the rest of ION's install), plus ion-node, a launcher
# that writes one node's ION configuration and contact plan from its
# environment and runs it. The image carries no node identity, no network
# and no tuning: a consuming workload profile supplies the node's network,
# its contacts and their light times, and peer addresses arrive from the
# platform's address resolution.
#
# Build choices, each with its reason:
# - The release is fetched by tag and checked against its commit, so the
#   image is the published 4.2.0 and nothing else. The repository carries no
#   generated configure script, so autoreconf makes one, as ION's quick start
#   guide directs.
# - Every configure option is ION's default (BPv7, no cipher suite library).
# - The runtime image keeps the installed programs, ION's shared libraries,
#   its start, stop and process-list files and its license. Headers, static
#   libraries, libtool archives and manual pages are build products a node
#   never reads.
# - The runtime image adds bash (the launcher), procps (ION's killm finds
#   processes with pgrep), iproute2 and iputils (the launcher reads the
#   node's addresses; inspection from the node's terminal). Debian stamps a
#   cap_net_raw file capability on ping; a NoNewPrivs container refuses to
#   exec a file-capability binary, so the capability and the setuid bit are
#   stripped and ping uses unprivileged ICMP sockets.
# - ION writes ion.log and its transaction log into the working directory,
#   and the consuming profile mounts a writable volume at /run/ion over a
#   read-only root filesystem; the image's working directory is that path,
#   so a terminal opened into the node starts where ION can write.
# - The Debian base is pinned by digest, the same one images/base uses, so a
#   rebuild starts from the same packages.

ARG DEBIAN_IMAGE=debian:bookworm-slim@sha256:f9c6a2fd2ddbc23e336b6257a5245e31f996953ef06cd13a59fa0a1df2d5c252

FROM ${DEBIAN_IMAGE} AS builder
ARG ION_TAG=ion-open-source-4.2.0
ARG ION_COMMIT=568df887cb9f18aa8ec013b1a566d83215e499ae
ARG CORES=8

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential autoconf automake libtool pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${ION_TAG}" https://github.com/nasa-jpl/ION-DTN.git /opt/ion-src \
    && test "$(git -C /opt/ion-src rev-parse HEAD)" = "${ION_COMMIT}"

WORKDIR /opt/ion-src
RUN autoreconf -fi \
    && ./configure --prefix=/opt/ion \
    && make -j "${CORES}" \
    && make install \
    && install -D -m 0644 license.txt /opt/ion/share/doc/ion/license.txt \
    && install -D -m 0644 third-party-licenses.txt /opt/ion/share/doc/ion/third-party-licenses.txt \
    && install -D -m 0644 tc/jerasure/License.txt /opt/ion/share/doc/ion/jerasure-License.txt \
    && install -D -m 0644 tc/gf-complete/License.txt /opt/ion/share/doc/ion/gf-complete-License.txt \
    && install -D -m 0644 ici/libbloom-master/LICENSE /opt/ion/share/doc/ion/libbloom-LICENSE \
    && rm -rf /opt/ion/include /opt/ion/share/man \
    && find /opt/ion/lib -name '*.a' -delete \
    && find /opt/ion/lib -name '*.la' -delete

FROM ${DEBIAN_IMAGE}
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc ION" \
      org.opencontainers.image.description="NASA JPL ION-DTN 4.2.0 daemons and bundle applications, with the ion-node launcher, for NodalArc emulation nodes" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="AGPL-3.0-only AND BSD-3-Clause AND BSD-2-Clause" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash procps iproute2 iputils-ping iputils-tracepath libcap2-bin \
    && rm -rf /var/lib/apt/lists/* \
    && if getcap /usr/bin/ping | grep -q .; then setcap -r /usr/bin/ping; fi \
    && chmod u-s /usr/bin/ping \
    && test -z "$(getcap /usr/bin/ping)"

COPY --from=builder /opt/ion /opt/ion
RUN echo /opt/ion/lib > /etc/ld.so.conf.d/ion.conf && ldconfig
# ion-node writes a node's ION configuration and contact plan from its
# environment and runs it; see the script's header for the contract.
COPY ion-node /usr/local/bin/ion-node
RUN chmod 0755 /usr/local/bin/ion-node
# A login shell (a node's terminal) resets PATH through /etc/profile, so the
# programs are added there as well as to the image environment.
ENV PATH="/opt/ion/bin:${PATH}"
RUN printf 'export PATH="/opt/ion/bin:$PATH"\n' > /etc/profile.d/ion.sh
WORKDIR /run/ion

# Every installed program must find its shared libraries in this image.
# (Scripts among them, such as ionstart and killm, are skipped.)
RUN elf=$(printf '\177ELF'); for f in /opt/ion/bin/*; do \
        if [ "$(head -c 4 "$f")" = "$elf" ] && ldd "$f" | grep -q "not found"; then \
            echo "missing libraries for $f"; ldd "$f"; exit 1; \
        fi; \
    done

# NodalArc's license, its additional terms and the third-party notices go with
# every image whose label names AGPL-3.0-only (AGPL-3.0 Sections 4 and 6). The
# last instruction, so a change to them rebuilds no other layer.
COPY --from=nodalarc-legal LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/doc/nodalarc/
