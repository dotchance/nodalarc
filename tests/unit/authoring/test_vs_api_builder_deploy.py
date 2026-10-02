import json
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

ROOT = Path(__file__).resolve().parents[3]
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


def test_yaml_import_accepts_a_valid_closure_larger_than_one_megabyte(
    catalog_context: CatalogContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vs_api.main as main

    monkeypatch.setattr(main, "_API_KEY", "")
    root = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-leo-simple.yaml").read_bytes())
    root["session"]["name"] = "large-yaml-import"
    root["segments"][0]["source"] = "user:constellations/large/ring.yaml"

    constellation = yaml.safe_load(
        (SHIPPED_ROOT / "constellations/earth/leo/earth-leo-ring-36.yaml").read_bytes()
    )
    constellation["constellation"]["id"] = "ring"
    constellation["constellation"]["node"] = "user:nodes/large/node.yaml"

    node = yaml.safe_load((SHIPPED_ROOT / "nodes/space/starlink-v2-mesh.yaml").read_bytes())
    node["node"]["id"] = "node"
    terminal_source = yaml.safe_load(
        (SHIPPED_ROOT / "terminals/rf/rf-ka-leo-access.yaml").read_bytes()
    )
    terminal_files = []
    mounts = []
    for suffix in ("a", "b", "c"):
        terminal = deepcopy(terminal_source)
        terminal["terminal"]["id"] = f"terminal-{suffix}"
        terminal["terminal"]["notes"] = suffix * 400_000
        ref = f"user:terminals/large/terminal-{suffix}.yaml"
        terminal_files.append(
            {
                "yaml_text": yaml.safe_dump(terminal, sort_keys=False),
                "logical_path_hint": f"catalog/user/terminals/large/terminal-{suffix}.yaml",
            }
        )
        mounts.append(
            {
                "id": f"access-{suffix}",
                "role": "access",
                "terminal": ref,
                "count": 1,
            }
        )
    node["node"]["terminals"] = mounts

    payload = {
        "yaml_files": [
            {
                "yaml_text": yaml.safe_dump(root, sort_keys=False),
                "logical_path_hint": "catalog/user/sessions/large-yaml-import.yaml",
            },
            {
                "yaml_text": yaml.safe_dump(constellation, sort_keys=False),
                "logical_path_hint": "catalog/user/constellations/large/ring.yaml",
            },
            {
                "yaml_text": yaml.safe_dump(node, sort_keys=False),
                "logical_path_hint": "catalog/user/nodes/large/node.yaml",
            },
            *terminal_files,
        ],
        "commit": False,
    }
    assert len(json.dumps(payload).encode("utf-8")) > main._MAX_BODY_BYTES

    response = TestClient(main.app).post(
        "/api/v1/builder/session/yaml/import",
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "proposed"
    assert len(response.json()["proposed_writes"]) == 6
