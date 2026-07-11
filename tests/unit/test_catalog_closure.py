"""Tests for exact, typed persisted catalog dependency collection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_closure import (
    CatalogClosureCollector,
    CatalogClosureError,
    CatalogClosureErrorCode,
    FilesystemCatalogReadView,
)
from nodalarc.catalog_paths import CatalogRoots
from nodalarc.catalog_refs import CatalogRef

from tests.catalog_session_fixtures import ISS_TLE_LINE_1, ISS_TLE_LINE_2

ROOT = Path(__file__).resolve().parents[2]


def _yaml_bytes(document: dict[str, Any]) -> bytes:
    return yaml.safe_dump(document, sort_keys=False).encode("utf-8")


def _body_document(identifier: str = "earth") -> dict[str, Any]:
    return {
        "body": {
            "id": identifier,
            "display_name": identifier.title(),
            "gravitational_parameter_km3_s2": 398600.4418,
            "mean_radius_km": 6371.0088,
            "equatorial_radius_km": 6378.137,
            "polar_radius_km": 6356.752,
            "reference": "urn:nodalarc:test",
        }
    }


def _terminal_document(identifier: str = "demo-terminal") -> dict[str, Any]:
    return {
        "terminal": {
            "id": identifier,
            "display_name": "Demo terminal",
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
            "reference": "urn:nodalarc:test",
        }
    }


def _orbit_document() -> dict[str, Any]:
    return {
        "orbit": {
            "id": "demo-orbit",
            "central_body": "nodalarc:bodies/earth.yaml",
            "epoch": "2026-06-08T00:00:00Z",
            "shape": {"altitude_km": 550},
            "orientation": {
                "inclination_deg": 53,
                "raan_deg": 0,
                "argument_of_perigee_deg": 0,
            },
            "phase": {"mean_anomaly_deg": 0},
            "propagator": "j2_mean_elements",
            "reference": "urn:nodalarc:test",
        }
    }


def _node_document(
    terminal_refs: tuple[str, ...] = (
        "user:terminals/demo-terminal.yaml",
        "user:terminals/demo-terminal.yaml",
    ),
) -> dict[str, Any]:
    return {
        "node": {
            "id": "demo-node",
            "forwarding": "routed",
            "ethernet": [],
            "terminals": [
                {
                    "id": f"terminal-{index}",
                    "role": "access" if index == 0 else "isl",
                    "terminal": ref,
                    "count": 1,
                }
                for index, ref in enumerate(terminal_refs)
            ],
            "payloads": [],
            "notes": "Literal user:terminals/prose-only.yaml is not a dependency.",
        }
    }


def _constellation_document() -> dict[str, Any]:
    return {
        "constellation": {
            "id": "demo-constellation",
            "node": "user:nodes/demo-node.yaml",
            "orbit": "nodalarc:orbits/demo-orbit.yaml",
            "planes": {"count": 1, "raan_spacing_deg": 0},
            "slots_per_plane": 1,
            "phasing": {"mode": "evenly_spaced_mean_anomaly"},
            "node_tags": [],
        }
    }


def _session_document(
    source: str = "user:constellations/demo-constellation.yaml",
) -> dict[str, Any]:
    return {
        "session": {
            "name": "closure-test",
            "description": "Literal user:nodes/prose-only.yaml is not a dependency.",
        },
        "time": {
            "start_time": "2026-06-08T00:00:00Z",
            "step_seconds": 1,
            "compression": 1,
        },
        "segments": [{"id": "space", "source": source}],
        "ephemeris": {
            "provider": "skyfield_bsp",
            "quality_tier": "test",
            "kernels": [
                {
                    "id": "test-kernel",
                    "path": "configs/ephemerides/test.bsp",
                    "targets": ["nodalarc:bodies/earth.yaml"],
                    "frame": "gcrs",
                }
            ],
        },
    }


def _path_for(roots: CatalogRoots, ref: str) -> Path:
    parsed = CatalogRef(ref)
    root = roots.root if parsed.namespace == "nodalarc" else roots.user_root
    assert root is not None
    return root / parsed.relative_path


def _write_ref(
    roots: CatalogRoots,
    ref: str,
    *,
    document: dict[str, Any] | None = None,
    yaml_bytes: bytes | None = None,
) -> bytes:
    content = yaml_bytes if yaml_bytes is not None else _yaml_bytes(document or {})
    path = _path_for(roots, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


@dataclass(frozen=True)
class ClosureFixture:
    roots: CatalogRoots
    root_yaml: bytes
    contents: dict[str, bytes]


@pytest.fixture()
def closure_fixture(tmp_path: Path) -> ClosureFixture:
    shipped = tmp_path / "catalog" / "nodalarc"
    user = tmp_path / "catalog" / "user"
    shipped.mkdir(parents=True)
    user.mkdir(parents=True)
    roots = CatalogRoots.from_catalog_root(shipped, user_root=user)

    terminal_bytes = b"# exact bytes remain exact\n" + _yaml_bytes(_terminal_document())
    documents = {
        "nodalarc:bodies/earth.yaml": _write_ref(
            roots,
            "nodalarc:bodies/earth.yaml",
            document=_body_document(),
        ),
        "nodalarc:orbits/demo-orbit.yaml": _write_ref(
            roots,
            "nodalarc:orbits/demo-orbit.yaml",
            document=_orbit_document(),
        ),
        "user:terminals/demo-terminal.yaml": _write_ref(
            roots,
            "user:terminals/demo-terminal.yaml",
            yaml_bytes=terminal_bytes,
        ),
        "user:nodes/demo-node.yaml": _write_ref(
            roots,
            "user:nodes/demo-node.yaml",
            document=_node_document(),
        ),
        "user:constellations/demo-constellation.yaml": _write_ref(
            roots,
            "user:constellations/demo-constellation.yaml",
            document=_constellation_document(),
        ),
    }
    _write_ref(
        roots,
        "user:terminals/prose-only.yaml",
        document=_terminal_document("prose-only"),
    )
    _write_ref(
        roots,
        "nodalarc:bodies/unrelated.yaml",
        document=_body_document("unrelated"),
    )
    return ClosureFixture(
        roots=roots,
        root_yaml=_yaml_bytes(_session_document()),
        contents=documents,
    )


def _collect(fixture: ClosureFixture):
    return CatalogClosureCollector.collect(
        fixture.root_yaml,
        FilesystemCatalogReadView(fixture.roots),
    )


def test_collects_exact_direct_and_nested_mixed_namespace_dependencies_only(
    closure_fixture: ClosureFixture,
) -> None:
    closure = _collect(closure_fixture)
    refs = tuple(str(entry.ref) for entry in closure.entries)

    assert {field.name for field in fields(closure)} == {
        "root_yaml",
        "entries",
        "document_digest",
        "closure_digest",
        "file_count",
        "total_bytes",
    }
    assert refs == tuple(sorted(closure_fixture.contents))
    assert all(isinstance(entry.ref, CatalogRef) for entry in closure.entries)
    assert closure.root_yaml == closure_fixture.root_yaml
    assert (
        closure.document_digest == "sha256:" + hashlib.sha256(closure_fixture.root_yaml).hexdigest()
    )
    assert closure.file_count == len(closure_fixture.contents)
    assert closure.total_bytes == sum(map(len, closure_fixture.contents.values()))
    assert closure.deployment_file_count == len(closure_fixture.contents) + 1
    assert closure.deployment_total_bytes == len(closure_fixture.root_yaml) + sum(
        map(len, closure_fixture.contents.values())
    )

    by_ref = {str(entry.ref): entry for entry in closure.entries}
    for ref, expected_bytes in closure_fixture.contents.items():
        entry = by_ref[ref]
        assert entry.yaml_bytes == expected_bytes
        assert entry.document_digest == "sha256:" + hashlib.sha256(expected_bytes).hexdigest()
        assert entry.preserved_path == "catalog/" + ref.replace(":", "/", 1)

    assert "user:terminals/prose-only.yaml" not in by_ref
    assert "nodalarc:bodies/unrelated.yaml" not in by_ref


def test_collects_nested_body_and_node_refs_from_canonical_tle_placement(
    closure_fixture: ClosureFixture,
) -> None:
    tle_ref = "user:space-node-sets/demo-tle.yaml"
    tle_bytes = _write_ref(
        closure_fixture.roots,
        tle_ref,
        document={
            "space_node_set": {
                "id": "demo-tle",
                "nodes": [
                    {
                        "id": "iss",
                        "node": "user:nodes/demo-node.yaml",
                        "sgp4_tle": {
                            "central_body": "nodalarc:bodies/earth.yaml",
                            "line_1": ISS_TLE_LINE_1,
                            "line_2": ISS_TLE_LINE_2,
                        },
                    }
                ],
            }
        },
    )
    root_yaml = _yaml_bytes(_session_document(tle_ref))

    closure = CatalogClosureCollector.collect(
        root_yaml,
        FilesystemCatalogReadView(closure_fixture.roots),
    )
    entries = {str(entry.ref): entry for entry in closure.entries}

    assert set(entries) == {
        "nodalarc:bodies/earth.yaml",
        "user:terminals/demo-terminal.yaml",
        "user:nodes/demo-node.yaml",
        tle_ref,
    }
    assert entries[tle_ref].yaml_bytes == tle_bytes
    assert ISS_TLE_LINE_1.encode() in entries[tle_ref].yaml_bytes


def test_repeated_dependencies_dedupe_with_deterministic_order_and_digest(
    closure_fixture: ClosureFixture,
) -> None:
    first = _collect(closure_fixture)
    second = _collect(closure_fixture)

    assert [str(entry.ref) for entry in first.entries] == sorted(
        str(entry.ref) for entry in first.entries
    )
    assert sum(str(entry.ref) == "nodalarc:bodies/earth.yaml" for entry in first.entries) == 1
    assert (
        sum(str(entry.ref) == "user:terminals/demo-terminal.yaml" for entry in first.entries) == 1
    )
    assert first.entries == second.entries
    assert first.closure_digest == second.closure_digest
    assert first.closure_digest.startswith("sha256:")
    assert len(first.closure_digest) == len("sha256:") + 64


def test_collect_references_validates_union_graph_without_a_session_root(
    closure_fixture: ClosureFixture,
) -> None:
    graph = CatalogClosureCollector.collect_references(
        (
            "user:constellations/demo-constellation.yaml",
            "user:nodes/demo-node.yaml",
        ),
        FilesystemCatalogReadView(closure_fixture.roots),
    )

    assert [str(entry.ref) for entry in graph.entries] == sorted(closure_fixture.contents)
    assert graph.file_count == len(graph.entries)
    assert graph.total_bytes == sum(entry.size_bytes for entry in graph.entries)
    assert graph.closure_digest.startswith("sha256:")


def test_dangling_reference_has_typed_chain_evidence(closure_fixture: ClosureFixture) -> None:
    root_yaml = _yaml_bytes(_session_document("user:constellations/missing.yaml"))

    with pytest.raises(CatalogClosureError) as raised:
        CatalogClosureCollector.collect(
            root_yaml,
            FilesystemCatalogReadView(closure_fixture.roots),
        )

    assert raised.value.code is CatalogClosureErrorCode.DANGLING_REFERENCE
    assert raised.value.evidence.ref == "user:constellations/missing.yaml"
    assert raised.value.evidence.dependency_chain == ("user:constellations/missing.yaml",)
    assert raised.value.evidence.cause_type == "FileNotFoundError"


def test_wrong_reference_family_fails_before_reading(closure_fixture: ClosureFixture) -> None:
    root_yaml = _yaml_bytes(_session_document("user:nodes/demo-node.yaml"))

    with pytest.raises(CatalogClosureError) as raised:
        CatalogClosureCollector.collect(
            root_yaml,
            FilesystemCatalogReadView(closure_fixture.roots),
        )

    assert raised.value.code is CatalogClosureErrorCode.REFERENCE_FAMILY_MISMATCH
    assert raised.value.evidence.ref == "user:nodes/demo-node.yaml"


def test_wrong_document_wrapper_reports_family_evidence(closure_fixture: ClosureFixture) -> None:
    _write_ref(
        closure_fixture.roots,
        "user:constellations/demo-constellation.yaml",
        document=_node_document(),
    )

    with pytest.raises(CatalogClosureError) as raised:
        _collect(closure_fixture)

    assert raised.value.code is CatalogClosureErrorCode.FAMILY_WRAPPER_MISMATCH
    assert raised.value.evidence.ref == "user:constellations/demo-constellation.yaml"
    assert raised.value.evidence.family == "constellations"


def test_traversal_reference_is_rejected_as_typed_path_evidence(
    closure_fixture: ClosureFixture,
) -> None:
    root_yaml = _yaml_bytes(_session_document("user:constellations/../escape.yaml"))

    with pytest.raises(CatalogClosureError) as raised:
        CatalogClosureCollector.collect(
            root_yaml,
            FilesystemCatalogReadView(closure_fixture.roots),
        )

    assert raised.value.code is CatalogClosureErrorCode.REFERENCE_PATH_REJECTED
    assert raised.value.evidence.ref == "user:constellations/../escape.yaml"


def test_filesystem_read_view_rejects_symlink_escape(
    closure_fixture: ClosureFixture,
    tmp_path: Path,
) -> None:
    target = _path_for(
        closure_fixture.roots,
        "user:constellations/demo-constellation.yaml",
    )
    outside = tmp_path / "outside-constellation.yaml"
    outside.write_bytes(_yaml_bytes(_constellation_document()))
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(CatalogClosureError) as raised:
        _collect(closure_fixture)

    assert raised.value.code is CatalogClosureErrorCode.REFERENCE_PATH_REJECTED
    assert raised.value.evidence.ref == "user:constellations/demo-constellation.yaml"
    assert raised.value.evidence.cause_type == "CatalogPathError"


def test_yml_suffix_is_preserved_as_a_catalog_identity(
    closure_fixture: ClosureFixture,
) -> None:
    _write_ref(
        closure_fixture.roots,
        "user:terminals/demo-terminal.yml",
        document=_terminal_document(),
    )
    _write_ref(
        closure_fixture.roots,
        "user:nodes/demo-node.yaml",
        document=_node_document(
            (
                "user:terminals/demo-terminal.yml",
                "user:terminals/demo-terminal.yml",
            )
        ),
    )

    closure = _collect(closure_fixture)

    terminal_entries = [
        entry for entry in closure.entries if str(entry.ref) == "user:terminals/demo-terminal.yml"
    ]
    assert len(terminal_entries) == 1
    assert terminal_entries[0].preserved_path == "catalog/user/terminals/demo-terminal.yml"


def test_session_root_must_be_the_unwrapped_strict_persisted_document(
    closure_fixture: ClosureFixture,
) -> None:
    wrapped = _yaml_bytes({"catalog_session": _session_document()})

    with pytest.raises(CatalogClosureError) as raised:
        CatalogClosureCollector.collect(
            wrapped,
            FilesystemCatalogReadView(closure_fixture.roots),
        )

    assert raised.value.code is CatalogClosureErrorCode.INVALID_SESSION_ROOT


@pytest.mark.parametrize(
    "session_path",
    sorted((ROOT / "catalog" / "nodalarc" / "sessions").glob("*.yaml")),
    ids=lambda path: path.name,
)
def test_every_shipped_session_has_a_complete_strict_exact_byte_closure(
    session_path: Path,
) -> None:
    catalog_root = ROOT / "catalog" / "nodalarc"
    root_yaml = session_path.read_bytes()

    closure = CatalogClosureCollector.collect(
        root_yaml,
        FilesystemCatalogReadView(CatalogRoots.from_catalog_root(catalog_root)),
    )

    assert closure.root_yaml == root_yaml
    assert closure.file_count == len(closure.entries)
    assert closure.total_bytes == sum(entry.size_bytes for entry in closure.entries)
    assert all(entry.preserved_path.startswith("catalog/nodalarc/") for entry in closure.entries)
