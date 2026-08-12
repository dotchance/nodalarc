# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Neutral content-identity helpers: digest spelling and canonical bytes.

One small module owns the sha256 digest form and the canonical JSON encoding
used for operational evidence, so identity consumers never depend on the
transport layer and the transport layer never depends on its consumers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import StringConstraints

SHA256_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
BARE_SHA256_PATTERN = r"^[0-9a-f]{64}$"

Sha256Digest = Annotated[str, StringConstraints(pattern=SHA256_DIGEST_PATTERN)]


def sha256_digest(content: bytes) -> str:
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable JSON for operational evidence outside the YAML file set."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
