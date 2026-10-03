"""The frontend reads the state feed's vocabularies from a file the backend wrote.

`frontend/src/generated/stateWireVocabularies.json` lists, for every wire model VS-API serves on
the state feed, the values each closed string field admits and `null` for each field left open.
The frontend contract test (`frontend/src/__tests__/stateWireContract.test.ts`) refuses a page
that branches on an open field or on a value outside a vocabulary. This test refuses a stale or
hand-edited file.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts/gen_state_wire_vocabularies.py"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_state_wire_vocabularies", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generated_vocabulary_file_is_what_the_wire_models_declare() -> None:
    generator = _generator()
    written = generator.OUTPUT.read_text()
    assert written == generator.render(), (
        f"{generator.OUTPUT.relative_to(ROOT)} is stale; run {GENERATOR.relative_to(ROOT)}"
    )


def test_every_state_feed_model_is_listed_with_its_string_fields() -> None:
    generator = _generator()
    listed = json.loads(generator.OUTPUT.read_text())["models"]
    assert {"StateSnapshot", "NodeState", "LinkState", "TracedPath"} <= set(listed)
    assert listed["NodeState"]["node_type"] == ["ground_station", "satellite"]
    assert listed["TracedPath"]["state"] == ["failed", "not_reached", "reached", "running"]


def test_every_response_model_the_app_declares_is_a_served_module_model() -> None:
    """A model served from a module the generator does not read escapes the contract."""
    import vs_api.main as m
    from pydantic import BaseModel

    served = {module.__name__ for module in _generator().SERVED_MODULES}
    outside = set()
    for route in m.app.routes:
        model = getattr(route, "response_model", None)
        for candidate in (model, *getattr(model, "__args__", ())):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                if candidate.__module__ not in served and not _has_generated_types(candidate):
                    outside.add(f"{candidate.__module__}.{candidate.__name__}")
    assert not outside, f"served models the vocabulary file does not cover: {sorted(outside)}"


def _has_generated_types(model: type) -> bool:
    """The Builder, Wizard and catalog models reach the page as generated TypeScript unions."""
    return model.__module__.startswith(
        ("nodalarc.models.builder_", "nodalarc.models.catalog", "nodalarc.models.coverage")
    )


def test_every_api_route_declares_the_model_it_answers_with() -> None:
    """A route that answers a raw dict crosses the API boundary with no contract.

    The page can read any key it likes from such an answer and nothing on either side says
    what the keys hold. Every JSON route declares a response model.
    """
    import vs_api.main as m
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    untyped = []
    for route in m.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        # FastAPI wraps its default (JSON) response class in a placeholder; an explicit other
        # class is a text answer, such as a session's YAML, and not a JSON contract.
        answer_class = getattr(route.response_class, "value", route.response_class)
        if not issubclass(answer_class, JSONResponse):
            continue
        model = route.response_model
        declared = isinstance(model, type) and issubclass(model, BaseModel)
        declared = declared or any(
            isinstance(member, type) and issubclass(member, BaseModel)
            for member in getattr(model, "__args__", ())
        )
        if not declared:
            untyped.append(f"{' '.join(sorted(route.methods))} {route.path}")
    assert not untyped, "routes that answer with no response model:\n" + "\n".join(untyped)
