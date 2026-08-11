# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload path stays provider-blind: no technology names in core.

The plan, admission, adapter contract, preparation, composition, and
materializer layers translate admitted profiles; the adapter modules are the
only technology-aware code. A provider name appearing in these files is a
hardwire regression, failed at build time.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROVIDER_NAMES = (
    "frr",
    "junos",
    "crpd",
    "xrd",
    "ceos",
    "cisco",
    "juniper",
    "arista",
    "picoquic",
    "ud3tn",
    "nodalpath",
)

PROVIDER_BLIND_FILES = (
    "lib/nodalarc/workloads/plan.py",
    "lib/nodalarc/workloads/admission.py",
    "lib/nodalarc/workloads/adapter.py",
    "services/nodalarc_operator/workloads/preparation.py",
    "services/nodalarc_operator/workloads/compose.py",
    "services/nodalarc_operator/workloads/materializer.py",
)


def test_the_provider_blind_layers_name_no_technology() -> None:
    for relative in PROVIDER_BLIND_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        for name in PROVIDER_NAMES:
            assert not re.search(rf"\b{name}\b", text), (
                f"{relative} names provider {name!r}; adapters are the only "
                "technology-aware modules"
            )
