"""Operator application adapter for the shared exact CR runtime boundary."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nodalarc.cr_runtime_config import RuntimeSessionConfig, load_cr_runtime_config
from nodalarc.kubernetes_runtime_config import ConfigMapReader
from nodalarc.runtime_service_config import DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT

OperatorSessionConfig = RuntimeSessionConfig


def resolve_operator_session(
    spec: Mapping[str, Any],
    *,
    core_v1: ConfigMapReader,
    namespace: str,
    source_origin: str,
    run_id: str | None = None,
    installed_shipped_root: str | Path = DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT,
    materialization_parent: str | Path | None = None,
) -> OperatorSessionConfig:
    """Return the shared exact-root result under an Operator-named seam."""
    return load_cr_runtime_config(
        spec,
        core_v1=core_v1,
        namespace=namespace,
        source_origin=source_origin,
        run_id=run_id,
        installed_shipped_root=installed_shipped_root,
        materialization_parent=materialization_parent,
    )
