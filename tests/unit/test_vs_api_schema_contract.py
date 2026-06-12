"""Contract test: frontend types.ts and backend vs_api.py must stay in sync.

Automatically discovers ALL TypeScript interfaces in frontend/src/types.ts
and ALL Pydantic models in lib/nodalarc/models/vs_api.py. For every model
that appears in both, verifies field-level agreement in both directions.
Also verifies that no model exists in only one side without the other.

This test exists because we shipped a broken UI — the frontend declared
interface_a/interface_b on LinkState but the backend Pydantic model didn't
have them, so the serialization layer silently dropped the fields. This
test ensures that can never happen again for ANY field on ANY model.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types.ts"
SNAPSHOT_SCHEMA_JSON = (
    Path(__file__).resolve().parents[2] / "services" / "vs_api" / "schema" / "snapshot_v1.json"
)
VS_API_MODULE = "nodalarc.models.vs_api"


def _parse_all_ts_interfaces(source: str) -> dict[str, set[str]]:
    """Extract ALL interface declarations and their fields from TypeScript.

    Returns {InterfaceName: {field1, field2, ...}} for every interface.
    """
    interfaces: dict[str, set[str]] = {}
    current: str | None = None
    brace_depth = 0

    for line in source.splitlines():
        m = re.match(r"^export\s+interface\s+(\w+)\s*\{", line)
        if m:
            current = m.group(1)
            interfaces[current] = set()
            brace_depth = 1
            continue

        if current is not None:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                current = None
                continue

            fm = re.match(r"^\s+(\w+)\??\s*:", line)
            if fm:
                interfaces[current].add(fm.group(1))

    return interfaces


def _discover_all_pydantic_models() -> dict[str, type[BaseModel]]:
    """Discover ALL Pydantic BaseModel subclasses in vs_api module.

    Returns {ClassName: ModelClass} for every model.
    """
    mod = importlib.import_module(VS_API_MODULE)
    models: dict[str, type[BaseModel]] = {}
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == mod.__name__:
            models[name] = obj
    return models


def _schema_fields(model_cls: type[BaseModel]) -> set[str]:
    """Get all field names from a Pydantic model's JSON schema."""
    schema = model_cls.model_json_schema()
    return set(schema.get("properties", {}).keys())


def _required_fields(model_cls: type[BaseModel]) -> set[str]:
    """Get required field names from a Pydantic model's JSON schema."""
    schema = model_cls.model_json_schema()
    return set(schema.get("required", []))


# -- Fixtures that auto-discover both sides --


@pytest.fixture(scope="module")
def ts_interfaces() -> dict[str, set[str]]:
    assert TYPES_TS.exists(), f"Frontend types.ts not found at {TYPES_TS}"
    return _parse_all_ts_interfaces(TYPES_TS.read_text())


@pytest.fixture(scope="module")
def py_models() -> dict[str, type[BaseModel]]:
    return _discover_all_pydantic_models()


@pytest.fixture(scope="module")
def shared_names(ts_interfaces, py_models) -> set[str]:
    """Names that exist in BOTH frontend and backend."""
    return set(ts_interfaces.keys()) & set(py_models.keys())


# -- Test: no orphan models on either side --


_FRONTEND_ONLY_INTERFACES = {
    # Nodal logging payload appended to StateSnapshot after model serialization.
    "OpsEvent",
    # Session switcher response is still served by vs_api.session_manager as dicts.
    "SessionInfo",
    # Local UI selection state, not a backend payload.
    "Selection",
}


def test_no_backend_models_missing_from_frontend(ts_interfaces, py_models):
    """Every Pydantic model in vs_api.py must have a matching TypeScript
    interface in types.ts."""
    backend_only = set(py_models.keys()) - set(ts_interfaces.keys())
    assert not backend_only, (
        f"Backend models with no frontend interface: {sorted(backend_only)}. "
        f"Add matching interfaces to frontend/src/types.ts."
    )


def test_no_frontend_interfaces_missing_from_backend(ts_interfaces, py_models):
    """Any TypeScript interface without a backend model must be intentional.

    This catches the common drift class where the UI grows a new API payload
    interface but no backend model owns or validates that shape.
    """
    frontend_only = set(ts_interfaces.keys()) - set(py_models.keys())
    unclassified = frontend_only - _FRONTEND_ONLY_INTERFACES
    stale_allowlist = _FRONTEND_ONLY_INTERFACES - frontend_only

    assert not unclassified, (
        "Frontend interfaces without backend models need an explicit ownership "
        f"classification: {sorted(unclassified)}"
    )
    assert not stale_allowlist, (
        "Frontend-only schema allowlist contains names that no longer exist: "
        f"{sorted(stale_allowlist)}"
    )


# -- Test: field-level agreement for every shared model --


def _get_shared_pairs(ts_interfaces, py_models):
    """Generate (name, ts_fields, py_model) for parametrize."""
    shared = set(ts_interfaces.keys()) & set(py_models.keys())
    return [(name, ts_interfaces[name], py_models[name]) for name in sorted(shared)]


@pytest.fixture(scope="module")
def shared_pairs(ts_interfaces, py_models):
    return _get_shared_pairs(ts_interfaces, py_models)


def test_shared_models_exist(shared_pairs):
    """Sanity: we should have at least the core models in common."""
    names = {name for name, _, _ in shared_pairs}
    assert "LinkState" in names, "LinkState must be shared"
    assert "NodeState" in names, "NodeState must be shared"
    assert "StateSnapshot" in names, "StateSnapshot must be shared"


_DYNAMIC_FIELDS = {
    "StateSnapshot": {"ops_events", "ops_log_token", "debug_events", "debug_sources"},
}

# Fields the FRONTEND stamps onto a wire model after decode — never sent
# by the backend, so "missing from the backend schema" is their correct
# state. Anything added here must be optional in the TS interface and
# documented at the declaration as client-stamped.
_CLIENT_STAMPED_FIELDS = {
    "StateSnapshot": {"client_arrival_ms"},
}


def test_frontend_fields_exist_in_backend(shared_pairs):
    """For every shared model: every field the frontend declares must
    exist in the backend JSON schema. If it doesn't, the backend
    serialization will silently drop it and the UI will show blank/zero.

    Fields in _DYNAMIC_FIELDS are appended to the JSON dict after Pydantic
    serialization (e.g., ops_events) and are excluded from this check.
    Fields in _CLIENT_STAMPED_FIELDS are written by the frontend itself
    at decode time and are likewise excluded.
    """
    failures = []
    for name, ts_fields, py_model in shared_pairs:
        backend_fields = _schema_fields(py_model)
        dynamic = _DYNAMIC_FIELDS.get(name, set())
        client_stamped = _CLIENT_STAMPED_FIELDS.get(name, set())
        missing = ts_fields - backend_fields - dynamic - client_stamped
        if missing:
            failures.append(f"  {name}: frontend has {sorted(missing)} not in backend")
    assert not failures, "Frontend declares fields that backend will silently drop:\n" + "\n".join(
        failures
    )


def test_backend_required_fields_exist_in_frontend(shared_pairs):
    """For every shared model: every required backend field must exist in
    the frontend interface. If the frontend doesn't declare it, the data
    arrives but is ignored — invisible to the user."""
    failures = []
    for name, ts_fields, py_model in shared_pairs:
        required = _required_fields(py_model)
        missing = required - ts_fields
        if missing:
            failures.append(f"  {name}: backend requires {sorted(missing)} not in frontend")
    assert not failures, "Backend sends required fields that frontend ignores:\n" + "\n".join(
        failures
    )


def test_checked_in_snapshot_schema_matches_backend_model(py_models):
    """The public schema artifact must not drift from the backend model."""
    expected = py_models["StateSnapshot"].model_json_schema()
    actual = json.loads(SNAPSHOT_SCHEMA_JSON.read_text(encoding="utf-8"))
    assert actual == expected


def test_node_state_body_frame_fields_are_required_without_defaults(py_models):
    """Body/frame identity must be explicit on the wire, not implied as Earth."""
    schema = py_models["StateSnapshot"].model_json_schema()
    node_schema = schema["$defs"]["NodeState"]
    required = set(node_schema["required"])

    assert {"reference_body", "frame_id"} <= required
    assert "default" not in node_schema["properties"]["reference_body"]
    assert "default" not in node_schema["properties"]["frame_id"]
