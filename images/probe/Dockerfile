FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033
ARG PROJECT_VERSION=0+unknown
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="NodalArc Probe" \
      org.opencontainers.image.description="NodalArc probe daemon image for measurement traffic" \
      org.opencontainers.image.vendor=".chance (dotchance)" \
      org.opencontainers.image.authors=".chance (dotchance)" \
      org.opencontainers.image.source="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.url="https://nodal.asmolab.net" \
      org.opencontainers.image.documentation="https://github.com/dotchance/nodalarc" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${PROJECT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"
ENV NODALARC_VERSION=${PROJECT_VERSION} \
    NODALARC_BUILD_REVISION=${VCS_REF} \
    NODALARC_BUILD_DATE=${BUILD_DATE}

RUN pip install --no-cache-dir fastapi uvicorn

WORKDIR /app
COPY services/measurement/probe_daemon.py /app/measurement/probe_daemon.py
RUN touch /app/measurement/__init__.py

ENV PYTHONPATH=/app

EXPOSE 9100
EXPOSE 19100/udp

CMD ["uvicorn", "measurement.probe_daemon:app", "--host", "0.0.0.0", "--port", "9100"]
