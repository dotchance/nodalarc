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
    listed = json.loads(generator.OUTPUT.read_text())
    assert {"StateSnapshot", "NodeState", "LinkState", "TracedPath"} <= set(listed)
    assert listed["NodeState"]["node_type"] == ["ground_station", "satellite"]
    assert listed["TracedPath"]["state"] == ["failed", "not_reached", "reached", "running"]
