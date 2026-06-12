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

from nodalarc.models.events import ReplayAnchor, SchedulingCheckpoint

log = logging.getLogger(__name__)


def encode_retained_scheduling_checkpoint(checkpoint: SchedulingCheckpoint) -> bytes:
    """Encode a SchedulingCheckpoint for retained JetStream storage.

    The gzip wire format is owned HERE, beside its decoder — producers
    (the OME publisher thread) and tests use this instead of re-stating
    the encoding inline.
    """
    return gzip.compress(checkpoint.model_dump_json().encode())


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


def encode_retained_replay_anchor(anchor: ReplayAnchor) -> bytes:
    """Encode a ReplayAnchor for retained JetStream storage.

    Same wire convention as the checkpoint: gzip of the model JSON,
    owned beside its decoder.
    """
    return gzip.compress(anchor.model_dump_json().encode())


def decode_retained_replay_anchor(payload: bytes) -> ReplayAnchor | None:
    """Decode a retained replay anchor; incompatible schemas decode to None.

    None means full replay from step zero — slower, never wrong. Corrupt
    gzip still raises: damaged transport is not a schema break.
    """
    decompressed = gzip.decompress(payload)
    try:
        return ReplayAnchor.model_validate_json(decompressed)
    except ValidationError as exc:
        log.warning(
            "Ignoring incompatible retained ReplayAnchor schema; "
            "recovery will replay from step zero: %s",
            exc,
        )
        return None
