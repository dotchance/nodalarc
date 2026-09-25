"""The ConstellationSpec contract: coordinates, the spec, and the CR runtime loader."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, get_args

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

# Provenance annotations VS-API writes on every ConstellationSpec it creates.
SOURCE_KIND_ANNOTATION = "nodalarc.io/source-kind"
SOURCE_ID_ANNOTATION = "nodalarc.io/source-id"
SOURCE_REVISION_ANNOTATION = "nodalarc.io/source-revision"
DOCUMENT_DIGEST_ANNOTATION = "nodalarc.io/document-digest"
CLOSURE_DIGEST_ANNOTATION = "nodalarc.io/closure-digest"
CATALOG_GENERATION_ANNOTATION = "nodalarc.io/catalog-generation"


class ConstellationSpecSpec(BaseModel):
    """The one shape of ``spec`` every writer emits and every reader accepts.

    The wire keys are the CR aliases and nothing else: a spec spelled with the
    Python field names is refused like any other unknown key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    session_yaml: str = Field(alias="sessionYaml", min_length=1)
    catalog_upload: CatalogUploadSelection = Field(alias="catalogUpload")
    # Whether VS-API keeps this session's history database, chosen per deploy.
    record_history: bool = Field(alias="recordHistory")

    @classmethod
    def of(
        cls,
        *,
        session_yaml: str,
        catalog_upload: CatalogUploadSelection,
        record_history: bool,
    ) -> ConstellationSpecSpec:
        """Build the spec a writer will emit."""
        return cls.model_validate(
            {
                "sessionYaml": session_yaml,
                "catalogUpload": catalog_upload,
                "recordHistory": record_history,
            },
            strict=True,
        )

    @classmethod
    def from_cr(cls, spec: Mapping[str, Any]) -> ConstellationSpecSpec:
        """Validate the ``spec`` mapping of one ConstellationSpec."""
        return cls.model_validate(spec, strict=True)

    def to_cr(self) -> dict[str, Any]:
        """The ``spec`` mapping to write into one ConstellationSpec."""
        return self.model_dump(mode="json", by_alias=True)


# The phases the CRD declares (deploy/helm/crds/constellationspec.yaml); a unit
# test keeps this literal and the CRD enum equal.
ConstellationSpecPhase = Literal[
    "Pending", "Rendering", "Creating", "Wiring", "Ready", "Error", "Terminating"
]
CONSTELLATION_SPEC_PHASES: tuple[str, ...] = get_args(ConstellationSpecPhase)


class ConstellationSpecStatus(BaseModel):
    """The one shape of ``status`` the Operator writes and every reader parses.

    Every field is optional because the Operator writes status as JSON merge
    patches: a patch carries only the fields it sets, and a CR observed early
    in a reconcile, or before any reconcile, legitimately carries few or none.
    The keys, the types and the phase vocabulary are the CRD's, no stricter:
    what the API server stores, this parses. Readers normalize empty strings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    phase: ConstellationSpecPhase | None = None
    message: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    session_name: str | None = Field(default=None, alias="sessionName")
    session_run_id: str | None = Field(default=None, alias="sessionRunId")
    pod_count: int | None = Field(default=None, alias="podCount")
    ready_pods: int | None = Field(default=None, alias="readyPods")
    wired_pods: int | None = Field(default=None, alias="wiredPods")
    platform_hash: str | None = Field(default=None, alias="platformHash")
    runtime_hash: str | None = Field(default=None, alias="runtimeHash")
    document_digest: str | None = Field(default=None, alias="documentDigest")
    closure_digest: str | None = Field(default=None, alias="closureDigest")
    resolved_semantic_digest: str | None = Field(default=None, alias="resolvedSemanticDigest")
    runtime_release: str | None = Field(default=None, alias="runtimeRelease")
    runtime_build: str | None = Field(default=None, alias="runtimeBuild")
    last_transition_time: str | None = Field(default=None, alias="lastTransitionTime")
    observed_generation: int | None = Field(default=None, alias="observedGeneration")

    @classmethod
    def from_cr(cls, status: Mapping[str, Any] | None) -> ConstellationSpecStatus:
        """Validate the ``status`` mapping of one ConstellationSpec; absent is empty."""
        return cls.model_validate(dict(status or {}), strict=True)

    def to_patch(self) -> dict[str, Any]:
        """The merge patch this status expresses: only the fields it set."""
        return self.model_dump(mode="json", by_alias=True, exclude_unset=True)

    def observes_generation(self, generation: object) -> bool:
        """Whether this status was computed from the given positive CR generation."""
        if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
            return False
        return self.observed_generation == generation

    def ready_pod_count(self) -> int | None:
        """The pod count when every pod is ready and wired, else ``None``.

        Readiness needs a positive pod count with the ready and wired counts
        equal to it; an absent or differing count means not ready.
        """
        count = self.pod_count
        if count is None or count <= 0:
            return None
        if self.ready_pods != count or self.wired_pods != count:
            return None
        return count

    def carries(self, intended: ConstellationSpecStatus) -> bool:
        """Whether every field the intended status sets holds the same value here."""
        return all(
            getattr(self, name) == getattr(intended, name) for name in intended.model_fields_set
        )

    def runtime_mismatches(
        self,
        *,
        document_digest: str,
        closure_digest: str,
        resolved_semantic_digest: str,
        release: str,
        build: str,
    ) -> tuple[str, ...]:
        """CR keys of the runtime proof fields that differ from the given values."""
        pairs = (
            ("documentDigest", self.document_digest, document_digest),
            ("closureDigest", self.closure_digest, closure_digest),
            ("resolvedSemanticDigest", self.resolved_semantic_digest, resolved_semantic_digest),
            ("runtimeRelease", self.runtime_release, release),
            ("runtimeBuild", self.runtime_build, build),
        )
        return tuple(key for key, observed, expected in pairs if observed != expected)


def cr_status_observes_current_generation(cr: Mapping[str, Any]) -> bool:
    """Whether a CR's status was computed from the CR's current generation."""
    metadata = cr.get("metadata") or {}
    status = ConstellationSpecStatus.from_cr(cr.get("status"))
    return status.observes_generation(metadata.get("generation"))


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
