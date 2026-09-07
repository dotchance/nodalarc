"""The catalog session listing: one collection per entry, digests on every row."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_closure import CatalogClosureCollector
from nodalarc.catalog_refs import CatalogRef
from vs_api import catalog_session_service as service_module
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.catalog_context import create_catalog_context
from vs_api.catalog_session_service import CatalogSessionService

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SIMPLE_SESSION = SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml"


def _context(tmp_path: Path):
    return create_catalog_context(session_data_root=tmp_path, shipped_root=SHIPPED_ROOT)


def _write_user_session(context, name: str, document: dict) -> str:
    ref = CatalogRef(f"user:sessions/{name}.yaml")
    canonical = canonicalize_persisted_configuration(ref, document)
    transaction = context.repository.begin(context.scope)
    transaction.write_bytes(ref, canonical.yaml_bytes, expected_revision=None)
    transaction.commit()
    return str(ref)


def test_listing_collects_each_session_once_and_keeps_digests_on_blocked_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    document = yaml.safe_load(SIMPLE_SESSION.read_text(encoding="utf-8"))
    document["session"]["name"] = "blocked-by-selector"
    document["link_rules"][0]["endpoints"][0]["select"] = {"tag": "matches-nothing"}
    blocked_ref = _write_user_session(context, "blocked-by-selector", document)

    real_collect = CatalogClosureCollector.collect
    collected: list[str] = []

    def counting_collect(root_yaml, view):
        closure = real_collect(root_yaml, view)
        collected.append(closure.document_digest)
        return closure

    monkeypatch.setattr(
        service_module.CatalogClosureCollector, "collect", staticmethod(counting_collect)
    )

    summaries = CatalogSessionService(context).list_sessions(
        active_session_ref=None, available_node_count=1_000_000
    )

    by_ref = {str(summary.source_id.session_ref): summary for summary in summaries}
    assert len(collected) == len(summaries)
    assert len(set(collected)) == len(summaries)

    blocked = by_ref[blocked_ref]
    assert blocked.deploy_allowed is False
    assert blocked.blockers
    assert blocked.document_digest is not None
    assert blocked.dependency_digest is not None

    shipped = by_ref["nodalarc:sessions/earth-leo-simple.yaml"]
    assert shipped.deploy_allowed is True
    assert shipped.document_digest is not None
