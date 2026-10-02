# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for na-reconfig's resolved-session probe-flow operations."""

from __future__ import annotations

import sys

import pytest

from tools import na_reconfig


def test_cli_offers_no_node_configuration_push(monkeypatch, capsys) -> None:
    """Node configuration reaches a pod only through its workload adapter
    and the artifact ConfigMap delivered at session start."""
    monkeypatch.setattr(
        sys, "argv", ["na_reconfig", "--session", "session.yaml", "--target", "all"]
    )

    with pytest.raises(SystemExit) as exit_info:
        na_reconfig.main()

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --target all" in capsys.readouterr().err
    assert not hasattr(na_reconfig, "reconfig")
