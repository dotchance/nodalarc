from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from vs_api.catalog_context import (
    CatalogContext,
    override_catalog_context_for_testing,
    reset_catalog_context_for_testing,
)

from tests.asgi_client import ASGITestClient as TestClient

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"


@pytest.fixture()
def catalog_context(tmp_path: Path):
    scope = CatalogScope()
    context = CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )
    override_catalog_context_for_testing(context)
    try:
        yield context
    finally:
        reset_catalog_context_for_testing()


def _draft_with_nested_user_refs() -> dict:
    session = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_bytes())
    session["session"]["name"] = "nested-user-deploy"
    session["segments"][0]["source"] = "user:constellations/nested-user-ring.yaml"

    constellation = yaml.safe_load(
        (SHIPPED_ROOT / "constellations/earth/leo/earth-leo-ring-36.yaml").read_bytes()
    )
    constellation = deepcopy(constellation)
    constellation["constellation"]["id"] = "nested-user-ring"
    constellation["constellation"]["node"] = "user:nodes/nested-user-node.yaml"

    node = yaml.safe_load((SHIPPED_ROOT / "nodes/space/starlink-v2-mesh.yaml").read_bytes())
    node = deepcopy(node)
    node["node"]["id"] = "nested-user-node"

    return {
        "contract_version": 1,
        "draft_revision": 7,
        "state": {
            "session": session,
            "catalog_documents": [
                {
                    "ref": "user:nodes/nested-user-node.yaml",
                    "document": node,
                },
                {
                    "ref": "user:constellations/nested-user-ring.yaml",
                    "document": constellation,
                },
            ],
        },
    }


def test_typed_route_prepares_direct_and_transitive_user_refs_for_switch(
    catalog_context: CatalogContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vs_api.main as main

    captured = []
    reservations = []

    async def fake_catalog_switch(deployment, context):
        captured.append((deployment, context))

    async def run_admitted(worker, *, reservation):
        reservations.append(reservation)
        await worker()
        return "catalog-deploy-operation-001"

    monkeypatch.setattr(main, "_API_KEY", "")
    monkeypatch.setattr(main, "_session_manager", object())
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 1_000_000)
    monkeypatch.setattr(main, "_run_catalog_switch", fake_catalog_switch)
    monkeypatch.setattr(main, "_admit_transition", run_admitted)

    client = TestClient(main.app)
    save = client.post(
        "/api/v1/builder/session/save",
        json={
            "draft": _draft_with_nested_user_refs(),
            "target_ref": "user:sessions/nested-user-deploy.yaml",
        },
    )
    assert save.status_code == 200, save.text
    saved = save.json()

    deploy = client.post(
        "/api/v1/builder/session/deploy",
        json={
            "session_ref": saved["session"]["ref"],
            "expected_session_revision": saved["session"]["revision"],
            "expected_document_digest": saved["digests"]["document"],
            "expected_dependency_digest": saved["digests"]["dependency"],
        },
    )

    assert deploy.status_code == 202, deploy.text
    assert deploy.json()["operation_id"] == "catalog-deploy-operation-001"
    assert len(captured) == 1
    deployment, observed_context = captured[0]
    assert observed_context is catalog_context
    assert deployment.prepared.root_yaml == saved["session"]["canonical_yaml"].encode()
    refs = {str(entry.ref) for entry in deployment.prepared.catalog_files}
    assert "user:constellations/nested-user-ring.yaml" in refs
    assert "user:nodes/nested-user-node.yaml" in refs
    assert deployment.upload.root_yaml == deployment.prepared.root_yaml
    assert deployment.upload.catalog_files == deployment.prepared.catalog_files
    assert deployment.upload.selection.closure_digest == saved["digests"]["dependency"]
    assert deployment.upload.selection.file_count == len(deployment.prepared.catalog_files)
    assert reservations[0].source.logical_id == saved["session"]["ref"]
    assert reservations[0].facts.document_digest == saved["digests"]["document"]
    assert reservations[0].facts.closure_digest == saved["digests"]["dependency"]

    changed_draft = _draft_with_nested_user_refs()
    changed_draft["draft_revision"] = 8
    changed_draft["state"]["session"]["session"]["description"] = "Changed after review"
    revisions = {
        entry["ref"]: entry["revision"]
        for entry in saved["dependency_closure"]["entries"]
        if entry.get("revision") is not None
    }
    for proposal in changed_draft["state"]["catalog_documents"]:
        proposal["expected_revision"] = revisions[proposal["ref"]]
    changed = client.post(
        "/api/v1/builder/session/save",
        json={
            "draft": changed_draft,
            "target_ref": saved["session"]["ref"],
            "expected_session_revision": saved["session"]["revision"],
        },
    )
    assert changed.status_code == 200, changed.text

    stale = client.post(
        "/api/v1/builder/session/deploy",
        json={
            "session_ref": saved["session"]["ref"],
            "expected_session_revision": saved["session"]["revision"],
            "expected_document_digest": saved["digests"]["document"],
            "expected_dependency_digest": saved["digests"]["dependency"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "builder_session_deploy.stale_source"
    assert len(captured) == 1


def test_wizard_compile_save_and_deploy_uploads_exact_custom_yaml_closure(
    catalog_context: CatalogContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vs_api.main as main

    captured = []

    async def fake_catalog_switch(deployment, context):
        captured.append((deployment, context))

    async def run_admitted(worker, **_kwargs):
        await worker()
        return "wizard-catalog-operation-001"

    monkeypatch.setattr(main, "_API_KEY", "")
    monkeypatch.setattr(main, "_session_manager", object())
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 1_000_000)
    monkeypatch.setattr(main, "_run_catalog_switch", fake_catalog_switch)
    monkeypatch.setattr(main, "_admit_transition", run_admitted)

    client = TestClient(main.app)
    compiled = client.post(
        "/api/v1/builder/wizard/compile",
        json={
            "draft_revision": 1,
            "intent": {
                "custom_constellation": {
                    "display_name": "Wizard custom shell",
                    "description": "Custom geometry for exact closure deployment",
                    "altitude_km": 550,
                    "inclination_deg": 53,
                    "pattern": "walker_delta",
                    "planes": 2,
                    "slots_per_plane": 3,
                    "raan_spacing_deg": 180,
                    "phase_offset_deg": 60,
                },
                "satellite_node_ref": "nodalarc:nodes/space/starlink-v2-mesh.yaml",
                "custom_site_refs": [
                    "nodalarc:sites/earth/us/earth-us-hawthorne.yaml",
                    "nodalarc:sites/earth/us/co/earth-us-co-denver.yaml",
                ],
                "protocol": "isis",
                "extensions": ["te", "mpls"],
                "orbit_propagator": "j2_mean_elements",
                "area_strategy": "per_plane",
                "routing_timers": {
                    "bfd": False,
                    "bfd_detect_multiplier": 3,
                    "bfd_rx_interval": 300,
                    "bfd_tx_interval": 300,
                    "isis_hello_interval": 1,
                    "isis_hello_multiplier": 3,
                    "spf_init_delay": 50,
                    "spf_short_delay": 200,
                    "spf_long_delay": 1000,
                    "spf_holddown": 2000,
                    "spf_time_to_learn": 500,
                    "ospf_hello_interval": 1,
                    "ospf_dead_interval": 3,
                    "ospf_spf_delay": 50,
                    "ospf_spf_initial_hold": 200,
                    "ospf_spf_max_hold": 1000,
                },
            },
        },
    )
    assert compiled.status_code == 200, compiled.text
    compile_result = compiled.json()
    assert compile_result["save_verdict"]["allowed"] is True
    assert isinstance(compile_result["draft"]["state"]["session"]["segments"][0]["source"], str)
    assert isinstance(
        compile_result["draft"]["state"]["session"]["segments"][1]["placement"]["from_site_set"],
        str,
    )

    save = client.post(
        "/api/v1/builder/session/save",
        json={
            "draft": compile_result["draft"],
            "target_ref": compile_result["target_ref"],
        },
    )
    assert save.status_code == 200, save.text
    saved = save.json()

    deploy = client.post(
        "/api/v1/builder/session/deploy",
        json={
            "session_ref": saved["session"]["ref"],
            "expected_session_revision": saved["session"]["revision"],
            "expected_document_digest": saved["digests"]["document"],
            "expected_dependency_digest": saved["digests"]["dependency"],
        },
    )
    assert deploy.status_code == 202, deploy.text
    assert deploy.json()["operation_id"] == "wizard-catalog-operation-001"

    deployment, observed_context = captured[0]
    assert observed_context is catalog_context
    assert deployment.prepared.root_yaml == saved["session"]["canonical_yaml"].encode()
    uploaded = {str(entry.ref): entry.yaml_bytes for entry in deployment.prepared.catalog_files}
    assert any(ref.startswith("user:orbits/wizard/") for ref in uploaded)
    assert any(ref.startswith("user:constellations/wizard/") for ref in uploaded)
    assert any(ref.startswith("user:site-sets/wizard/") for ref in uploaded)
    constellation_ref = next(ref for ref in uploaded if ref.startswith("user:constellations/"))
    constellation = yaml.safe_load(uploaded[constellation_ref])
    assert isinstance(constellation["constellation"]["orbit"], str)
