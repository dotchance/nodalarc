"""Tests for the Operator adapter over the shared selected-upload boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, CatalogUploadError, encode_catalog_upload
from nodalarc.cr_runtime_config import RuntimeSessionConfig
from nodalarc.kubernetes_runtime_config import (
    CATALOG_DOCUMENT_KEY,
    CATALOG_REF_ANNOTATION,
    CATALOG_UPLOAD_LABEL,
)
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)

from services.nodalarc_operator.runtime_session import (
    OperatorSessionConfig,
    resolve_operator_session,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"
NAMESPACE = "nodalarc-test"


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def prepared() -> PreparedSessionFiles:
    root_yaml = SIMPLE_SESSION.read_bytes()
    return prepare_session_files(
        root_yaml,
        FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT)),
        source=PreparedSessionSource(
            logical_id="nodalarc:sessions/earth-leo-simple.yaml",
            origin="test.operator_runtime_session.prepare",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="operator-test-upload")


class FakeCoreV1:
    def __init__(self, config_maps: list[Any]) -> None:
        self.config_maps = config_maps
        self.lists: list[tuple[str, str]] = []

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any:
        self.lists.append((namespace, label_selector))
        key, value = label_selector.split("=", 1)
        return SimpleNamespace(
            items=[
                config_map
                for config_map in self.config_maps
                if config_map.metadata.labels.get(key) == value
            ]
        )


def _client_for(upload: CatalogUpload) -> FakeCoreV1:
    config_maps = []
    for order, entry in enumerate(upload.catalog_files):
        config_maps.append(
            SimpleNamespace(
                api_version="v1",
                kind="ConfigMap",
                metadata=SimpleNamespace(
                    name=f"{upload.upload_id}-{order:06d}",
                    namespace=NAMESPACE,
                    labels={CATALOG_UPLOAD_LABEL: upload.upload_id},
                    annotations={CATALOG_REF_ANNOTATION: str(entry.ref)},
                    owner_references=None,
                ),
                immutable=None,
                data={CATALOG_DOCUMENT_KEY: entry.yaml_bytes.decode("utf-8")},
                binary_data=None,
            )
        )
    return FakeCoreV1(config_maps)


def _spec(upload: CatalogUpload) -> dict[str, Any]:
    return {
        "sessionYaml": upload.root_yaml.decode("utf-8"),
        "catalogUpload": upload.selection.model_dump(mode="json"),
    }


def test_operator_uses_the_shared_required_selection_once(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)

    loaded = resolve_operator_session(
        _spec(upload),
        core_v1=client,
        namespace=NAMESPACE,
        source_origin="test.operator_runtime_session",
        run_id="run-operator-runtime-0001",
        installed_shipped_root=SHIPPED_ROOT,
        materialization_parent=tmp_path,
    )

    assert OperatorSessionConfig is RuntimeSessionConfig
    assert loaded.root_yaml.encode("utf-8") == upload.root_yaml
    assert loaded.catalog_upload == upload.selection
    assert loaded.proof.upload_id == upload.upload_id
    assert loaded.proof.run_id == "run-operator-runtime-0001"
    assert client.lists == [(NAMESPACE, f"{CATALOG_UPLOAD_LABEL}={upload.upload_id}")]


def test_operator_rejects_the_retired_selection_pair_fields(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    """The CR spec carries the session and its upload, nothing else: the
    retired selection pair is an unsupported field, not a silent no-op."""
    paired = {
        **_spec(upload),
        "implementationBindingRef": "nodalarc:bindings/frr-observer-everywhere.yaml",
        "implementationPackageDigest": "sha256:" + "c" * 64,
    }
    with pytest.raises(ValueError, match="unsupported field"):
        resolve_operator_session(
            paired,
            core_v1=_client_for(upload),
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            run_id="run-operator-runtime-0001",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )


def test_operator_rejects_missing_or_malformed_selection(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    missing = {"sessionYaml": upload.root_yaml.decode("utf-8")}
    with pytest.raises(ValueError, match="catalogUpload is required"):
        resolve_operator_session(
            missing,
            core_v1=client,
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )

    malformed = _spec(upload)
    malformed["catalogUpload"] = None
    with pytest.raises(ValueError):
        resolve_operator_session(
            malformed,
            core_v1=client,
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )

    assert client.lists == []


def test_operator_requires_kubernetes_and_rejects_extra_spec_fields(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="core_v1 is required"):
        resolve_operator_session(
            _spec(upload),
            core_v1=None,  # type: ignore[arg-type]
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )

    unexpected = {**_spec(upload), "legacyMode": True}
    with pytest.raises(ValueError, match="unsupported field"):
        resolve_operator_session(
            unexpected,
            core_v1=_client_for(upload),
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )


def test_operator_propagates_corrupt_upload_refusal(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    client.config_maps.pop()

    with pytest.raises(CatalogUploadError):
        resolve_operator_session(
            _spec(upload),
            core_v1=client,
            namespace=NAMESPACE,
            source_origin="test.operator_runtime_session",
            installed_shipped_root=SHIPPED_ROOT,
            materialization_parent=tmp_path,
        )
