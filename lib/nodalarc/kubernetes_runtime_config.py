"""Kubernetes reader for one selected ordinary-file catalog upload."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from nodalarc.catalog_closure import CatalogClosureEntry, preserved_catalog_path
from nodalarc.catalog_refs import CatalogFamily, CatalogRef
from nodalarc.catalog_upload import (
    DEFAULT_CATALOG_UPLOAD_LIMITS,
    CatalogUpload,
    CatalogUploadLimits,
    CatalogUploadSelection,
    canonical_json_bytes,
    sha256_digest,
)
from nodalarc.models.resolved_session import SourceContext
from nodalarc.runtime_config import (
    ResolvedRuntimeConfig,
    RuntimeConfigProof,
    load_runtime_config,
)

CATALOG_UPLOAD_LABEL = "nodalarc.io/catalog-upload"
CATALOG_REF_ANNOTATION = "nodalarc.io/catalog-ref"
CATALOG_DOCUMENT_KEY = "document.yaml"
RUNTIME_CONFIG_PROOF_FILENAME = "runtime-config-proof.json"


class ConfigMapReader(Protocol):
    """One label-list operation used by every runtime upload reader."""

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any: ...


class KubernetesRuntimeConfigErrorCode(StrEnum):
    CONFIG_MAP_FETCH_FAILED = "kubernetes_runtime_config.config_map_fetch_failed"
    INVALID_CONFIG_MAP = "kubernetes_runtime_config.invalid_config_map"
    PROOF_WRITE_FAILED = "kubernetes_runtime_config.proof_write_failed"


@dataclass(frozen=True, slots=True)
class KubernetesRuntimeConfigErrorEvidence:
    code: KubernetesRuntimeConfigErrorCode
    message: str
    namespace: str | None = None
    config_map_name: str | None = None
    expected: str | int | None = None
    observed: str | int | None = None
    cause_type: str | None = None


class KubernetesRuntimeConfigError(ValueError):
    """Typed refusal at the Kubernetes ordinary-file boundary."""

    def __init__(self, evidence: KubernetesRuntimeConfigErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> KubernetesRuntimeConfigErrorCode:
        return self.evidence.code


def _error(
    code: KubernetesRuntimeConfigErrorCode,
    message: str,
    *,
    namespace: str | None = None,
    config_map_name: str | None = None,
    expected: str | int | None = None,
    observed: str | int | None = None,
    cause: BaseException | None = None,
) -> KubernetesRuntimeConfigError:
    return KubernetesRuntimeConfigError(
        KubernetesRuntimeConfigErrorEvidence(
            code=code,
            message=message,
            namespace=namespace,
            config_map_name=config_map_name,
            expected=expected,
            observed=observed,
            cause_type=type(cause).__name__ if cause is not None else None,
        )
    )


def _field(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _string_mapping(
    value: Any,
    *,
    field_name: str,
    namespace: str,
    name: str,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} has invalid {field_name}",
            namespace=namespace,
            config_map_name=name,
        )
    result = dict(value)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in result.items()):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} has non-string {field_name}",
            namespace=namespace,
            config_map_name=name,
        )
    return result


def _items(response: Any, *, namespace: str) -> tuple[Any, ...]:
    items = _field(response, "items")
    if not isinstance(items, (list, tuple)):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap list response in {namespace} has no items",
            namespace=namespace,
        )
    return tuple(items)


def _entry_from_config_map(
    value: Any,
    *,
    namespace: str,
    selection: CatalogUploadSelection,
) -> tuple[str, CatalogClosureEntry]:
    metadata = _field(value, "metadata")
    name = _field(metadata, "name")
    observed_namespace = _field(metadata, "namespace")
    if not isinstance(name, str) or not name:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload inventory in {namespace} contains an unnamed ConfigMap",
            namespace=namespace,
        )
    if observed_namespace != namespace:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {name} is not in namespace {namespace}",
            namespace=namespace,
            config_map_name=name,
            expected=namespace,
            observed=str(observed_namespace),
        )
    if _field(value, "api_version", "apiVersion") != "v1" or _field(value, "kind") != "ConfigMap":
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload object {namespace}/{name} is not a v1 ConfigMap",
            namespace=namespace,
            config_map_name=name,
        )
    if _field(value, "immutable") is True:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} must not be immutable",
            namespace=namespace,
            config_map_name=name,
        )
    if _field(value, "binary_data", "binaryData") not in (None, {}):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} must not use binaryData",
            namespace=namespace,
            config_map_name=name,
        )
    if _field(metadata, "owner_references", "ownerReferences") not in (None, [], ()):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} must not use owner references",
            namespace=namespace,
            config_map_name=name,
        )
    labels = _string_mapping(
        _field(metadata, "labels"),
        field_name="metadata.labels",
        namespace=namespace,
        name=name,
    )
    expected_labels = {CATALOG_UPLOAD_LABEL: selection.upload_id}
    if labels != expected_labels:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} labels do not match its selection",
            namespace=namespace,
            config_map_name=name,
        )
    annotations = _string_mapping(
        _field(metadata, "annotations"),
        field_name="metadata.annotations",
        namespace=namespace,
        name=name,
    )
    if set(annotations) != {CATALOG_REF_ANNOTATION}:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} must contain only "
            "the catalog ref annotation",
            namespace=namespace,
            config_map_name=name,
        )
    try:
        ref = CatalogRef(annotations[CATALOG_REF_ANNOTATION])
    except (TypeError, ValueError) as exc:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} has an invalid catalog ref",
            namespace=namespace,
            config_map_name=name,
            cause=exc,
        ) from exc
    if ref.family is None:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} catalog ref has no family",
            namespace=namespace,
            config_map_name=name,
        )
    data = _string_mapping(
        _field(value, "data"),
        field_name="data",
        namespace=namespace,
        name=name,
    )
    if set(data) != {CATALOG_DOCUMENT_KEY}:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {namespace}/{name} must contain only {CATALOG_DOCUMENT_KEY}",
            namespace=namespace,
            config_map_name=name,
        )
    content = data[CATALOG_DOCUMENT_KEY].encode("utf-8")
    return name, CatalogClosureEntry(
        ref=ref,
        family=cast(CatalogFamily, ref.family),
        preserved_path=preserved_catalog_path(ref),
        yaml_bytes=content,
        document_digest=sha256_digest(content),
        size_bytes=len(content),
    )


def read_catalog_upload(
    client: ConfigMapReader,
    *,
    namespace: str,
    root_yaml: bytes,
    selection: CatalogUploadSelection,
) -> CatalogUpload:
    """Fetch one selected upload with exactly one label-list request."""
    if not isinstance(namespace, str) or not namespace.strip():
        raise TypeError("namespace must be a non-empty string")
    if not isinstance(root_yaml, bytes):
        raise TypeError("root_yaml must be bytes")
    if not isinstance(selection, CatalogUploadSelection):
        raise TypeError("selection must be a CatalogUploadSelection")
    try:
        response = client.list_namespaced_config_map(
            namespace=namespace,
            label_selector=f"{CATALOG_UPLOAD_LABEL}={selection.upload_id}",
        )
    except Exception as exc:
        raise _error(
            KubernetesRuntimeConfigErrorCode.CONFIG_MAP_FETCH_FAILED,
            f"Could not list catalog upload {selection.upload_id} in {namespace}: {exc}",
            namespace=namespace,
            cause=exc,
        ) from exc

    entries: dict[CatalogRef, CatalogClosureEntry] = {}
    names: set[str] = set()
    for item in _items(response, namespace=namespace):
        name, entry = _entry_from_config_map(
            item,
            namespace=namespace,
            selection=selection,
        )
        if name in names:
            raise _error(
                KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
                f"Catalog upload {selection.upload_id} contains duplicate ConfigMap name {name}",
                namespace=namespace,
                config_map_name=name,
            )
        if entry.ref in entries:
            raise _error(
                KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
                f"Catalog upload {selection.upload_id} contains duplicate catalog ref {entry.ref}",
                namespace=namespace,
                config_map_name=name,
            )
        names.add(name)
        entries[entry.ref] = entry
    return CatalogUpload(
        selection=selection,
        root_yaml=root_yaml,
        catalog_files=tuple(entries[ref] for ref in sorted(entries, key=str)),
    )


def write_runtime_config_proof(
    runtime_config: ResolvedRuntimeConfig,
    *,
    destination: str | Path,
) -> Path:
    """Atomically persist the strict runtime proof inside a materialization."""
    destination_path = Path(destination)
    proof_path = destination_path / RUNTIME_CONFIG_PROOF_FILENAME
    temporary_path = destination_path / f".{RUNTIME_CONFIG_PROOF_FILENAME}.tmp"
    content = canonical_json_bytes(runtime_config.proof.model_dump(mode="json"))
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
            file_descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, proof_path)
        observed = proof_path.read_bytes()
        if observed != content:
            raise OSError("runtime proof exact-byte verification failed")
        RuntimeConfigProof.model_validate_json(observed)
    except Exception as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)
        temporary_path.unlink(missing_ok=True)
        raise _error(
            KubernetesRuntimeConfigErrorCode.PROOF_WRITE_FAILED,
            f"Could not persist runtime configuration proof in {destination_path}: {exc}",
            cause=exc,
        ) from exc
    return proof_path


def load_kubernetes_runtime_config(
    client: ConfigMapReader,
    *,
    namespace: str,
    root_yaml: bytes,
    selection: CatalogUploadSelection,
    destination: str | Path,
    installed_shipped_root: str | Path,
    source_context: SourceContext,
    limits: CatalogUploadLimits = DEFAULT_CATALOG_UPLOAD_LIMITS,
) -> ResolvedRuntimeConfig:
    """Fetch once, verify the selected upload, materialize, and resolve once."""
    upload = read_catalog_upload(
        client,
        namespace=namespace,
        root_yaml=root_yaml,
        selection=selection,
    )
    target = Path(destination)
    runtime_config = load_runtime_config(
        upload,
        destination=target,
        installed_shipped_root=installed_shipped_root,
        source_context=source_context,
        limits=limits,
    )
    try:
        write_runtime_config_proof(runtime_config, destination=target)
    except KubernetesRuntimeConfigError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return runtime_config
