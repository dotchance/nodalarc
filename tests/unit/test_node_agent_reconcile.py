"""The cleaner's entry point, run as the teardown runs it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from node_agent import reconcile

ROOT = Path(__file__).resolve().parents[2]


def test_module_entry_point_runs_as_a_subprocess_with_the_repository_paths() -> None:
    """The workstation invocation the teardown uses resolves the module through
    explicit lib and services paths; the usage path proves the entry without
    touching any kernel state."""
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'lib'}:{ROOT / 'services'}"}
    result = subprocess.run(
        [sys.executable, "-m", "node_agent.reconcile", "--purge"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert reconcile.USAGE in result.stderr
    assert result.stdout == ""
