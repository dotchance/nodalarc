# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""NodalFilter — enriches every LogRecord with structured fields.

Attached to each handler by configure(). Runs once per record (idempotent
guard via hasattr check). Every formatter reads the nodal_* fields.
"""

from __future__ import annotations

import logging
import re
import socket
from datetime import UTC, datetime

_hostname: str = socket.gethostname()

_PREFIX_RE = re.compile(r"^([A-Z][A-Za-z_]+):")

# _PREFIX_RE constrains prefix words to [A-Za-z_] — no digits, no hyphens.
# ID patterns like sat-P00S00, node-04, IPs, UUIDs all contain digits or
# characters outside that class, so they can never pass _PREFIX_RE. The \d
# check is defense-in-depth if _PREFIX_RE is ever loosened.
_DYNAMIC_RE = re.compile(r"\d")

_CAMEL_BOUNDARY_RE = re.compile(r"([a-z\d])([A-Z])")
_CONSECUTIVE_CAPS_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")


def _pascal_to_upper_snake(s: str) -> str:
    s = _CAMEL_BOUNDARY_RE.sub(r"\1_\2", s)
    s = _CONSECUTIVE_CAPS_RE.sub(r"\1_\2", s)
    return s.upper()


class NodalFilter(logging.Filter):
    """Enriches LogRecords with nodal_* structured fields.

    One instance is shared across all handlers. The idempotency guard
    (hasattr check on nodal_ts) ensures enrichment runs exactly once
    per record even when multiple handlers share this filter.
    """

    def __init__(self, service: str, tenant_id: str = "", session_id: str = "") -> None:
        super().__init__()
        self._service = service
        self._source = self._derive_source(service)
        self._tenant_id = tenant_id
        self._session_id = session_id

    @staticmethod
    def _derive_source(service: str) -> str:
        parts = service.rsplit(".", 1)
        return parts[-1] if len(parts) > 1 else service

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "nodal_ts"):
            return True

        record.nodal_ts = datetime.fromtimestamp(record.created, UTC).isoformat()
        record.nodal_service = self._service
        record.nodal_source = self._source
        record.nodal_host = _hostname

        record.nodal_tenant = self._tenant_id
        record.nodal_session = self._session_id

        levelname = record.levelname
        if levelname == "WARNING":
            record.nodal_level = "warning"
        else:
            record.nodal_level = levelname.lower()

        explicit_code = getattr(record, "code", None)
        if explicit_code:
            record.nodal_code = str(explicit_code).upper()
        else:
            record.nodal_code = self._derive_code(record)

        record.nodal_details = getattr(record, "details", None)

        return True

    def _derive_code(self, record: logging.LogRecord) -> str:
        logger_name = record.name.rsplit(".", 1)[-1]
        if logger_name in ("root", "__main__"):
            suffix = self._source.upper()
        else:
            suffix = logger_name.upper()

        try:
            msg = record.getMessage()
        except Exception:
            return suffix

        m = _PREFIX_RE.match(msg)
        if m:
            prefix_word = m.group(1)
            if not _DYNAMIC_RE.search(prefix_word):
                return f"{suffix}_{_pascal_to_upper_snake(prefix_word)}"

        return suffix

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @tenant_id.setter
    def tenant_id(self, value: str) -> None:
        self._tenant_id = value
