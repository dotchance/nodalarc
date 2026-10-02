# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""The workload plan envelope: identity and rendered-file containment."""

from __future__ import annotations

import pytest
from nodalarc.workloads.plan import WorkloadPlan, validate_rendered_file_name

PROFILE_REF = "nodalarc:profiles/frr-router.yaml"


def test_plan_requires_a_node_and_a_profile_reference() -> None:
    with pytest.raises(ValueError, match="node_id must be non-empty"):
        WorkloadPlan(node_id="", profile_ref=PROFILE_REF)

    with pytest.raises(ValueError, match="profile reference"):
        WorkloadPlan(node_id="n1", profile_ref="nodalarc:nodes/space/leo-sat.yaml")


def test_rendered_files_are_flat_contained_names_with_byte_content() -> None:
    with pytest.raises(ValueError, match="flat, contained"):
        WorkloadPlan(node_id="n1", profile_ref=PROFILE_REF, rendered_files={"a/b": b"x"})

    with pytest.raises(ValueError, match="dot segment"):
        WorkloadPlan(node_id="n1", profile_ref=PROFILE_REF, rendered_files={"..": b"x"})

    with pytest.raises(TypeError, match="must be bytes"):
        WorkloadPlan(node_id="n1", profile_ref=PROFILE_REF, rendered_files={"a": "text"})

    assert validate_rendered_file_name("frr.conf") == "frr.conf"
