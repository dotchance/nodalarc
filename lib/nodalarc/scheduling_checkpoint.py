# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""SchedulingCheckpoint wire decoding helpers.

Retained checkpoints are process-recovery hints, not user configuration. A
checkpoint written by an older schema is not trustworthy enough to recover MBB
teardown state. During active development we explicitly discard incompatible
retained checkpoints and start a new lineage instead of crashing on deploy or
inventing missing fields.
"""

from __future__ import annotations

import gzip
import logging

from pydantic import ValidationError

from nodalarc.models.events import SchedulingCheckpoint

log = logging.getLogger(__name__)


def decode_retained_scheduling_checkpoint(payload: bytes) -> SchedulingCheckpoint | None:
    """Decode a retained gzip-compressed SchedulingCheckpoint.

    Returns ``None`` only for schema-incompatible retained checkpoints. Corrupt
    gzip payloads still raise because they indicate damaged transport/storage,
    not an intentional schema break.
    """
    decompressed = gzip.decompress(payload)
    try:
        return SchedulingCheckpoint.model_validate_json(decompressed)
    except ValidationError as exc:
        log.warning(
            "Ignoring incompatible retained SchedulingCheckpoint schema; "
            "starting a new checkpoint lineage: %s",
            exc,
        )
        return None
