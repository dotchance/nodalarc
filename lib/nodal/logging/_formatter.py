# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Formatters for the Nodal logging library.

Three formatters, one underlying record. Each reads the nodal_* fields
set by NodalFilter. Add a new output format by writing another
Formatter subclass.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_LEVEL_SHORT: dict[str, str] = {
    "DEBUG": "DEBUG",
    "INFO": " INFO",
    "WARNING": " WARN",
    "ERROR": "ERROR",
    "CRITICAL": " CRIT",
}


class HumanFormatter(logging.Formatter):
    """Human-readable format for terminal and kubectl output.

    Format: {iso_utc_ms}  {level:5} {logger} — {message}
    """

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, UTC)
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
        level = _LEVEL_SHORT.get(record.levelname, record.levelname[:5].rjust(5))
        msg = record.getMessage()

        line = f"{ts} {level} {record.name} — {msg}"

        if record.exc_info and record.exc_info[0] is not None:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            line = line + "\n" + record.exc_text

        if record.stack_info:
            line = line + "\n" + self.formatStack(record.stack_info)

        return line


class JsonFormatter(logging.Formatter):
    """Structured JSON output — one object per line (JSONL/ndjson).

    Every field from the enriched record on every line. Machine-parseable,
    grep-friendly. Compatible with Loki, Splunk, ELK, Datadog.
    """

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": getattr(record, "nodal_ts", ""),
            "level": getattr(record, "nodal_level", record.levelname.lower()),
            "service": getattr(record, "nodal_service", ""),
            "logger": record.name,
            "tenant": getattr(record, "nodal_tenant", ""),
            "session": getattr(record, "nodal_session", ""),
            "host": getattr(record, "nodal_host", ""),
            "code": getattr(record, "nodal_code", ""),
            "msg": record.getMessage(),
        }

        details = getattr(record, "nodal_details", None)
        if details is not None:
            obj["details"] = details

        if record.exc_info and record.exc_info[0] is not None:
            obj["exc"] = self.formatException(record.exc_info)

        return json.dumps(obj, default=str, ensure_ascii=False)


class OpsEventFormatter(logging.Formatter):
    """Formats LogRecords as OpsEvent-compatible JSON.

    Internal to NatsHandler. Produces JSON matching the OpsEvent
    Pydantic model schema in lib/nodalarc/models/events.py.
    """

    def format(self, record: logging.LogRecord) -> str:
        details = getattr(record, "nodal_details", None)

        if record.exc_info and record.exc_info[0] is not None:
            tb = self.formatException(record.exc_info)
            details = {**details, "traceback": tb} if details is not None else {"traceback": tb}

        obj: dict = {
            "timestamp": getattr(record, "nodal_ts", ""),
            "session_id": getattr(record, "nodal_session", ""),
            "source": getattr(record, "nodal_source", ""),
            "hostname": getattr(record, "nodal_host", ""),
            "level": getattr(record, "nodal_level", record.levelname.lower()),
            "code": getattr(record, "nodal_code", ""),
            "message": record.getMessage(),
            "details": details,
        }

        tenant = getattr(record, "nodal_tenant", "")
        if tenant:
            obj["tenant_id"] = tenant

        return json.dumps(obj, default=str, ensure_ascii=False)
