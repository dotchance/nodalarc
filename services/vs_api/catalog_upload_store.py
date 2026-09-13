"""Namespaced Kubernetes storage for ordinary catalog YAML files."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_upload import (
    CatalogUpload,
    CatalogUploadSelection,
    verify_catalog_upload,
)
from nodalarc.kubernetes_runtime_config import (
    CATALOG_UPLOAD_LABEL,
    ConfigMapReader,
    KubernetesRuntimeConfigError,
    catalog_upload_config_map_identity,
    config_map_metadata_identity,
    decode_catalog_upload_config_maps,
    encode_catalog_upload_config_map,
    listed_config_maps,
)

DEFAULT_CATALOG_UPLOAD_GC_GRACE = timedelta(minutes=15)
MAX_CATALOG_UPLOAD_GC_GRACE = timedelta(hours=24)


class CoreV1ConfigMapApi(ConfigMapReader, Protocol):
    """The lifecycle transport: the shared list operation plus create and delete."""

    def create_namespaced_config_map(self, namespace: str, body: Mapping[str, Any]) -> Any: ...

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


def _creation_timestamp(value: Any) -> datetime | None:
    metadata = (
        value.get("metadata") if isinstance(value, Mapping) else getattr(value, "metadata", None)
    )
    if isinstance(metadata, Mapping):
        timestamp = metadata.get("creation_timestamp", metadata.get("creationTimestamp"))
    else:
        timestamp = getattr(metadata, "creation_timestamp", None)
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


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
                try:
                    body = encode_catalog_upload_config_map(
                        namespace=self._namespace,
                        upload_id=upload.upload_id,
                        order=order,
                        entry=entry,
                    )
                except KubernetesRuntimeConfigError as exc:
                    raise _error(
                        CatalogUploadStoreErrorCode.INVALID_UPLOAD,
                        str(exc),
                        upload_id=upload.upload_id,
                        cause=exc,
                    ) from exc
                name = body["metadata"]["name"]
                try:
                    observed = self._client.create_namespaced_config_map(
                        namespace=self._namespace,
                        body=body,
                    )
                    # Register what the server persisted before any content
                    # rule runs: readback judges the content, cleanup needs
                    # the identity either way.
                    identity = config_map_metadata_identity(observed)
                except Exception as exc:
                    raise _error(
                        CatalogUploadStoreErrorCode.CREATE_FAILED,
                        f"Could not create catalog YAML ConfigMap {name}: {exc}",
                        upload_id=upload.upload_id,
                        resource_name=name,
                        cause=exc,
                        created_names=(resource.name for resource in created),
                    ) from exc
                if identity.name != name or identity.namespace != self._namespace:
                    raise _error(
                        CatalogUploadStoreErrorCode.CREATE_FAILED,
                        f"Created ConfigMap identity does not match {self._namespace}/{name}",
                        upload_id=upload.upload_id,
                        resource_name=name,
                        created_names=(resource.name for resource in created),
                    )
                evidence = CatalogUploadResourceEvidence(name=name, ref=entry.ref, uid=identity.uid)
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
            cleanup_failures = _failed_delete_names(
                self._cleanup(resource.name for resource in created)
            )
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
            items = listed_config_maps(response, namespace=self._namespace)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload {selection.upload_id}: {exc}",
                upload_id=selection.upload_id,
                cause=exc,
            ) from exc

        try:
            upload, resources = decode_catalog_upload_config_maps(
                items,
                namespace=self._namespace,
                root_yaml=root_yaml,
                selection=selection,
            )
            verify_catalog_upload(upload)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.READBACK_MISMATCH,
                f"Catalog upload {selection.upload_id} readback is invalid: {exc}",
                upload_id=selection.upload_id,
                cause=exc,
            ) from exc
        return upload, tuple(
            CatalogUploadResourceEvidence(name=item.name, ref=item.entry.ref, uid=item.uid)
            for item in resources
        )

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
            items = listed_config_maps(response, namespace=self._namespace)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload {upload_id}: {exc}",
                upload_id=upload_id,
                cause=exc,
            ) from exc
        names = sorted(catalog_upload_config_map_identity(item).name for item in items)
        failures = self._cleanup(names)
        if failures:
            failed_names = _failed_delete_names(failures)
            group = _failed_delete_group(failures)
            raise _error(
                CatalogUploadStoreErrorCode.DELETE_FAILED,
                f"Could not delete catalog upload {upload_id}: {', '.join(failed_names)}",
                upload_id=upload_id,
                cause=group,
                cleanup_failures=failed_names,
            ) from group
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
            items = listed_config_maps(response, namespace=self._namespace)
        except Exception as exc:
            raise _error(
                CatalogUploadStoreErrorCode.LIST_FAILED,
                f"Could not list catalog upload resources: {exc}",
                cause=exc,
            ) from exc

        groups: dict[str, list[tuple[str, Any]]] = {}
        unsafe_names: list[str] = []
        for item in items:
            try:
                identity = catalog_upload_config_map_identity(item)
            except KubernetesRuntimeConfigError as exc:
                unsafe_names.append(str(exc.evidence.config_map_name or "<unknown>"))
                continue
            if identity.namespace != self._namespace:
                unsafe_names.append(identity.name)
                continue
            groups.setdefault(identity.upload_id, []).append((identity.name, item))

        deleted_names: list[str] = []
        active_names: list[str] = []
        grace_names: list[str] = []
        cutoff = current - self._gc_grace
        for upload_id, group in sorted(groups.items()):
            names = sorted(name for name, _item in group)
            if upload_id in active:
                active_names.extend(names)
                continue
            timestamps = tuple(_creation_timestamp(item) for _name, item in group)
            if any(timestamp is None for timestamp in timestamps):
                unsafe_names.extend(names)
                continue
            if any(timestamp > cutoff for timestamp in timestamps if timestamp is not None):
                grace_names.extend(names)
                continue
            failures = self._cleanup(names)
            if failures:
                failed_names = _failed_delete_names(failures)
                group = _failed_delete_group(failures)
                raise _error(
                    CatalogUploadStoreErrorCode.DELETE_FAILED,
                    f"Could not garbage-collect catalog upload {upload_id}: "
                    + ", ".join(failed_names),
                    upload_id=upload_id,
                    cause=group,
                    cleanup_failures=failed_names,
                ) from group
            deleted_names.extend(names)

        return CatalogUploadGarbageCollectionReceipt(
            active_upload_ids=tuple(sorted(active)),
            deleted_names=tuple(sorted(deleted_names)),
            active_names=tuple(sorted(active_names)),
            grace_names=tuple(sorted(grace_names)),
            unsafe_names=tuple(sorted(unsafe_names)),
        )

    def _cleanup(self, names: Collection[str]) -> tuple[tuple[str, Exception], ...]:
        """Delete each named ConfigMap; return every non-404 failure with its exception."""
        failures: list[tuple[str, Exception]] = []
        for name in names:
            try:
                self._client.delete_namespaced_config_map(
                    name=name,
                    namespace=self._namespace,
                    body={"apiVersion": "v1", "kind": "DeleteOptions"},
                )
            except Exception as exc:
                if getattr(exc, "status", None) != 404:
                    failures.append((name, exc))
        return tuple(failures)


def _failed_delete_names(failures: Sequence[tuple[str, Exception]]) -> tuple[str, ...]:
    return tuple(name for name, _exc in failures)


def _failed_delete_group(failures: Sequence[tuple[str, Exception]]) -> ExceptionGroup[Exception]:
    """Every failed delete of one upload group, retained together as the cause."""
    return ExceptionGroup("catalog upload deletes failed", [exc for _name, exc in failures])
