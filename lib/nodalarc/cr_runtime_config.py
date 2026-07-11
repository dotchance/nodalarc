"""Shared CR boundary for one exact root session and catalog upload."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.kubernetes_runtime_config import ConfigMapReader, load_kubernetes_runtime_config
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import SessionResolution
from nodalarc.runtime_config import RuntimeConfigProof
from nodalarc.runtime_service_config import DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT


@dataclass(frozen=True, slots=True)
class RuntimeSessionConfig:
    """Verified CR input safe to reuse throughout one application operation."""

    resolution: SessionResolution
    proof: RuntimeConfigProof
    root_yaml: str
    catalog_upload: CatalogUploadSelection


def load_cr_runtime_config(
    spec: Mapping[str, Any],
    *,
    core_v1: ConfigMapReader,
    namespace: str,
    source_origin: str,
    run_id: str | None = None,
    installed_shipped_root: str | Path = DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT,
    materialization_parent: str | Path | None = None,
) -> RuntimeSessionConfig:
    """Verify, materialize, and resolve a CR's selected upload once."""
    if not isinstance(spec, Mapping):
        raise TypeError("runtime session spec must be a mapping")
    unexpected_fields = sorted(set(spec).difference({"sessionYaml", "catalogUpload"}))
    if unexpected_fields:
        raise ValueError(
            "runtime session spec contains unsupported field(s): " + ", ".join(unexpected_fields)
        )
    root_yaml = spec.get("sessionYaml")
    if not isinstance(root_yaml, str) or not root_yaml:
        raise ValueError("spec.sessionYaml must be a non-empty string")
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("runtime namespace must be a non-empty string")
    if not isinstance(source_origin, str) or not source_origin.strip():
        raise ValueError("runtime source_origin must be a non-empty string")

    if "catalogUpload" not in spec:
        raise ValueError("spec.catalogUpload is required")
    selection = CatalogUploadSelection.model_validate(spec["catalogUpload"], strict=True)
    if core_v1 is None:
        raise ValueError("core_v1 is required")

    root_yaml_bytes = root_yaml.encode("utf-8")
    parent = Path(materialization_parent) if materialization_parent is not None else None
    with tempfile.TemporaryDirectory(prefix="nodalarc-cr-runtime-", dir=parent) as temporary:
        destination = Path(temporary) / "runtime"
        source_context = SourceContext(origin=source_origin, run_id=run_id)
        runtime_config = load_kubernetes_runtime_config(
            core_v1,
            namespace=namespace,
            root_yaml=root_yaml_bytes,
            selection=selection,
            destination=destination,
            installed_shipped_root=installed_shipped_root,
            source_context=source_context,
        )
        if runtime_config.session_path.read_bytes() != root_yaml_bytes:
            raise RuntimeError("materialized runtime root differs from spec.sessionYaml")
        return RuntimeSessionConfig(
            resolution=runtime_config.resolution,
            proof=runtime_config.proof,
            root_yaml=root_yaml,
            catalog_upload=selection,
        )
