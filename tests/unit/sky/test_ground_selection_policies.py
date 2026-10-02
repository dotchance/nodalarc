# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for pure ground selection policy hooks."""

from typing import get_args

from nodalarc.models.ground_policy import SELECTION_POLICY_SCORE_SCALES, SelectionPolicyName
from ome.ground_selection_policies import SCORE_FUNCTIONS


def test_selection_dispatch_tables_match_policy_literal() -> None:
    expected = set(get_args(SelectionPolicyName))

    assert set(SCORE_FUNCTIONS) == expected
    assert set(SELECTION_POLICY_SCORE_SCALES) == expected
