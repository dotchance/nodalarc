"""The ConstellationSpec contract: coordinates, the spec, and the CR runtime loader."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nodalarc.catalog_upload import CatalogUploadSelection
from nodalarc.kubernetes_runtime_config import ConfigMapReader, load_kubernetes_runtime_config
from nodalarc.models.resolved_session import SourceContext
from nodalarc.runtime_config import ResolvedRuntimeConfig
from nodalarc.runtime_service_config import DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT

CR_GROUP = "nodalarc.io"
CR_VERSION = "v1alpha1"
CR_API_VERSION = f"{CR_GROUP}/{CR_VERSION}"
CR_KIND = "ConstellationSpec"
CR_PLURAL = "constellationspecs"
CR_NAME = "current-session"


class ConstellationSpecSpec(BaseModel):
    """The one shape of ``spec`` every writer emits and every reader accepts.

    The wire keys are the CR aliases and nothing else: a spec spelled with the
    Python field names is refused like any other unknown key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    session_yaml: str = Field(alias="sessionYaml", min_length=1)
    catalog_upload: CatalogUploadSelection = Field(alias="catalogUpload")

    @classmethod
    def of(
        cls, *, session_yaml: str, catalog_upload: CatalogUploadSelection
    ) -> ConstellationSpecSpec:
        """Build the spec a writer will emit."""
        return cls.model_validate(
            {"sessionYaml": session_yaml, "catalogUpload": catalog_upload}, strict=True
        )

    @classmethod
    def from_cr(cls, spec: Mapping[str, Any]) -> ConstellationSpecSpec:
        """Validate the ``spec`` mapping of one ConstellationSpec."""
        return cls.model_validate(spec, strict=True)

    def to_cr(self) -> dict[str, Any]:
        """The ``spec`` mapping to write into one ConstellationSpec."""
        return self.model_dump(mode="json", by_alias=True)


def load_cr_runtime_config(
    spec: Mapping[str, Any],
    *,
    core_v1: ConfigMapReader,
    namespace: str,
    source_origin: str,
    run_id: str | None = None,
    installed_shipped_root: str | Path = DEFAULT_INSTALLED_SHIPPED_CATALOG_ROOT,
    materialization_parent: str | Path | None = None,
) -> ResolvedRuntimeConfig:
    """Verify, materialize, and resolve a CR's selected upload once.

    The materialized files live only for the resolution; what returns carries
    no path into them.
    """
    if not isinstance(spec, Mapping):
        raise TypeError("runtime session spec must be a mapping")
    parsed = ConstellationSpecSpec.from_cr(spec)
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("runtime namespace must be a non-empty string")
    if not isinstance(source_origin, str) or not source_origin.strip():
        raise ValueError("runtime source_origin must be a non-empty string")
    if core_v1 is None:
        raise ValueError("core_v1 is required")

    root_yaml_bytes = parsed.session_yaml.encode("utf-8")
    parent = Path(materialization_parent) if materialization_parent is not None else None
    with tempfile.TemporaryDirectory(prefix="nodalarc-cr-runtime-", dir=parent) as temporary:
        destination = Path(temporary) / "runtime"
        source_context = SourceContext(origin=source_origin, run_id=run_id)
        materialized = load_kubernetes_runtime_config(
            core_v1,
            namespace=namespace,
            root_yaml=root_yaml_bytes,
            selection=parsed.catalog_upload,
            destination=destination,
            installed_shipped_root=installed_shipped_root,
            source_context=source_context,
        )
        if materialized.session_path.read_bytes() != root_yaml_bytes:
            raise RuntimeError("materialized runtime root differs from spec.sessionYaml")
        return materialized.config
