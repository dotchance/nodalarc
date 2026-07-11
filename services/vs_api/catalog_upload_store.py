"""Namespaced Kubernetes storage for ordinary catalog YAML files."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from nodalarc.catalog_closure import CatalogClosureEntry, preserved_catalog_path
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_upload import (
    CatalogUpload,
    CatalogUploadSelection,
    sha256_digest,
    verify_catalog_upload,
)

CATALOG_UPLOAD_LABEL = "nodalarc.io/catalog-upload"
CATALOG_REF_ANNOTATION = "nodalarc.io/catalog-ref"
CATALOG_DOCUMENT_KEY = "document.yaml"
DEFAULT_CATALOG_UPLOAD_GC_GRACE = timedelta(minutes=15)
MAX_CATALOG_UPLOAD_GC_GRACE = timedelta(hours=24)


class CoreV1ConfigMapApi(Protocol):
    def create_namespaced_config_map(self, namespace: str, body: Mapping[str, Any]) -> Any: ...

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any: ...

    def delete_namespaced_config_map(
        self,
        name: str,
        namespace: str,
        body: Mapping[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CatalogUploadResourceEvidence:
    name: str
    ref: CatalogRef
    uid: str


@dataclass(frozen=True, slots=True)
class CatalogUploadStoreReceipt:
    selection: CatalogUploadSelection
    resources: tuple[CatalogUploadResourceEvidence, ...]

    def __post_init__(self) -> None:
        if len(self.resources) != self.selection.file_count:
            raise ValueError("catalog upload receipt resource count does not match selection")
        if len({resource.name for resource in self.resources}) != len(self.resources):
            raise ValueError("catalog upload receipt contains duplicate resource names")
        if len({resource.ref for resource in self.resources}) != len(self.resources):
            raise ValueError("catalog upload receipt contains duplicate refs")

    @property
    def created_names(self) -> tuple[str, ...]:
        return tuple(resource.name for resource in self.resources)

    @property
    def kubernetes_uids(self) -> tuple[tuple[str, str], ...]:
        return tuple((resource.name, resource.uid) for resource in self.resources)


@dataclass(frozen=True, slots=True)
class CatalogUploadDeleteReceipt:
    upload_id: str
    deleted_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogUploadGarbageCollectionReceipt:
    active_upload_ids: tuple[str, ...]
    deleted_names: tuple[str, ...]
    active_names: tuple[str, ...]
    grace_names: tuple[str, ...]
    unsafe_names: tuple[str, ...]


class CatalogUploadStoreErrorCode(StrEnum):
    INVALID_UPLOAD = "catalog_upload_store.invalid_upload"
    CREATE_FAILED = "catalog_upload_store.create_failed"
    LIST_FAILED = "catalog_upload_store.list_failed"
    READBACK_MISMATCH = "catalog_upload_store.readback_mismatch"
    DELETE_FAILED = "catalog_upload_store.delete_failed"


@dataclass(frozen=True, slots=True)
class CatalogUploadStoreErrorEvidence:
    code: CatalogUploadStoreErrorCode
    message: str
    upload_id: str | None = None
    resource_name: str | None = None
    kubernetes_status: int | None = None
    created_names: tuple[str, ...] = ()
    cleanup_failures: tuple[str, ...] = ()
    cause_type: str | None = None


class CatalogUploadStoreError(RuntimeError):
    def __init__(self, evidence: CatalogUploadStoreErrorEvidence) -> None:
        super().__init__(evidence.message)
        self.evidence = evidence

    @property
    def code(self) -> CatalogUploadStoreErrorCode:
        return self.evidence.code


def _error(
    code: CatalogUploadStoreErrorCode,
    message: str,
    *,
    upload_id: str | None = None,
    resource_name: str | None = None,
    cause: BaseException | None = None,
    created_names: Collection[str] = (),
    cleanup_failures: Collection[str] = (),
) -> CatalogUploadStoreError:
    status = getattr(cause, "status", None)
    return CatalogUploadStoreError(
        CatalogUploadStoreErrorEvidence(
            code=code,
            message=message,
            upload_id=upload_id,
            resource_name=resource_name,
            kubernetes_status=status if isinstance(status, int) else None,
            created_names=tuple(created_names),
            cleanup_failures=tuple(cleanup_failures),
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


def _string_mapping(value: Any, *, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    result = dict(value)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in result.items()):
        raise ValueError(f"{label} must contain only strings")
    return result


def _resource_name(upload_id: str, order: int) -> str:
    suffix = f"-{order:06d}"
    prefix = upload_id[: 63 - len(suffix)].rstrip("-")
    return prefix + suffix


def _config_map_body(
    *,
    namespace: str,
    upload_id: str,
    order: int,
    entry: CatalogClosureEntry,
) -> dict[str, Any]:
    try:
        document = entry.yaml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(
            CatalogUploadStoreErrorCode.INVALID_UPLOAD,
            f"Catalog file {entry.ref} is not UTF-8 YAML",
            upload_id=upload_id,
            cause=exc,
        ) from exc
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": _resource_name(upload_id, order),
            "namespace": namespace,
            "labels": {CATALOG_UPLOAD_LABEL: upload_id},
            "annotations": {CATALOG_REF_ANNOTATION: str(entry.ref)},
        },
        "data": {CATALOG_DOCUMENT_KEY: document},
    }


def _metadata(value: Any) -> Any:
    return _field(value, "metadata")


def _resource_identity(value: Any) -> tuple[str, str, str]:
    metadata = _metadata(value)
    name = _field(metadata, "name")
    namespace = _field(metadata, "namespace")
    uid = _field(metadata, "uid")
    if not all(isinstance(item, str) and item for item in (name, namespace, uid)):
        raise ValueError("ConfigMap metadata must contain name, namespace, and UID")
    return name, namespace, uid


def _creation_timestamp(value: Any) -> datetime | None:
    timestamp = _field(_metadata(value), "creation_timestamp", "creationTimestamp")
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _entry_from_config_map(
    value: Any, *, namespace: str, upload_id: str
) -> tuple[str, str, CatalogClosureEntry]:
    name, observed_namespace, uid = _resource_identity(value)
    if observed_namespace != namespace:
        raise ValueError(f"ConfigMap {name} is in namespace {observed_namespace!r}")
    if _field(value, "api_version", "apiVersion") != "v1" or _field(value, "kind") != "ConfigMap":
        raise ValueError(f"Object {name} is not a v1 ConfigMap")
    if _field(value, "immutable") is True:
        raise ValueError(f"ConfigMap {name} must not be immutable")
    if _field(value, "binary_data", "binaryData") not in (None, {}):
        raise ValueError(f"ConfigMap {name} must not use binaryData")
    if _field(_metadata(value), "owner_references", "ownerReferences") not in (None, [], ()):
        raise ValueError(f"ConfigMap {name} must not use owner references")
    labels = _string_mapping(_field(_metadata(value), "labels"), label=f"{name} labels")
    if labels.get(CATALOG_UPLOAD_LABEL) != upload_id:
        raise ValueError(f"ConfigMap {name} has the wrong upload label")
    annotations = _string_mapping(
        _field(_metadata(value), "annotations"),
        label=f"{name} annotations",
    )
    if set(annotations) != {CATALOG_REF_ANNOTATION}:
        raise ValueError(f"ConfigMap {name} must contain only the catalog ref annotation")
    ref = CatalogRef(annotations[CATALOG_REF_ANNOTATION])
    if ref.family is None:
        raise ValueError(f"ConfigMap {name} catalog ref has no family")
    data = _string_mapping(_field(value, "data"), label=f"{name} data")
    if set(data) != {CATALOG_DOCUMENT_KEY}:
        raise ValueError(f"ConfigMap {name} must contain only {CATALOG_DOCUMENT_KEY}")
    content = data[CATALOG_DOCUMENT_KEY].encode("utf-8")
    return (
        name,
        uid,
        CatalogClosureEntry(
            ref=ref,
            family=ref.family,
            preserved_path=preserved_catalog_path(ref),
            yaml_bytes=content,
            document_digest=sha256_digest(content),
            size_bytes=len(content),
        ),
    )


def _items(response: Any) -> tuple[Any, ...]:
    items = _field(response, "items")
    if not isinstance(items, (list, tuple)):
        raise ValueError("ConfigMap list response has no items")
    return tuple(items)


class KubernetesCatalogUploadStore:
    """Create-only storage and label-based lifecycle for exact YAML files."""

    def __init__(
        self,
        client: CoreV1ConfigMapApi,
        namespace: str,
        *,
        gc_grace: timedelta = DEFAULT_CATALOG_UPLOAD_GC_GRACE,
    ) -> None:
        if not isinstance(namespace, str) or not namespace.strip():
            raise TypeError("namespace must be a non-empty string")
        if not isinstance(gc_grace, timedelta):
            raise TypeError("gc_grace must be a timedelta")
        if gc_grace < timedelta(0) or gc_grace > MAX_CATALOG_UPLOAD_GC_GRACE:
            raise ValueError("gc_grace is outside the supported range")
        self._client = client
        self._namespace = namespace
        self._gc_grace = gc_grace

    def put(
        self,
        upload: CatalogUpload,
        *,
        resource_observer: Callable[[CatalogUploadResourceEvidence], None] | None = None,
    ) -> CatalogUploadStoreReceipt:
        try:
            verify_catalog_upload(upload)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.INVALID_UPLOAD,
                f"Catalog upload is invalid: {exc}",
                upload_id=getattr(upload, "upload_id", None),
                cause=exc,
            ) from exc

        created: list[CatalogUploadResourceEvidence] = []
        try:
            for order, entry in enumerate(upload.catalog_files):
                body = _config_map_body(
                    namespace=self._namespace,
                    upload_id=upload.upload_id,
                    order=order,
                    entry=entry,
                )
                name = body["metadata"]["name"]
                try:
                    observed = self._client.create_namespaced_config_map(
                        namespace=self._namespace,
                        body=body,
                    )
                    observed_name, observed_namespace, uid = _resource_identity(observed)
                except Exception as exc:
                    raise _error(
                        CatalogUploadStoreErrorCode.CREATE_FAILED,
                        f"Could not create catalog YAML ConfigMap {name}: {exc}",
                        upload_id=upload.upload_id,
                        resource_name=name,
                        cause=exc,
                        created_names=(resource.name for resource in created),
                    ) from exc
                if observed_name != name or observed_namespace != self._namespace:
                    raise _error(
                        CatalogUploadStoreErrorCode.CREATE_FAILED,
                        f"Created ConfigMap identity does not match {self._namespace}/{name}",
                        upload_id=upload.upload_id,
                        resource_name=name,
                        created_names=(resource.name for resource in created),
                    )
                evidence = CatalogUploadResourceEvidence(name=name, ref=entry.ref, uid=uid)
                created.append(evidence)
                if resource_observer is not None:
                    resource_observer(evidence)

            verified, observed_resources = self.read(upload.selection, root_yaml=upload.root_yaml)
            if verified.catalog_files != upload.catalog_files:
                raise _error(
                    CatalogUploadStoreErrorCode.READBACK_MISMATCH,
                    "Catalog upload readback differs from the created YAML files",
                    upload_id=upload.upload_id,
                    created_names=(resource.name for resource in created),
                )
            expected_names = {resource.name for resource in created}
            observed_names = {resource.name for resource in observed_resources}
            if observed_names != expected_names:
                raise _error(
                    CatalogUploadStoreErrorCode.READBACK_MISMATCH,
                    "Catalog upload readback resource names differ from the created set",
                    upload_id=upload.upload_id,
                    created_names=expected_names,
                )
            return CatalogUploadStoreReceipt(
                selection=upload.selection,
                resources=tuple(sorted(observed_resources, key=lambda item: str(item.ref))),
            )
        except Exception as exc:
            cleanup_failures = self._cleanup(resource.name for resource in created)
            if isinstance(exc, CatalogUploadStoreError):
                evidence = exc.evidence
                raise CatalogUploadStoreError(
                    CatalogUploadStoreErrorEvidence(
                        code=evidence.code,
                        message=evidence.message,
                        upload_id=evidence.upload_id,
                        resource_name=evidence.resource_name,
                        kubernetes_status=evidence.kubernetes_status,
                        created_names=tuple(resource.name for resource in created),
                        cleanup_failures=cleanup_failures,
                        cause_type=evidence.cause_type,
                    )
                ) from exc
            raise _error(
                CatalogUploadStoreErrorCode.CREATE_FAILED,
                f"Catalog upload creation failed: {exc}",
                upload_id=upload.upload_id,
                cause=exc,
                created_names=(resource.name for resource in created),
                cleanup_failures=cleanup_failures,
            ) from exc

    def read(
        self,
        selection: CatalogUploadSelection,
        *,
        root_yaml: bytes,
    ) -> tuple[CatalogUpload, tuple[CatalogUploadResourceEvidence, ...]]:
        if not isinstance(selection, CatalogUploadSelection):
            raise TypeError("selection must be a CatalogUploadSelection")
        try:
            response = self._client.list_namespaced_config_map(
                namespace=self._namespace,
                label_selector=f"{CATALOG_UPLOAD_LABEL}={selection.upload_id}",
            )
            observed = _items(response)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload {selection.upload_id}: {exc}",
                upload_id=selection.upload_id,
                cause=exc,
            ) from exc

        entries: dict[CatalogRef, CatalogClosureEntry] = {}
        resources: list[CatalogUploadResourceEvidence] = []
        names: set[str] = set()
        try:
            for item in observed:
                name, uid, entry = _entry_from_config_map(
                    item,
                    namespace=self._namespace,
                    upload_id=selection.upload_id,
                )
                if name in names:
                    raise ValueError(f"duplicate ConfigMap name {name}")
                if entry.ref in entries:
                    raise ValueError(f"duplicate catalog ref {entry.ref}")
                names.add(name)
                entries[entry.ref] = entry
                resources.append(CatalogUploadResourceEvidence(name=name, ref=entry.ref, uid=uid))
            upload = CatalogUpload(
                selection=selection,
                root_yaml=root_yaml,
                catalog_files=tuple(entries[ref] for ref in sorted(entries, key=str)),
            )
            verify_catalog_upload(upload)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.READBACK_MISMATCH,
                f"Catalog upload {selection.upload_id} readback is invalid: {exc}",
                upload_id=selection.upload_id,
                cause=exc,
            ) from exc
        return upload, tuple(sorted(resources, key=lambda item: str(item.ref)))

    def delete(
        self, upload: CatalogUpload | CatalogUploadSelection | str
    ) -> CatalogUploadDeleteReceipt:
        upload_id = (
            upload.upload_id
            if isinstance(upload, CatalogUpload)
            else upload.upload_id
            if isinstance(upload, CatalogUploadSelection)
            else upload
        )
        if not isinstance(upload_id, str) or not upload_id:
            raise TypeError("upload must identify a non-empty upload ID")
        try:
            response = self._client.list_namespaced_config_map(
                namespace=self._namespace,
                label_selector=f"{CATALOG_UPLOAD_LABEL}={upload_id}",
            )
            items = _items(response)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload {upload_id}: {exc}",
                upload_id=upload_id,
                cause=exc,
            ) from exc
        names = sorted(_resource_identity(item)[0] for item in items)
        failures = self._cleanup(names)
        if failures:
            raise _error(
                CatalogUploadStoreErrorCode.DELETE_FAILED,
                f"Could not delete catalog upload {upload_id}: {', '.join(failures)}",
                upload_id=upload_id,
                cleanup_failures=failures,
            )
        return CatalogUploadDeleteReceipt(upload_id=upload_id, deleted_names=tuple(names))

    def garbage_collect(
        self,
        *,
        active_upload_ids: Collection[str],
        now: datetime | None = None,
    ) -> CatalogUploadGarbageCollectionReceipt:
        if isinstance(active_upload_ids, str):
            raise TypeError("active_upload_ids must be a collection of upload IDs")
        active = frozenset(active_upload_ids)
        if not all(isinstance(upload_id, str) and upload_id for upload_id in active):
            raise TypeError("active_upload_ids must contain non-empty strings")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        try:
            response = self._client.list_namespaced_config_map(
                namespace=self._namespace,
                label_selector=CATALOG_UPLOAD_LABEL,
            )
            items = _items(response)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload resources: {exc}",
                cause=exc,
            ) from exc

        groups: dict[str, list[Any]] = {}
        unsafe_names: list[str] = []
        for item in items:
            try:
                name, namespace, _uid = _resource_identity(item)
                labels = _string_mapping(_field(_metadata(item), "labels"), label=f"{name} labels")
                upload_id = labels.get(CATALOG_UPLOAD_LABEL)
                if namespace != self._namespace or not upload_id:
                    unsafe_names.append(name)
                    continue
                groups.setdefault(upload_id, []).append(item)
            except ValueError:
                name = _field(_metadata(item), "name")
                unsafe_names.append(str(name or "<unknown>"))

        deleted_names: list[str] = []
        active_names: list[str] = []
        grace_names: list[str] = []
        cutoff = current - self._gc_grace
        for upload_id, group in sorted(groups.items()):
            names = sorted(_resource_identity(item)[0] for item in group)
            if upload_id in active:
                active_names.extend(names)
                continue
            timestamps = tuple(_creation_timestamp(item) for item in group)
            if any(timestamp is None for timestamp in timestamps):
                unsafe_names.extend(names)
                continue
            if any(timestamp > cutoff for timestamp in timestamps if timestamp is not None):
                grace_names.extend(names)
                continue
            failures = self._cleanup(names)
            if failures:
                raise _error(
                    CatalogUploadStoreErrorCode.DELETE_FAILED,
                    f"Could not garbage-collect catalog upload {upload_id}: " + ", ".join(failures),
                    upload_id=upload_id,
                    cleanup_failures=failures,
                )
            deleted_names.extend(names)

        return CatalogUploadGarbageCollectionReceipt(
            active_upload_ids=tuple(sorted(active)),
            deleted_names=tuple(sorted(deleted_names)),
            active_names=tuple(sorted(active_names)),
            grace_names=tuple(sorted(grace_names)),
            unsafe_names=tuple(sorted(unsafe_names)),
        )

    def _cleanup(self, names: Collection[str]) -> tuple[str, ...]:
        failures: list[str] = []
        for name in names:
            try:
                self._client.delete_namespaced_config_map(
                    name=name,
                    namespace=self._namespace,
                    body={"apiVersion": "v1", "kind": "DeleteOptions"},
                )
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    failures.append(name)
        return tuple(failures)
