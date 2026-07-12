"""Tests for the scoped atomic filesystem catalog repository."""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import CatalogClosureError, CatalogClosureErrorCode
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import (
    CatalogConflictError,
    CatalogContainmentError,
    CatalogNotFoundError,
    CatalogReadOnlyError,
    CatalogScope,
    CatalogTransactionOrderError,
    CatalogValidationError,
    UnknownCatalogScopeError,
)
from nodalarc.filesystem_catalog_repository import (
    FilesystemCatalogRepository,
    catalog_content_revision,
)


def _node_document(object_id: str, *, display_name: str | None = None) -> bytes:
    display = f"  display_name: {display_name}\n" if display_name is not None else ""
    return (
        "# exact authored bytes are preserved\n"
        "node:\n"
        f"  id: {object_id}\n"
        f"{display}"
        "  forwarding: routed\n"
        "  ethernet: []\n"
        "  terminals: []\n"
        "  payloads: []\n"
    ).encode()


def _session_document(name: str) -> bytes:
    return (
        "session:\n"
        f"  name: {name}\n"
        "  display_name: Repository test session\n"
        "segments:\n"
        "- id: leo\n"
        "  source: nodalarc:constellations/test-constellation.yaml\n"
        "time:\n"
        "  start_time: '2026-06-08T00:00:00Z'\n"
        "  step_seconds: 1\n"
        "  compression: 1\n"
    ).encode()


def _yaml_bytes(document: dict[str, Any]) -> bytes:
    return yaml.safe_dump(document, sort_keys=False).encode()


def _terminal_document(object_id: str) -> bytes:
    return _yaml_bytes(
        {
            "terminal": {
                "id": object_id,
                "display_name": object_id,
                "medium": "optical",
                "signal": {"wavelength_nm": 1550},
                "bandwidth_mbps": {"transmit": 1000, "receive": 1000},
                "tracking_capacity": 1,
                "max_range_km": 5000,
                "limits": {
                    "azimuth_deg": {"min": -180, "max": 180},
                    "elevation_deg": {"min": -90, "max": 90},
                    "max_tracking_rate_deg_s": 1,
                },
                "reference": "urn:nodalarc:repository-test",
            }
        }
    )


def _node_with_terminal(object_id: str, terminal_ref: str) -> bytes:
    return _yaml_bytes(
        {
            "node": {
                "id": object_id,
                "forwarding": "routed",
                "ethernet": [],
                "terminals": [
                    {
                        "id": "access",
                        "role": "access",
                        "terminal": terminal_ref,
                        "count": 1,
                    }
                ],
                "payloads": [],
            }
        }
    )


def _seed_session_dependencies(shipped_root: Path) -> None:
    _write_document(
        shipped_root,
        "bodies/test-body.yaml",
        _yaml_bytes(
            {
                "body": {
                    "id": "test-body",
                    "display_name": "Test body",
                    "gravitational_parameter_km3_s2": 398600.4418,
                    "mean_radius_km": 6371.0088,
                    "equatorial_radius_km": 6378.137,
                    "polar_radius_km": 6356.752,
                    "reference": "urn:nodalarc:repository-test",
                }
            }
        ),
    )
    _write_document(shipped_root, "nodes/test-node.yaml", _node_document("test-node"))
    _write_document(
        shipped_root,
        "orbits/test-orbit.yaml",
        _yaml_bytes(
            {
                "orbit": {
                    "id": "test-orbit",
                    "central_body": "nodalarc:bodies/test-body.yaml",
                    "epoch": "2026-06-08T00:00:00Z",
                    "shape": {"altitude_km": 550},
                    "orientation": {
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "argument_of_perigee_deg": 0,
                    },
                    "phase": {"mean_anomaly_deg": 0},
                    "propagator": "j2_mean_elements",
                    "reference": "urn:nodalarc:repository-test",
                }
            }
        ),
    )
    _write_document(
        shipped_root,
        "constellations/test-constellation.yaml",
        _yaml_bytes(
            {
                "constellation": {
                    "id": "test-constellation",
                    "node": "nodalarc:nodes/test-node.yaml",
                    "orbit": "nodalarc:orbits/test-orbit.yaml",
                    "planes": {"count": 1, "raan_spacing_deg": 0},
                    "slots_per_plane": 1,
                    "phasing": {"mode": "evenly_spaced_mean_anomaly"},
                    "node_tags": [],
                }
            }
        ),
    )


def _write_document(root: Path, relative: str, content: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _repository(
    tmp_path: Path,
    *,
    scopes: int = 1,
) -> tuple[FilesystemCatalogRepository, Path, tuple[CatalogScope, ...], tuple[Path, ...]]:
    shipped_root = tmp_path / "shipped"
    shipped_root.mkdir()
    catalog_scopes = tuple(CatalogScope() for _ in range(scopes))
    user_roots = tuple(tmp_path / f"scope-{index}" for index in range(scopes))
    repository = FilesystemCatalogRepository(
        shipped_root=shipped_root,
        scope_roots=dict(zip(catalog_scopes, user_roots, strict=True)),
    )
    return repository, shipped_root, catalog_scopes, user_roots


def _create(
    repository: FilesystemCatalogRepository,
    scope: CatalogScope,
    ref: str,
    content: bytes,
):
    transaction = repository.begin(scope)
    transaction.write_bytes(ref, content, expected_revision=None)
    return transaction.commit()


def test_exact_bytes_and_revision_survive_repository_reopen(tmp_path: Path) -> None:
    repository, shipped_root, (scope,), (user_root,) = _repository(tmp_path)
    content = _node_document("router", display_name="Router  with  deliberate  spacing")

    committed = _create(repository, scope, "user:nodes/router.yaml", content)
    document = committed.get("user:nodes/router.yaml")

    assert committed.read_bytes("user:nodes/router.yaml") == content
    assert document.content == content
    assert document.revision == catalog_content_revision(content)
    assert document.revision.startswith("sha256:")
    closure_document = committed.read(CatalogRef("user:nodes/router.yaml"))
    assert closure_document.yaml_bytes == content
    assert closure_document.preserved_path == "catalog/user/nodes/router.yaml"

    reopened_repository = FilesystemCatalogRepository(
        shipped_root=shipped_root,
        scope_roots={scope: user_root},
    )
    reopened = reopened_repository.snapshot(scope)

    assert reopened.generation == committed.generation
    assert reopened.read_bytes("user:nodes/router.yaml") == content
    assert reopened.get("user:nodes/router.yaml").revision == document.revision


def test_create_collision_and_stale_write_delete_are_rejected(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    first = _node_document("router", display_name="first")
    second = _node_document("router", display_name="second")
    created = _create(repository, scope, "user:nodes/router.yaml", first)
    first_revision = created.get("user:nodes/router.yaml").revision

    collision = repository.begin(scope)
    collision.write_bytes("user:nodes/router.yaml", second, expected_revision=None)
    with pytest.raises(CatalogConflictError, match="already exists"):
        collision.commit()

    stale_write = repository.begin(scope)
    stale_write.write_bytes(
        "user:nodes/router.yaml",
        second,
        expected_revision="sha256:" + "0" * 64,
    )
    with pytest.raises(CatalogConflictError, match="stale write"):
        stale_write.commit()

    updated_transaction = repository.begin(scope)
    updated_transaction.write_bytes(
        "user:nodes/router.yaml",
        second,
        expected_revision=first_revision,
    )
    updated = updated_transaction.commit()
    second_revision = updated.get("user:nodes/router.yaml").revision

    stale_delete = repository.begin(scope)
    stale_delete.delete("user:nodes/router.yaml", expected_revision=first_revision)
    with pytest.raises(CatalogConflictError, match="stale delete"):
        stale_delete.commit()

    delete = repository.begin(scope)
    delete.delete("user:nodes/router.yaml", expected_revision=second_revision)
    deleted = delete.commit()
    with pytest.raises(CatalogNotFoundError):
        deleted.get("user:nodes/router.yaml")


def test_shipped_catalog_mutation_is_refused_before_commit(tmp_path: Path) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    shipped = _node_document("router")
    _write_document(shipped_root, "nodes/router.yaml", shipped)

    assert repository.snapshot(scope).read_bytes("nodalarc:nodes/router.yaml") == shipped

    transaction = repository.begin(scope)
    with pytest.raises(CatalogReadOnlyError, match="read-only"):
        transaction.write_bytes(
            "nodalarc:nodes/router.yaml",
            _node_document("router", display_name="mutated"),
            expected_revision=catalog_content_revision(shipped),
        )
    with pytest.raises(CatalogReadOnlyError, match="read-only"):
        transaction.delete(
            "nodalarc:nodes/router.yaml",
            expected_revision=catalog_content_revision(shipped),
        )


def test_two_scopes_isolate_identical_refs_without_fallback(tmp_path: Path) -> None:
    repository, _shipped_root, (scope_a, scope_b), _user_roots = _repository(
        tmp_path,
        scopes=2,
    )
    ref = "user:nodes/router.yaml"
    content_a = _node_document("router", display_name="scope-a")
    content_b = _node_document("router", display_name="scope-b")

    snapshot_a = _create(repository, scope_a, ref, content_a)
    snapshot_b = _create(repository, scope_b, ref, content_b)
    _create(repository, scope_a, "user:nodes/only-a.yaml", _node_document("only-a"))

    assert snapshot_a.read_bytes(ref) == content_a
    assert snapshot_b.read_bytes(ref) == content_b
    with pytest.raises(CatalogNotFoundError):
        repository.snapshot(scope_b).get("user:nodes/only-a.yaml")
    with pytest.raises(UnknownCatalogScopeError):
        repository.snapshot(CatalogScope())


@pytest.mark.parametrize(
    "ref,content,error",
    [
        (
            "user:nodes/filename.yaml",
            _node_document("different-id"),
            "object id.*must match filename",
        ),
        (
            "user:sessions/filename.yaml",
            _session_document("different-name"),
            "session.name.*must match filename",
        ),
    ],
)
def test_catalog_identity_must_match_filename(
    tmp_path: Path,
    ref: str,
    content: bytes,
    error: str,
) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    if ref.startswith("user:sessions/"):
        _seed_session_dependencies(shipped_root)
    generation = repository.snapshot(scope).generation
    transaction = repository.begin(scope)
    transaction.write_bytes(ref, content, expected_revision=None)

    with pytest.raises(CatalogValidationError, match=error):
        transaction.commit()

    assert repository.snapshot(scope).generation == generation


def test_registry_enforces_wrapped_objects_and_unwrapped_sessions(tmp_path: Path) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    _seed_session_dependencies(shipped_root)

    unwrapped_node = repository.begin(scope)
    unwrapped_node.write_bytes(
        "user:nodes/router.yaml",
        b"id: router\nforwarding: routed\nethernet: []\nterminals: []\npayloads: []\n",
        expected_revision=None,
    )
    with pytest.raises(CatalogValidationError, match="top-level object wrapper"):
        unwrapped_node.commit()

    wrapped_session = repository.begin(scope)
    wrapped_session.write_bytes(
        "user:sessions/demo.yaml",
        b"session_document:\n  session:\n    name: demo\n  segments: []\n",
        expected_revision=None,
    )
    with pytest.raises(CatalogValidationError):
        wrapped_session.commit()

    valid = _create(repository, scope, "user:sessions/demo.yaml", _session_document("demo"))
    assert valid.read_bytes("user:sessions/demo.yaml") == _session_document("demo")


def test_orphan_object_with_dangling_reference_is_refused(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:nodes/orphan.yaml",
        _node_with_terminal("orphan", "user:terminals/missing.yaml"),
        expected_revision=None,
    )

    with pytest.raises(CatalogValidationError, match="dangling_reference") as raised:
        transaction.commit()

    cause = raised.value.__cause__
    assert isinstance(cause, CatalogClosureError)
    assert cause.code is CatalogClosureErrorCode.DANGLING_REFERENCE
    with pytest.raises(CatalogNotFoundError):
        repository.snapshot(scope).get("user:nodes/orphan.yaml")


def test_same_transaction_dependency_creation_succeeds(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:terminals/access.yaml",
        _terminal_document("access"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:nodes/router.yaml",
        _node_with_terminal("router", "user:terminals/access.yaml"),
        expected_revision=None,
    )

    committed = transaction.commit()

    assert committed.get("user:nodes/router.yaml").family == "nodes"
    assert committed.get("user:terminals/access.yaml").family == "terminals"


def test_delete_of_referenced_dependency_is_refused_atomically(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:terminals/access.yaml",
        _terminal_document("access"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:nodes/router.yaml",
        _node_with_terminal("router", "user:terminals/access.yaml"),
        expected_revision=None,
    )
    baseline = transaction.commit()
    terminal = baseline.get("user:terminals/access.yaml")

    deletion = repository.begin(scope)
    deletion.delete("user:terminals/access.yaml", expected_revision=terminal.revision)
    with pytest.raises(CatalogValidationError, match="dangling_reference"):
        deletion.commit()

    current = repository.snapshot(scope)
    assert current.generation == baseline.generation
    assert current.read_bytes("user:terminals/access.yaml") == terminal.content
    assert current.get("user:nodes/router.yaml").family == "nodes"


def test_failed_multi_document_commit_exposes_no_write_or_delete(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), (user_root,) = _repository(tmp_path)
    baseline = _create(
        repository,
        scope,
        "user:nodes/existing.yaml",
        _node_document("existing"),
    )
    baseline_document = baseline.get("user:nodes/existing.yaml")
    current_before = (user_root / "CURRENT").read_bytes()
    generations_before = tuple(sorted(path.name for path in (user_root / "generations").iterdir()))

    transaction = repository.begin(scope)
    transaction.delete(
        "user:nodes/existing.yaml",
        expected_revision=baseline_document.revision,
    )
    transaction.write_bytes(
        "user:nodes/new.yaml",
        _node_document("new"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:nodes/broken.yaml",
        _node_document("wrong-id"),
        expected_revision=None,
    )

    with pytest.raises(CatalogValidationError, match="must match filename"):
        transaction.commit()

    current = repository.snapshot(scope)
    assert current.generation == baseline.generation
    assert current.read_bytes("user:nodes/existing.yaml") == baseline_document.content
    with pytest.raises(CatalogNotFoundError):
        current.get("user:nodes/new.yaml")
    with pytest.raises(CatalogNotFoundError):
        current.get("user:nodes/broken.yaml")
    assert (user_root / "CURRENT").read_bytes() == current_before
    assert tuple(sorted(path.name for path in (user_root / "generations").iterdir())) == (
        generations_before
    )


def test_multiple_session_publications_may_finish_one_atomic_transaction(
    tmp_path: Path,
) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    _seed_session_dependencies(shipped_root)

    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:nodes/migrated.yaml",
        _node_document("migrated"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:sessions/first.yaml",
        _session_document("first"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:sessions/second.yaml",
        _session_document("second"),
        expected_revision=None,
    )

    committed = transaction.commit()

    assert committed.read_bytes("user:nodes/migrated.yaml") == _node_document("migrated")
    assert committed.read_bytes("user:sessions/first.yaml") == _session_document("first")
    assert committed.read_bytes("user:sessions/second.yaml") == _session_document("second")


def test_component_mutation_after_session_publication_remains_rejected(
    tmp_path: Path,
) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    _seed_session_dependencies(shipped_root)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:sessions/first.yaml",
        _session_document("first"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:sessions/second.yaml",
        _session_document("second"),
        expected_revision=None,
    )

    with pytest.raises(CatalogTransactionOrderError, match="final logical"):
        transaction.write_bytes(
            "user:nodes/late.yaml",
            _node_document("late"),
            expected_revision=None,
        )


def test_noncanonical_yaml_suffix_is_rejected_before_transaction_mutation(
    tmp_path: Path,
) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    _seed_session_dependencies(shipped_root)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:sessions/alias-demo.yaml",
        _session_document("alias-demo"),
        expected_revision=None,
    )

    with pytest.raises(CatalogValidationError, match="path must be YAML"):
        transaction.write_bytes(
            "user:sessions/alias-demo.YAML",
            _session_document("alias-demo"),
            expected_revision=None,
        )


def test_reader_remains_pinned_across_later_commit(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), (user_root,) = _repository(tmp_path)
    first = _node_document("router", display_name="first")
    second = _node_document("router", display_name="second")
    pinned = _create(repository, scope, "user:nodes/router.yaml", first)
    revision = pinned.get("user:nodes/router.yaml").revision

    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:nodes/router.yaml",
        second,
        expected_revision=revision,
    )
    current = transaction.commit()

    assert pinned.generation != current.generation
    assert pinned.read_bytes("user:nodes/router.yaml") == first
    assert current.read_bytes("user:nodes/router.yaml") == second
    pinned_directory = user_root / "generations" / str(pinned.generation).removeprefix("sha256:")
    assert pinned_directory.is_dir()

    del pinned
    gc.collect()

    assert not pinned_directory.exists()
    assert repository.generation_retention == "current-plus-live-snapshots"


def test_stale_base_generation_fences_a_second_in_process_writer(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    first = repository.begin(scope)
    second = repository.begin(scope)
    first.write_bytes("user:nodes/first.yaml", _node_document("first"), expected_revision=None)
    second.write_bytes("user:nodes/second.yaml", _node_document("second"), expected_revision=None)

    first.commit()
    with pytest.raises(CatalogConflictError, match="generation is stale"):
        second.commit()

    assert repository.writer_coordination == "single-process"


def test_listing_and_export_are_deterministic(tmp_path: Path) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    _write_document(shipped_root, "nodes/z-shipped.yaml", _node_document("z-shipped"))
    _write_document(shipped_root, "nodes/a-shipped.yaml", _node_document("a-shipped"))

    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:nodes/z-user.yaml",
        _node_document("z-user"),
        expected_revision=None,
    )
    transaction.write_bytes(
        "user:nodes/a-user.yaml",
        _node_document("a-user"),
        expected_revision=None,
    )
    snapshot = transaction.commit()

    refs = tuple(str(entry.ref) for entry in snapshot.list())
    exported = snapshot.export_documents()

    assert refs == tuple(sorted(refs))
    assert tuple(str(document.ref) for document in exported) == refs
    assert snapshot.list() == snapshot.list()
    assert snapshot.export_documents() == exported
    assert tuple(str(entry.ref) for entry in snapshot.list(namespace="user")) == (
        "user:nodes/a-user.yaml",
        "user:nodes/z-user.yaml",
    )
    assert tuple(str(entry.ref) for entry in snapshot.list(family="nodes")) == refs


def test_session_publication_is_the_final_logical_mutation(tmp_path: Path) -> None:
    repository, _shipped_root, (scope,), _user_roots = _repository(tmp_path)
    transaction = repository.begin(scope)
    transaction.write_bytes(
        "user:sessions/demo.yaml",
        _session_document("demo"),
        expected_revision=None,
    )

    with pytest.raises(CatalogTransactionOrderError, match="final logical"):
        transaction.write_bytes(
            "user:nodes/router.yaml",
            _node_document("router"),
            expected_revision=None,
        )


def test_symlinks_and_traversal_cannot_escape_injected_roots(tmp_path: Path) -> None:
    repository, shipped_root, (scope,), _user_roots = _repository(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_bytes(_node_document("escape"))
    symlink = shipped_root / "nodes" / "escape.yaml"
    symlink.parent.mkdir()
    symlink.symlink_to(outside)

    with pytest.raises(CatalogContainmentError, match="symlink"):
        repository.snapshot(scope).read_bytes("nodalarc:nodes/escape.yaml")
    with pytest.raises(CatalogValidationError, match="invalid catalog reference"):
        repository.snapshot(scope).read_bytes("user:nodes/../outside.yaml")

    transaction = repository.begin(scope)
    with pytest.raises(CatalogValidationError, match="invalid catalog reference"):
        transaction.write_bytes(
            "user:nodes/../outside.yaml",
            _node_document("outside"),
            expected_revision=None,
        )

    real_scope_root = tmp_path / "real-scope"
    real_scope_root.mkdir()
    linked_scope_root = tmp_path / "linked-scope"
    linked_scope_root.symlink_to(real_scope_root, target_is_directory=True)
    with pytest.raises(CatalogContainmentError, match="must not be symlinks"):
        FilesystemCatalogRepository(
            shipped_root=shipped_root,
            scope_roots={CatalogScope(): linked_scope_root},
        )
