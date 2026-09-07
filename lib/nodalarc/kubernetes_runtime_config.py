"""The ConfigMap codec for ordinary-file catalog uploads, and the runtime reader over it.

One upload is one ConfigMap per catalog file: the upload label, the catalog
ref annotation and the document key. VS-API encodes and persists these;
every runtime service decodes them here under one rule.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Mapping
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
)
from nodalarc.content_identity import canonical_json_bytes, sha256_digest
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
    INVALID_UPLOAD = "kubernetes_runtime_config.invalid_upload"
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


def _require_namespace(namespace: object) -> str:
    if not isinstance(namespace, str) or not namespace.strip():
        raise TypeError("namespace must be a non-empty string")
    return namespace


def listed_config_maps(response: Any, *, namespace: str) -> tuple[Any, ...]:
    """The items of one ConfigMap list response."""
    items = _field(response, "items")
    if not isinstance(items, (list, tuple)):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap list response in {namespace} has no items",
            namespace=namespace,
        )
    return tuple(items)


@dataclass(frozen=True, slots=True)
class ConfigMapMetadataIdentity:
    """What the API server names a persisted ConfigMap: name, namespace and uid.

    This is the identity a store registers the moment a create returns, before
    any content rule is applied, so that every persisted resource can be
    cleaned up whatever else is wrong with it.
    """

    name: str
    namespace: str
    uid: str


@dataclass(frozen=True, slots=True)
class CatalogUploadConfigMapIdentity:
    """Metadata identity of one upload ConfigMap plus the upload it belongs to."""

    name: str
    namespace: str
    uid: str
    upload_id: str


@dataclass(frozen=True, slots=True)
class CatalogUploadConfigMap:
    """One persisted upload ConfigMap decoded to the catalog file it carries."""

    name: str
    uid: str
    entry: CatalogClosureEntry


def catalog_upload_config_map_name(upload_id: str, order: int) -> str:
    """The name of the ConfigMap carrying one upload's file at one position."""
    suffix = f"-{order:06d}"
    prefix = upload_id[: 63 - len(suffix)].rstrip("-")
    return prefix + suffix


def encode_catalog_upload_config_map(
    *,
    namespace: str,
    upload_id: str,
    order: int,
    entry: CatalogClosureEntry,
) -> dict[str, Any]:
    """The creation body for one catalog file.

    A creation body carries no uid; the API server assigns one, and only the
    persisted resource decodes.
    """
    try:
        document = entry.yaml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_UPLOAD,
            f"Catalog file {entry.ref} is not UTF-8 YAML",
            namespace=namespace,
            cause=exc,
        ) from exc
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": catalog_upload_config_map_name(upload_id, order),
            "namespace": namespace,
            "labels": {CATALOG_UPLOAD_LABEL: upload_id},
            "annotations": {CATALOG_REF_ANNOTATION: str(entry.ref)},
        },
        "data": {CATALOG_DOCUMENT_KEY: document},
    }


def config_map_metadata_identity(value: Any) -> ConfigMapMetadataIdentity:
    """Name, namespace and uid of one persisted ConfigMap."""
    metadata = _field(value, "metadata")
    name = _field(metadata, "name")
    namespace = _field(metadata, "namespace")
    uid = _field(metadata, "uid")
    if not all(isinstance(item, str) and item for item in (name, namespace, uid)):
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {name!r} lacks a name, namespace or uid",
            namespace=namespace if isinstance(namespace, str) else None,
            config_map_name=name if isinstance(name, str) else None,
        )
    return ConfigMapMetadataIdentity(name=name, namespace=namespace, uid=uid)


def catalog_upload_config_map_identity(value: Any) -> CatalogUploadConfigMapIdentity:
    """Metadata identity plus the upload id one persisted upload ConfigMap is labelled with."""
    identity = config_map_metadata_identity(value)
    labels = _string_mapping(
        _field(_field(value, "metadata"), "labels"),
        field_name="metadata.labels",
        namespace=identity.namespace,
        name=identity.name,
    )
    upload_id = labels.get(CATALOG_UPLOAD_LABEL)
    if not upload_id:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {identity.namespace}/{identity.name} carries no "
            f"{CATALOG_UPLOAD_LABEL} label",
            namespace=identity.namespace,
            config_map_name=identity.name,
        )
    return CatalogUploadConfigMapIdentity(
        name=identity.name, namespace=identity.namespace, uid=identity.uid, upload_id=upload_id
    )


def decode_catalog_upload_config_map(
    value: Any,
    *,
    namespace: str,
    upload_id: str,
) -> CatalogUploadConfigMap:
    """Decode one persisted upload ConfigMap under the rule every runtime applies."""
    identity = catalog_upload_config_map_identity(value)
    name = identity.name
    if identity.namespace != namespace:
        raise _error(
            KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
            f"Catalog upload ConfigMap {name} is not in namespace {namespace}",
            namespace=namespace,
            config_map_name=name,
            expected=namespace,
            observed=identity.namespace,
        )
    api_version = _field(value, "api_version", "apiVersion")
    kind = _field(value, "kind")
    if api_version not in (None, "v1") or kind not in (None, "ConfigMap"):
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
    metadata = _field(value, "metadata")
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
    if labels != {CATALOG_UPLOAD_LABEL: upload_id}:
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
    return CatalogUploadConfigMap(
        name=name,
        uid=identity.uid,
        entry=CatalogClosureEntry(
            ref=ref,
            family=cast(CatalogFamily, ref.family),
            preserved_path=preserved_catalog_path(ref),
            yaml_bytes=content,
            document_digest=sha256_digest(content),
            size_bytes=len(content),
        ),
    )


def _validated_selection_inputs(
    namespace: object, root_yaml: object, selection: object
) -> tuple[str, bytes, CatalogUploadSelection]:
    _require_namespace(namespace)
    if not isinstance(root_yaml, bytes):
        raise TypeError("root_yaml must be bytes")
    if not isinstance(selection, CatalogUploadSelection):
        raise TypeError("selection must be a CatalogUploadSelection")
    return cast(str, namespace), root_yaml, selection


def decode_catalog_upload_config_maps(
    items: Iterable[Any],
    *,
    namespace: str,
    root_yaml: bytes,
    selection: CatalogUploadSelection,
) -> tuple[CatalogUpload, tuple[CatalogUploadConfigMap, ...]]:
    """Decode the listed ConfigMaps of one selection into the upload and its resources.

    Duplicate names and duplicate refs are refused; resources come back sorted
    by ref, the order the upload's files carry.
    """
    namespace, root_yaml, selection = _validated_selection_inputs(namespace, root_yaml, selection)
    resources: dict[CatalogRef, CatalogUploadConfigMap] = {}
    names: set[str] = set()
    for item in items:
        resource = decode_catalog_upload_config_map(
            item,
            namespace=namespace,
            upload_id=selection.upload_id,
        )
        if resource.name in names:
            raise _error(
                KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
                f"Catalog upload {selection.upload_id} contains duplicate ConfigMap name "
                f"{resource.name}",
                namespace=namespace,
                config_map_name=resource.name,
            )
        if resource.entry.ref in resources:
            raise _error(
                KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP,
                f"Catalog upload {selection.upload_id} contains duplicate catalog ref "
                f"{resource.entry.ref}",
                namespace=namespace,
                config_map_name=resource.name,
            )
        names.add(resource.name)
        resources[resource.entry.ref] = resource
    ordered = tuple(resources[ref] for ref in sorted(resources, key=str))
    upload = CatalogUpload(
        selection=selection,
        root_yaml=root_yaml,
        catalog_files=tuple(resource.entry for resource in ordered),
    )
    return upload, ordered


def read_catalog_upload(
    client: ConfigMapReader,
    *,
    namespace: str,
    root_yaml: bytes,
    selection: CatalogUploadSelection,
) -> CatalogUpload:
    """Fetch one selected upload with exactly one label-list request."""
    namespace, root_yaml, selection = _validated_selection_inputs(namespace, root_yaml, selection)
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
    upload, _resources = decode_catalog_upload_config_maps(
        listed_config_maps(response, namespace=namespace),
        namespace=namespace,
        root_yaml=root_yaml,
        selection=selection,
    )
    return upload


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
