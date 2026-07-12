"""Tests for the one-list ordinary-file Kubernetes runtime reader."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_upload import CatalogUpload, CatalogUploadError, encode_catalog_upload
from nodalarc.kubernetes_runtime_config import (
    CATALOG_DOCUMENT_KEY,
    CATALOG_REF_ANNOTATION,
    CATALOG_UPLOAD_LABEL,
    RUNTIME_CONFIG_PROOF_FILENAME,
    KubernetesRuntimeConfigError,
    KubernetesRuntimeConfigErrorCode,
    load_kubernetes_runtime_config,
)
from nodalarc.models.resolved_session import SourceContext
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.runtime_config import RuntimeConfigProof

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
            origin="test.kubernetes_runtime_config.prepare",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="kubernetes-test-upload")


class FakeCoreV1:
    def __init__(self, config_maps: list[Any]) -> None:
        self.config_maps = config_maps
        self.lists: list[tuple[str, str]] = []
        self.error: BaseException | None = None

    def list_namespaced_config_map(self, namespace: str, *, label_selector: str) -> Any:
        self.lists.append((namespace, label_selector))
        if self.error is not None:
            raise self.error
        key, value = label_selector.split("=", 1)
        return SimpleNamespace(
            items=[
                config_map
                for config_map in self.config_maps
                if config_map.metadata.labels.get(key) == value
            ]
        )


def _config_map(entry, *, upload_id: str, order: int) -> SimpleNamespace:
    name = f"{upload_id}-{order:06d}"
    return SimpleNamespace(
        api_version="v1",
        kind="ConfigMap",
        metadata=SimpleNamespace(
            name=name,
            namespace=NAMESPACE,
            labels={CATALOG_UPLOAD_LABEL: upload_id},
            annotations={CATALOG_REF_ANNOTATION: str(entry.ref)},
            owner_references=None,
        ),
        immutable=None,
        data={CATALOG_DOCUMENT_KEY: entry.yaml_bytes.decode("utf-8")},
        binary_data=None,
    )


def _client_for(upload: CatalogUpload) -> FakeCoreV1:
    return FakeCoreV1(
        [
            _config_map(entry, upload_id=upload.upload_id, order=order)
            for order, entry in enumerate(upload.catalog_files)
        ]
    )


def _load(client: FakeCoreV1, upload: CatalogUpload, destination: Path):
    return load_kubernetes_runtime_config(
        client,
        namespace=NAMESPACE,
        root_yaml=upload.root_yaml,
        selection=upload.selection,
        destination=destination,
        installed_shipped_root=SHIPPED_ROOT,
        source_context=SourceContext(
            origin="test.kubernetes_runtime_config",
            run_id="run-kubernetes-reader-0001",
        ),
    )


def test_fetches_once_and_materializes_exact_ordinary_paths(
    upload: CatalogUpload,
    prepared: PreparedSessionFiles,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    destination = tmp_path / "runtime"

    loaded = _load(client, upload, destination)

    assert client.lists == [(NAMESPACE, f"{CATALOG_UPLOAD_LABEL}={upload.upload_id}")]
    assert loaded.session_path.read_bytes() == prepared.root_yaml
    for entry in prepared.catalog_files:
        assert (destination / entry.preserved_path).read_bytes() == entry.yaml_bytes
    proof_bytes = (destination / RUNTIME_CONFIG_PROOF_FILENAME).read_bytes()
    canonical_proof = json.dumps(
        json.loads(proof_bytes),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert canonical_proof == proof_bytes
    proof = RuntimeConfigProof.model_validate_json(proof_bytes)
    assert proof == loaded.proof
    assert proof.upload_id == upload.upload_id
    assert proof.run_id == "run-kubernetes-reader-0001"


def test_typed_config_maps_without_type_meta_are_accepted(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    for config_map in client.config_maps:
        config_map.api_version = None
        config_map.kind = None

    loaded = _load(client, upload, tmp_path / "without-type-meta")

    assert loaded.proof.upload_id == upload.upload_id


def _deep_user_upload(tmp_path: Path) -> tuple[CatalogUpload, dict[str, bytes]]:
    user_root = tmp_path / "user"
    user_root.mkdir()
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)
    root_document = yaml.safe_load(SIMPLE_SESSION.read_bytes())
    shipped_constellation_ref = root_document["segments"][0]["source"]
    root_document["segments"][0]["source"] = "user:constellations/deep.yaml"

    constellation = yaml.safe_load(
        (SHIPPED_ROOT / shipped_constellation_ref.split(":", 1)[1]).read_bytes()
    )
    shipped_node_ref = constellation["constellation"]["node"]
    constellation["constellation"]["id"] = "deep"
    constellation["constellation"]["node"] = "user:nodes/deep.yaml"

    node = yaml.safe_load((SHIPPED_ROOT / shipped_node_ref.split(":", 1)[1]).read_bytes())
    shipped_terminal_ref = node["node"]["terminals"][0]["terminal"]
    node["node"]["id"] = "deep"
    node["node"]["terminals"][0]["terminal"] = "user:terminals/deep.yaml"

    terminal = yaml.safe_load((SHIPPED_ROOT / shipped_terminal_ref.split(":", 1)[1]).read_bytes())
    terminal["terminal"]["id"] = "deep"
    user_files = {
        "constellations/deep.yaml": yaml.safe_dump(constellation, sort_keys=False).encode(),
        "nodes/deep.yaml": yaml.safe_dump(node, sort_keys=False).encode(),
        "terminals/deep.yaml": b"# exact deep user terminal\n"
        + yaml.safe_dump(terminal, sort_keys=False).encode(),
    }
    for relative, content in user_files.items():
        path = user_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    root_yaml = (
        b"# exact deep user session\n"
        + yaml.safe_dump(
            root_document,
            sort_keys=False,
        ).encode()
    )
    prepared = prepare_session_files(
        root_yaml,
        FilesystemCatalogReadView(roots),
        source=PreparedSessionSource(
            logical_id="user:sessions/deep.yaml",
            origin="test.kubernetes_runtime_config.deep",
        ),
        source_revision=_digest(root_yaml),
        available_node_count=100,
    )
    return encode_catalog_upload(prepared, upload_id="deep-user-upload"), user_files


def test_deep_user_references_preserve_user_catalog_paths(tmp_path: Path) -> None:
    upload, user_files = _deep_user_upload(tmp_path)
    destination = tmp_path / "deep-runtime"

    loaded = _load(_client_for(upload), upload, destination)

    assert loaded.proof.upload_id == upload.upload_id
    for relative, content in user_files.items():
        assert (destination / "catalog" / "user" / relative).read_bytes() == content


def test_missing_document_is_rejected_by_upload_verification(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    client.config_maps.pop()
    destination = tmp_path / "missing"

    with pytest.raises(CatalogUploadError):
        _load(client, upload, destination)

    assert client.lists == [(NAMESPACE, f"{CATALOG_UPLOAD_LABEL}={upload.upload_id}")]
    assert not destination.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda config_map: setattr(config_map, "api_version", "v2"),
        lambda config_map: setattr(config_map, "kind", "Secret"),
        lambda config_map: config_map.metadata.labels.__setitem__("unexpected", "value"),
        lambda config_map: config_map.metadata.annotations.clear(),
        lambda config_map: setattr(config_map, "immutable", True),
        lambda config_map: setattr(config_map, "binary_data", {"document.yaml": "bad"}),
        lambda config_map: setattr(config_map.metadata, "owner_references", [object()]),
        lambda config_map: setattr(config_map, "data", {"wrong": "field"}),
    ],
)
def test_config_maps_must_match_the_store_shape(
    upload: CatalogUpload,
    tmp_path: Path,
    mutate,
) -> None:
    client = _client_for(upload)
    mutate(client.config_maps[0])

    with pytest.raises(KubernetesRuntimeConfigError) as raised:
        _load(client, upload, tmp_path / "invalid")

    assert raised.value.code is KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP


def test_duplicate_ref_is_rejected_before_upload_verification(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    duplicate = deepcopy(client.config_maps[0])
    duplicate.metadata.name = "duplicate-name"
    client.config_maps.append(duplicate)

    with pytest.raises(KubernetesRuntimeConfigError) as raised:
        _load(client, upload, tmp_path / "duplicate")

    assert raised.value.code is KubernetesRuntimeConfigErrorCode.INVALID_CONFIG_MAP


def test_list_failure_is_typed_and_does_not_materialize(
    upload: CatalogUpload,
    tmp_path: Path,
) -> None:
    client = _client_for(upload)
    client.error = RuntimeError("list unavailable")
    destination = tmp_path / "list-failure"

    with pytest.raises(KubernetesRuntimeConfigError) as raised:
        _load(client, upload, destination)

    assert raised.value.code is KubernetesRuntimeConfigErrorCode.CONFIG_MAP_FETCH_FAILED
    assert not destination.exists()
