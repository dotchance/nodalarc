"""Catalog transactions at small size and explicit qualification scale."""

from __future__ import annotations

import gc
import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from vs_api.builder_compiler import canonicalize_persisted_configuration

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"


def _generation_directories(scope_root: Path) -> list[Path]:
    return [path for path in (scope_root / "generations").iterdir() if path.is_dir()]


@pytest.mark.parametrize(
    "artifact_count",
    [
        pytest.param(2, id="small"),
        pytest.param(
            200,
            id="scale",
            marks=pytest.mark.skipif(
                os.environ.get("NODALARC_PERF") != "1",
                reason="catalog scale qualification runs with make perf-test (NODALARC_PERF=1)",
            ),
        ),
    ],
)
def test_repository_transaction_preserves_documents_and_collects_generations(
    tmp_path: Path,
    artifact_count: int,
) -> None:
    scope = CatalogScope()
    scope_root = tmp_path / "user-catalog"
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: scope_root},
    )
    base_snapshot = repository.snapshot(scope)
    transaction = repository.begin(scope, base_generation=base_snapshot.generation)

    terminal_source = next((SHIPPED_ROOT / "terminals").rglob("*.yaml"))
    terminal_template = yaml.safe_load(terminal_source.read_text(encoding="utf-8"))
    session_template = yaml.safe_load(
        (SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_text(encoding="utf-8")
    )

    for index in range(artifact_count):
        terminal_id = f"scale-terminal-{index:03d}"
        terminal_ref = CatalogRef(f"user:terminals/scale/{terminal_id}.yaml")
        terminal = deepcopy(terminal_template)
        terminal["terminal"]["id"] = terminal_id
        terminal["terminal"]["display_name"] = f"Scale terminal {index}"
        canonical = canonicalize_persisted_configuration(terminal_ref, terminal)
        transaction.write_bytes(terminal_ref, canonical.yaml_bytes, expected_revision=None)

    for index in range(artifact_count):
        session_id = f"scale-session-{index:03d}"
        session_ref = CatalogRef(f"user:sessions/scale/{session_id}.yaml")
        session = deepcopy(session_template)
        session["session"]["name"] = session_id
        session["session"]["display_name"] = f"Scale session {index}"
        canonical = canonicalize_persisted_configuration(session_ref, session)
        transaction.write_bytes(session_ref, canonical.yaml_bytes, expected_revision=None)

    committed = transaction.commit()

    assert len(committed.list(namespace="user", family="terminals")) == artifact_count
    assert len(committed.list(namespace="user", family="sessions")) == artifact_count
    assert committed.get(f"user:sessions/scale/scale-session-{artifact_count - 1:03d}.yaml").content

    # Copy-on-write cleanup retains only CURRENT plus generations pinned by a
    # live snapshot; releasing that snapshot reclaims the old generation.
    assert len(_generation_directories(scope_root)) == 2
    del base_snapshot
    gc.collect()
    assert len(_generation_directories(scope_root)) == 1
