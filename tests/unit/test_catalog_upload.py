"""Ordinary-YAML catalog upload contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from nodalarc.catalog_closure import (
    CatalogClosureEntry,
    FilesystemCatalogReadView,
    preserved_catalog_path,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_upload import (
    DEFAULT_CATALOG_UPLOAD_LIMITS,
    CatalogUpload,
    CatalogUploadError,
    CatalogUploadErrorCode,
    CatalogUploadSelection,
    encode_catalog_upload,
    sha256_digest,
    verify_catalog_upload,
)
from nodalarc.prepared_session import (
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions/earth-leo-simple.yaml"


@pytest.fixture(scope="module")
def prepared() -> PreparedSessionFiles:
    root_yaml = SIMPLE_SESSION.read_bytes()
    return prepare_session_files(
        root_yaml,
        FilesystemCatalogReadView(CatalogRoots.from_catalog_root(SHIPPED_ROOT)),
        source=PreparedSessionSource(
            logical_id="nodalarc:sessions/earth-leo-simple.yaml",
            origin="test.catalog_upload",
        ),
        source_revision=sha256_digest(root_yaml),
        available_node_count=100,
    )


@pytest.fixture(scope="module")
def upload(prepared: PreparedSessionFiles) -> CatalogUpload:
    return encode_catalog_upload(prepared, upload_id="upload-test-ordinary-yaml")


def _entry(ref: str, content: bytes) -> CatalogClosureEntry:
    parsed = CatalogRef(ref)
    assert parsed.family is not None
    return CatalogClosureEntry(
        ref=parsed,
        family=parsed.family,
        preserved_path=preserved_catalog_path(parsed),
        yaml_bytes=content,
        document_digest=sha256_digest(content),
        size_bytes=len(content),
    )


def test_encode_preserves_root_refs_paths_and_exact_yaml(
    prepared: PreparedSessionFiles,
    upload: CatalogUpload,
) -> None:
    assert upload.root_yaml == prepared.root_yaml
    assert upload.catalog_files == prepared.catalog_files
    assert upload.selection == CatalogUploadSelection(
        upload_id="upload-test-ordinary-yaml",
        closure_digest=prepared.closure_digest,
        file_count=len(prepared.catalog_files),
    )
    assert all(
        entry.preserved_path == preserved_catalog_path(entry.ref) for entry in upload.catalog_files
    )
    assert verify_catalog_upload(upload) is upload


def test_default_upload_ids_are_fresh_not_content_addressed(prepared: PreparedSessionFiles) -> None:
    first = encode_catalog_upload(prepared)
    second = encode_catalog_upload(prepared)

    assert first.upload_id != second.upload_id
    assert first.selection.closure_digest == second.selection.closure_digest
    assert first.catalog_files == second.catalog_files


def test_selection_is_small_strict_and_closed(upload: CatalogUpload) -> None:
    assert set(upload.selection.model_dump(mode="json")) == {
        "upload_id",
        "closure_digest",
        "file_count",
    }
    with pytest.raises(ValidationError):
        CatalogUploadSelection.model_validate(
            {**upload.selection.model_dump(mode="json"), "manifest": "forbidden"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        CatalogUploadSelection.model_validate(
            {**upload.selection.model_dump(mode="json"), "file_count": "1"},
            strict=True,
        )


def test_verify_rejects_missing_extra_duplicate_and_corrupt_files(upload: CatalogUpload) -> None:
    with pytest.raises(CatalogUploadError) as missing:
        verify_catalog_upload(replace(upload, catalog_files=upload.catalog_files[:-1]))
    assert missing.value.code is CatalogUploadErrorCode.CLOSURE_MISMATCH

    extra_content = (SHIPPED_ROOT / "bodies/luna.yaml").read_bytes()
    extra = _entry("nodalarc:bodies/luna.yaml", extra_content)
    with pytest.raises(CatalogUploadError) as unexpected:
        verify_catalog_upload(replace(upload, catalog_files=(*upload.catalog_files, extra)))
    assert unexpected.value.code is CatalogUploadErrorCode.CLOSURE_MISMATCH

    duplicate = (*upload.catalog_files, upload.catalog_files[0])
    with pytest.raises(CatalogUploadError) as repeated:
        verify_catalog_upload(replace(upload, catalog_files=duplicate))
    assert repeated.value.code is CatalogUploadErrorCode.CLOSURE_MISMATCH

    first = upload.catalog_files[0]
    corrupt = replace(first, yaml_bytes=b"not: [valid", size_bytes=len(b"not: [valid"))
    with pytest.raises(CatalogUploadError) as invalid:
        verify_catalog_upload(replace(upload, catalog_files=(corrupt, *upload.catalog_files[1:])))
    assert invalid.value.code is CatalogUploadErrorCode.INVALID_UPLOAD


def test_verify_rejects_selection_digest_and_count_mismatch(upload: CatalogUpload) -> None:
    wrong_digest = upload.selection.model_copy(update={"closure_digest": f"sha256:{'0' * 64}"})
    with pytest.raises(CatalogUploadError) as digest:
        verify_catalog_upload(replace(upload, selection=wrong_digest))
    assert digest.value.code is CatalogUploadErrorCode.CLOSURE_MISMATCH

    wrong_count = upload.selection.model_copy(update={"file_count": upload.file_count + 1})
    with pytest.raises(CatalogUploadError) as count:
        verify_catalog_upload(replace(upload, selection=wrong_count))
    assert count.value.code is CatalogUploadErrorCode.CLOSURE_MISMATCH


@pytest.mark.parametrize(
    ("limit_name", "maximum"),
    (
        ("max_root_yaml_bytes", 1),
        ("max_file_bytes", 1),
        ("max_file_count", 1),
        ("max_aggregate_bytes", 1),
    ),
)
def test_every_upload_bound_is_a_typed_refusal(
    prepared: PreparedSessionFiles,
    limit_name: str,
    maximum: int,
) -> None:
    limits = DEFAULT_CATALOG_UPLOAD_LIMITS.model_copy(update={limit_name: maximum})
    with pytest.raises(CatalogUploadError) as raised:
        encode_catalog_upload(prepared, upload_id="upload-over-limit", limits=limits)
    assert raised.value.code is CatalogUploadErrorCode.LIMIT_EXCEEDED
    assert raised.value.evidence.limit_name == limit_name
