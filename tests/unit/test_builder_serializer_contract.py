"""Stopgap contract test (deliverable #1): every builder-serializer output must
resolve through real NodalArc, and the shipped-session JSON fixtures used by the
frontend round-trip test must stay in sync with the shipped YAML.

This bounds drift between the two grammar implementations; it does not make them
one. See specs/session-builder-requirement.md (backend-owned grammar is the
target). The corpus JSON is produced by
frontend/src/builder/__tests__/serializerCorpus.test.ts.
"""

import json
from pathlib import Path

import pytest
import yaml
from nodalarc.models.resolved_session import SourceContext
from nodalarc.resolve_session import resolve_session_with_assets

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests" / "fixtures" / "builder-serializer-corpus"
SHIPPED_JSON = REPO / "tests" / "fixtures" / "shipped-sessions-json"
SHIPPED_YAML = REPO / "catalog" / "nodalarc" / "sessions"

CORPUS_FILES = sorted(CORPUS.glob("*.json"))
SHIPPED_JSON_FILES = sorted(SHIPPED_JSON.glob("*.json"))


def test_corpus_is_non_empty():
    assert CORPUS_FILES, (
        "builder-serializer corpus is empty — the frontend emitter "
        "(serializerCorpus.test.ts) produced nothing"
    )


@pytest.mark.parametrize("path", CORPUS_FILES, ids=lambda p: p.stem)
def test_builder_serializer_output_resolves(path):
    """Each builder-produced session document resolves through the real resolver."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    resolution = resolve_session_with_assets(
        doc, source_context=SourceContext(origin="builder.serializer.contract")
    )
    assert len(resolution.resolved.nodes) > 0, f"{path.name} resolved to zero nodes"


def test_shipped_json_fixtures_exist():
    assert SHIPPED_JSON_FILES, "shipped-session JSON fixtures missing — regenerate them"


@pytest.mark.parametrize("path", SHIPPED_JSON_FILES, ids=lambda p: p.stem)
def test_shipped_json_fixture_matches_yaml(path):
    """The JSON the frontend round-trip test reads must equal the shipped YAML,
    so a session change can't silently leave the frontend contract testing a
    stale document."""
    yaml_path = SHIPPED_YAML / (path.stem + ".yaml")
    assert yaml_path.exists(), f"no shipped YAML for fixture {path.name}"
    assert json.loads(path.read_text(encoding="utf-8")) == yaml.safe_load(
        yaml_path.read_text(encoding="utf-8")
    ), f"{path.name} is stale vs {yaml_path.name}; regenerate the shipped-session JSON fixtures"
