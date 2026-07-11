"""Tests for exact session preparation before any runtime transition."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from nodalarc import prepared_session as prepared_module
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogClosureErrorCode,
    CatalogReadView,
    FilesystemCatalogReadView,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogRef, SessionRef
from nodalarc.prepared_session import (
    PreparedSessionError,
    PreparedSessionErrorCode,
    PreparedSessionFiles,
    PreparedSessionSource,
    prepare_session_files,
)
from nodalarc.resolve_session import SessionResolution
from nodalarc.runtime_support import FeatureCategory, UnsupportedFeatureError

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _load_shipped_ref(ref: str) -> dict:
    reference = CatalogRef(ref)
    return yaml.safe_load((SHIPPED_ROOT / reference.relative_path).read_bytes())


def _write_user_ref(user_root: Path, ref: str, content: bytes) -> None:
    reference = CatalogRef(ref)
    assert reference.namespace == "user"
    path = user_root / reference.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@dataclass(frozen=True)
class PreparedFixture:
    root_yaml: bytes
    roots: CatalogRoots
    read_view: FilesystemCatalogReadView
    source: PreparedSessionSource
    source_revision: str
    user_contents: dict[str, bytes]


@pytest.fixture()
def prepared_fixture(tmp_path: Path) -> PreparedFixture:
    user_root = tmp_path / "user-catalog"
    user_root.mkdir()
    roots = CatalogRoots.from_catalog_root(SHIPPED_ROOT, user_root=user_root)

    root_document = yaml.safe_load(SIMPLE_SESSION.read_bytes())
    root_document["session"]["name"] = "prepared-user-deep"
    shipped_constellation_ref = root_document["segments"][0]["source"]
    root_document["segments"][0]["source"] = "user:constellations/prepared-constellation.yaml"

    constellation = _load_shipped_ref(shipped_constellation_ref)
    shipped_node_ref = constellation["constellation"]["node"]
    constellation["constellation"]["id"] = "prepared-constellation"
    constellation["constellation"]["node"] = "user:nodes/prepared-node.yaml"

    node = _load_shipped_ref(shipped_node_ref)
    shipped_terminal_ref = node["node"]["terminals"][0]["terminal"]
    node["node"]["id"] = "prepared-node"
    node["node"]["terminals"][0]["terminal"] = "user:terminals/prepared-terminal.yaml"
    node["node"]["notes"] = "Literal user:terminals/not-a-dependency.yaml stays prose."

    terminal = _load_shipped_ref(shipped_terminal_ref)
    terminal["terminal"]["id"] = "prepared-terminal"

    root_yaml = b"# exact prepared root bytes\n" + yaml.safe_dump(
        root_document, sort_keys=False
    ).encode("utf-8")
    user_contents = {
        "user:constellations/prepared-constellation.yaml": yaml.safe_dump(
            constellation, sort_keys=False
        ).encode("utf-8"),
        "user:nodes/prepared-node.yaml": yaml.safe_dump(node, sort_keys=False).encode("utf-8"),
        "user:terminals/prepared-terminal.yaml": b"# exact nested user bytes\n"
        + yaml.safe_dump(terminal, sort_keys=False).encode("utf-8"),
    }
    for ref, content in user_contents.items():
        _write_user_ref(user_root, ref, content)

    source = PreparedSessionSource(
        logical_id="user:sessions/prepared-user-deep.yaml",
        origin="test.prepared_session",
    )
    return PreparedFixture(
        root_yaml=root_yaml,
        roots=roots,
        read_view=FilesystemCatalogReadView(roots),
        source=source,
        source_revision=_sha256(root_yaml),
        user_contents=user_contents,
    )


def _closure(fixture: PreparedFixture):
    return CatalogClosureCollector.collect(fixture.root_yaml, fixture.read_view)


def _prepare(
    fixture: PreparedFixture,
    *,
    source: PreparedSessionSource | None = None,
    run_id: str = "run-prepared-0001",
    available_node_count: int = 100,
) -> PreparedSessionFiles:
    closure = _closure(fixture)
    return prepare_session_files(
        fixture.root_yaml,
        fixture.read_view,
        source=source or fixture.source,
        source_revision=fixture.source_revision,
        expected_source_revision=fixture.source_revision,
        expected_document_digest=closure.document_digest,
        expected_closure_digest=closure.closure_digest,
        available_node_count=available_node_count,
        run_id=run_id,
    )


def test_prepares_deep_user_refs_exactly_and_resolves_once(
    prepared_fixture: PreparedFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure = _closure(prepared_fixture)
    resolver = prepared_module.resolve_session_with_assets
    calls: list[Path] = []

    def tracking_resolver(raw_session, **kwargs):
        roots = kwargs["catalog_roots"]
        source_context = kwargs["source_context"]
        temp_root = roots.root.parents[1]
        assert (temp_root / "session.yaml").read_bytes() == prepared_fixture.root_yaml
        for entry in closure.entries:
            assert (temp_root / entry.preserved_path).read_bytes() == entry.yaml_bytes
        assert source_context.origin == prepared_fixture.source.origin
        assert source_context.run_id == "run-prepared-0001"
        assert source_context.session_path is None
        calls.append(temp_root)
        return resolver(raw_session, **kwargs)

    monkeypatch.setattr(prepared_module, "resolve_session_with_assets", tracking_resolver)

    prepared = _prepare(prepared_fixture, available_node_count=1)

    assert len(calls) == 1
    assert not calls[0].exists()
    assert isinstance(prepared, PreparedSessionFiles)
    assert isinstance(prepared.resolution, SessionResolution)
    assert isinstance(prepared.source.logical_id, SessionRef)
    assert {field.name for field in fields(prepared)} == {
        "source",
        "source_revision",
        "root_yaml",
        "catalog_files",
        "document_digest",
        "closure_digest",
        "resolved_semantic_digest",
        "resolution",
        "validation_report",
        "warnings",
        "file_count",
        "total_bytes",
    }
    assert prepared.root_yaml == prepared_fixture.root_yaml
    assert prepared.catalog_files == closure.entries
    assert prepared.source_revision == prepared_fixture.source_revision
    assert prepared.document_digest == closure.document_digest
    assert prepared.closure_digest == closure.closure_digest
    assert prepared.file_count == closure.deployment_file_count
    assert prepared.total_bytes == closure.deployment_total_bytes
    assert prepared.validation_report.status == "valid"
    assert prepared.validation_report.dispatchable is True
    assert prepared.validation_report.errors == ()
    assert prepared.warnings == prepared.validation_report.warnings
    assert any(warning.code == "W004" for warning in prepared.warnings)

    by_ref = {str(entry.ref): entry for entry in prepared.catalog_files}
    for ref, content in prepared_fixture.user_contents.items():
        assert by_ref[ref].yaml_bytes == content
    assert prepared.resolution.resolved.source_context.session_path is None
    assert "/tmp/" not in repr(prepared.source)
    assert str(calls[0]) not in repr(prepared)


@dataclass
class _RecordingReadView:
    delegate: CatalogReadView
    refs: list[str]

    def read(self, ref):
        self.refs.append(str(ref))
        return self.delegate.read(ref)


def test_stale_preconditions_stop_at_the_earliest_authoritative_boundary(
    prepared_fixture: PreparedFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure = _closure(prepared_fixture)
    resolver_calls: list[object] = []

    def forbidden_resolver(*args, **kwargs):
        resolver_calls.append((args, kwargs))
        raise AssertionError("resolver must not run for stale preparation")

    def forbidden_temp_dir(*args, **kwargs):
        raise AssertionError("temp materialization must not run for stale preparation")

    monkeypatch.setattr(prepared_module, "resolve_session_with_assets", forbidden_resolver)
    monkeypatch.setattr(
        prepared_module,
        "tempfile",
        SimpleNamespace(TemporaryDirectory=forbidden_temp_dir),
    )

    cases = (
        (
            PreparedSessionErrorCode.STALE_SOURCE_REVISION,
            {"expected_source_revision": f"sha256:{'1' * 64}"},
        ),
        (
            PreparedSessionErrorCode.STALE_DOCUMENT_DIGEST,
            {"expected_document_digest": f"sha256:{'2' * 64}"},
        ),
        (
            PreparedSessionErrorCode.STALE_CLOSURE_DIGEST,
            {"expected_closure_digest": f"sha256:{'3' * 64}"},
        ),
    )
    for expected_code, override in cases:
        recording = _RecordingReadView(prepared_fixture.read_view, [])
        kwargs = {
            "expected_source_revision": prepared_fixture.source_revision,
            "expected_document_digest": closure.document_digest,
            "expected_closure_digest": closure.closure_digest,
            **override,
        }
        with pytest.raises(PreparedSessionError) as raised:
            prepare_session_files(
                prepared_fixture.root_yaml,
                recording,
                source=prepared_fixture.source,
                source_revision=prepared_fixture.source_revision,
                available_node_count=100,
                **kwargs,
            )

        assert raised.value.code is expected_code
        if expected_code is PreparedSessionErrorCode.STALE_SOURCE_REVISION:
            assert recording.refs == []
        else:
            assert recording.refs

    assert resolver_calls == []


def test_runtime_unsupported_feature_propagates_typed_support_evidence(
    prepared_fixture: PreparedFixture,
) -> None:
    root = yaml.safe_load(prepared_fixture.root_yaml)
    root["routing"] = {
        "domains": [
            {
                "id": "unsupported-bgp",
                "protocol": "bgp",
                "selectors": [{"any": [{"segment": "leo"}, {"segment": "ground"}]}],
            }
        ]
    }
    root_yaml = yaml.safe_dump(root, sort_keys=False).encode("utf-8")

    with pytest.raises(UnsupportedFeatureError) as raised:
        prepare_session_files(
            root_yaml,
            prepared_fixture.read_view,
            source=prepared_fixture.source,
            source_revision=_sha256(root_yaml),
            available_node_count=100,
        )

    assert any(
        feature.category is FeatureCategory.ROUTING_PROTOCOL and feature.value == "bgp"
        for feature in raised.value.features
    )


def test_readiness_errors_are_typed_and_block_return(
    prepared_fixture: PreparedFixture,
) -> None:
    constellation_ref = "user:constellations/prepared-constellation.yaml"
    constellation = yaml.safe_load(prepared_fixture.user_contents[constellation_ref])
    constellation["constellation"]["slots_per_plane"] = 1
    _write_user_ref(
        prepared_fixture.roots.user_root,
        constellation_ref,
        yaml.safe_dump(constellation, sort_keys=False).encode("utf-8"),
    )

    with pytest.raises(PreparedSessionError) as raised:
        prepare_session_files(
            prepared_fixture.root_yaml,
            prepared_fixture.read_view,
            source=prepared_fixture.source,
            source_revision=prepared_fixture.source_revision,
            available_node_count=100,
        )

    assert raised.value.code is PreparedSessionErrorCode.NOT_READY
    assert raised.value.validation_report is not None
    assert raised.value.validation_report.status == "invalid"
    assert raised.value.validation_report.dispatchable is False
    assert any(result.code == "E003" for result in raised.value.evidence.readiness_errors)


def test_resolved_semantic_digest_excludes_logical_source_and_run_provenance(
    prepared_fixture: PreparedFixture,
) -> None:
    first = _prepare(
        prepared_fixture,
        source=PreparedSessionSource(
            logical_id="user:sessions/prepared-user-deep.yaml",
            origin="test.semantic.first",
        ),
        run_id="run-first-0001",
    )
    second = _prepare(
        prepared_fixture,
        source=PreparedSessionSource(
            logical_id="user:sessions/semantic-second.yaml",
            origin="test.semantic.second",
        ),
        run_id="run-second-0002",
    )

    assert first.resolved_semantic_digest == second.resolved_semantic_digest
    assert first.document_digest == second.document_digest
    assert first.closure_digest == second.closure_digest
    assert first.resolution.resolved.source_context != second.resolution.resolved.source_context


@pytest.mark.parametrize(
    "failure,expected_code",
    [
        ("missing", CatalogClosureErrorCode.DANGLING_REFERENCE),
        ("corrupt", CatalogClosureErrorCode.INVALID_DEPENDENCY_YAML),
    ],
)
def test_missing_or_corrupt_dependency_fails_before_resolver_or_temp_mutation(
    prepared_fixture: PreparedFixture,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: CatalogClosureErrorCode,
) -> None:
    terminal_ref = "user:terminals/prepared-terminal.yaml"
    terminal_path = prepared_fixture.roots.user_root / CatalogRef(terminal_ref).relative_path
    if failure == "missing":
        terminal_path.unlink()
    else:
        terminal_path.write_bytes(b"terminal: [\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("preparation crossed the corrupt-closure boundary")

    monkeypatch.setattr(prepared_module, "resolve_session_with_assets", forbidden)
    monkeypatch.setattr(
        prepared_module,
        "tempfile",
        SimpleNamespace(TemporaryDirectory=forbidden),
    )

    with pytest.raises(CatalogClosureError) as raised:
        prepare_session_files(
            prepared_fixture.root_yaml,
            prepared_fixture.read_view,
            source=prepared_fixture.source,
            source_revision=prepared_fixture.source_revision,
            available_node_count=100,
        )

    assert raised.value.code is expected_code


def test_source_identity_rejects_non_session_refs_and_invalid_origin() -> None:
    with pytest.raises(ValueError):
        PreparedSessionSource(
            logical_id="/tmp/private/session.yaml",
            origin="test.prepared_session",
        )

    with pytest.raises(ValueError, match="non-whitespace logical token"):
        PreparedSessionSource(
            logical_id="user:sessions/import-0003.yaml",
            origin="   ",
        )
