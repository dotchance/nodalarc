"""Client-visible refusal messages carry the declared inputs, never host details."""

from pathlib import Path

import pytest
import vs_api.main as main
import yaml
from nodalarc import ephemeris_runtime
from nodalarc.catalog_closure import CatalogClosureCollector
from nodalarc.catalog_refs import SessionRef
from vs_api.catalog_context import create_catalog_context
from vs_api.main import app

from tests.asgi_client import ASGITestClient as TestClient

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
DECLARED_KERNEL = "configs/ephemerides/de440s.bsp"


@pytest.fixture
def catalog_client(tmp_path: Path):
    context = create_catalog_context(
        session_data_root=tmp_path / "session-data",
        shipped_root=SHIPPED_ROOT,
    )
    app.dependency_overrides[main.get_catalog_context] = lambda: context
    try:
        yield TestClient(app), context
    finally:
        app.dependency_overrides.pop(main.get_catalog_context, None)


def _persist_luna_session(context, name: str) -> tuple[SessionRef, dict]:
    raw = yaml.safe_load((SHIPPED_ROOT / "sessions/earth-luna-quic.yaml").read_bytes())
    raw["session"]["name"] = name
    assert raw["ephemeris"]["kernels"][0]["path"] == DECLARED_KERNEL
    content = yaml.safe_dump(raw, sort_keys=False).encode("utf-8")
    ref = SessionRef(f"user:sessions/{name}.yaml")
    snapshot = context.repository.snapshot(context.scope)
    transaction = context.repository.begin(context.scope, base_generation=snapshot.generation)
    transaction.write_bytes(ref, content, expected_revision=None)
    committed = transaction.commit()
    saved = committed.get(ref)
    closure = CatalogClosureCollector.collect(saved.content, committed)
    return ref, {
        "source": {"kind": "catalog", "session_ref": str(ref)},
        "expected_source_revision": str(saved.revision),
        "expected_document_digest": closure.document_digest,
        "expected_dependency_digest": closure.closure_digest,
        "record_history": False,
    }


def test_switch_refusal_names_the_declared_kernel_and_hides_the_host_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_client,
) -> None:
    # The declared kernel path resolves, under an injected repository root, to a
    # directory. Real validation then fails on the kernel read with an OSError
    # whose text names the resolved absolute path.
    injected_root = tmp_path / "injected-repo"
    (injected_root / DECLARED_KERNEL).mkdir(parents=True)
    monkeypatch.setattr(ephemeris_runtime, "_repo_root", lambda: injected_root)

    scoped_client, context = catalog_client
    _ref, request = _persist_luna_session(context, "ephemeris-unreadable")
    monkeypatch.setattr(main, "_session_manager", object())
    monkeypatch.setattr(main, "_available_session_node_count", lambda: 7)

    response = scoped_client.post("/api/v1/sessions/switch", json=request)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "session_resolution.invalid"
    message = body["message"]
    assert str(injected_root) not in message
    assert "Errno" not in message
    assert DECLARED_KERNEL in message
    assert "could not be read" in message
