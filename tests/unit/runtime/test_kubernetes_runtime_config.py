"""Tests for the one-list ordinary-file Kubernetes runtime reader."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, encode_catalog_upload
from nodalarc.kubernetes_runtime_config import (
    KubernetesRuntimeConfigError,
    catalog_upload_config_map_identity,
    catalog_upload_config_map_name,
    decode_catalog_upload_config_map,
    encode_catalog_upload_config_map,
)
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)

ROOT = Path(__file__).resolve().parents[3]
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
            origin="test.kubernetes_runtime_config.prepare",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="kubernetes-test-upload")


def test_encoded_body_decodes_to_the_same_file_once_persisted(upload: CatalogUpload) -> None:
    for order, entry in enumerate(upload.catalog_files):
        body = encode_catalog_upload_config_map(
            namespace=NAMESPACE, upload_id=upload.upload_id, order=order, entry=entry
        )
        assert "uid" not in body["metadata"]
        with pytest.raises(KubernetesRuntimeConfigError, match="lacks a name, namespace or uid"):
            decode_catalog_upload_config_map(body, namespace=NAMESPACE, upload_id=upload.upload_id)

        persisted = {**body, "metadata": {**body["metadata"], "uid": f"uid-{order}"}}
        decoded = decode_catalog_upload_config_map(
            persisted, namespace=NAMESPACE, upload_id=upload.upload_id
        )

        assert decoded.name == catalog_upload_config_map_name(upload.upload_id, order)
        assert decoded.uid == f"uid-{order}"
        assert decoded.entry == entry
        identity = catalog_upload_config_map_identity(persisted)
        assert (identity.name, identity.namespace, identity.upload_id) == (
            decoded.name,
            NAMESPACE,
            upload.upload_id,
        )


def test_config_map_names_stay_within_kubernetes_limits() -> None:
    assert catalog_upload_config_map_name("upload-abc", 7) == "upload-abc-000007"
    long_name = catalog_upload_config_map_name("u" * 60 + "---", 12)
    assert len(long_name) <= 63
    assert long_name.endswith("-000012")
    assert "--000012" not in long_name
