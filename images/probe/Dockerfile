FROM python:3.14-slim@sha256:5b3879b6f3cb77e712644d50262d05a7c146b7312d784a18eff7ff5462e77033

RUN pip install --no-cache-dir fastapi uvicorn

WORKDIR /app
COPY services/measurement/probe_daemon.py /app/measurement/probe_daemon.py
RUN touch /app/measurement/__init__.py

ENV PYTHONPATH=/app

EXPOSE 9100
EXPOSE 19100/udp

CMD ["uvicorn", "measurement.probe_daemon:app", "--host", "0.0.0.0", "--port", "9100"]
