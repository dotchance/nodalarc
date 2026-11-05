# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The per-buffer memory a server needs from the queue estimate, by page size: what node
qualification compares with network.queueMemory.bytesPerBuffer and names when it refuses."""

from __future__ import annotations

from pathlib import Path

import yaml
from node_agent.qualification import DEFAULT_GSO_GRO_MAX_BYTES, queue_buffer_bytes_needed

SHIPPED_BYTES_PER_BUFFER = yaml.safe_load(
    (Path(__file__).parents[3] / "configs" / "platform.yaml").read_text(encoding="utf-8")
)["platform"]["queue_memory_bytes_per_buffer"]


def test_the_shipped_estimate_covers_4_kib_pages_and_refuses_larger_ones():
    needed = {
        page: queue_buffer_bytes_needed(largest_buffer=DEFAULT_GSO_GRO_MAX_BYTES, page_size=page)
        for page in (4_096, 16_384, 65_536)
    }
    assert needed == {4_096: 70_656, 16_384: 82_944, 65_536: 132_096}
    assert needed[4_096] <= SHIPPED_BYTES_PER_BUFFER < needed[16_384]


def test_a_raised_gro_limit_needs_its_whole_buffer():
    # BIG TCP raises the GSO and GRO limit; a buffer of 185,000 bytes spans 46 pages of 4 KiB.
    assert (
        queue_buffer_bytes_needed(largest_buffer=185_000, page_size=4_096)
        == 46 * 4_096 + 4_096 + 1_024
    )
