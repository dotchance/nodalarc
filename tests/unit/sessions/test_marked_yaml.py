"""Marked YAML decoding and JSON-pointer source-map contracts."""

from __future__ import annotations

import pytest
from nodalarc.marked_yaml import (
    MarkedYamlError,
    YamlSourcePoint,
    YamlSourceSpan,
    load_marked_yaml,
)

SOURCE = """session:
  name: demo
segments:
  - id: first
    apply:
      enabled: true
"""


def _span(
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> YamlSourceSpan:
    return YamlSourceSpan(
        start=YamlSourcePoint(line=start_line, column=start_column),
        end=YamlSourcePoint(line=end_line, column=end_column),
    )


def test_load_marked_yaml_returns_strict_data_and_mapping_key_value_spans() -> None:
    document = load_marked_yaml(SOURCE)

    assert document.data == {
        "session": {"name": "demo"},
        "segments": [{"id": "first", "apply": {"enabled": True}}],
    }
    root = document.source_map.exact("")
    session = document.source_map.exact("/session")
    name = document.source_map.exact("/session/name")

    assert root is not None
    assert root.key is None
    assert root.value == _span(1, 1, 7, 1)
    assert session is not None
    assert session.key == _span(1, 1, 1, 8)
    assert session.value == _span(2, 3, 3, 1)
    assert name is not None
    assert name.key == _span(2, 3, 2, 7)
    assert name.value == _span(2, 9, 2, 13)


def test_load_marked_yaml_indexes_sequence_items_and_nested_values() -> None:
    document = load_marked_yaml(SOURCE)

    item = document.source_map.exact("/segments/0")
    enabled = document.source_map.exact("/segments/0/apply/enabled")

    assert item is not None
    assert item.key is None
    assert item.value == _span(4, 5, 7, 1)
    assert enabled is not None
    assert enabled.key == _span(6, 7, 6, 14)
    assert enabled.value == _span(6, 16, 6, 20)


def test_json_pointer_tokens_escape_slashes_and_tildes() -> None:
    document = load_marked_yaml('"route/name~id": enabled\n')

    spans = document.source_map.exact("/route~1name~0id")

    assert spans is not None
    assert spans.key == _span(1, 1, 1, 16)
    assert spans.value == _span(1, 18, 1, 25)


def test_missing_pointer_resolves_to_deepest_existing_parent() -> None:
    document = load_marked_yaml(SOURCE)

    session_match = document.source_map.resolve("/session/missing/leaf")
    item_match = document.source_map.resolve("/segments/0/missing")
    root_match = document.source_map.resolve("/entirely/missing")

    assert session_match is not None
    assert session_match.matched_pointer == "/session"
    assert not session_match.exact
    assert document.source_map.span_for("/session/missing/leaf") == _span(2, 3, 3, 1)
    assert document.source_map.span_for("/session/missing/leaf", prefer_key=True) == _span(
        1, 1, 1, 8
    )
    assert item_match is not None
    assert item_match.matched_pointer == "/segments/0"
    assert root_match is not None
    assert root_match.matched_pointer == ""


def test_syntax_failure_exposes_one_based_problem_mark() -> None:
    with pytest.raises(MarkedYamlError) as caught:
        load_marked_yaml("session:\n  name: [broken\nsegments: []\n")

    assert caught.value.problem_mark == YamlSourcePoint(line=3, column=9)
    assert caught.value.problem is not None


def test_strict_construction_failure_exposes_duplicate_key_mark() -> None:
    with pytest.raises(MarkedYamlError) as caught:
        load_marked_yaml("session:\n  name: first\n  name: second\nsegments: []\n")

    assert caught.value.problem == "found duplicate key 'name'"
    assert caught.value.problem_mark == YamlSourcePoint(line=3, column=3)


def test_source_map_rejects_non_pointer_lookups() -> None:
    document = load_marked_yaml(SOURCE)

    with pytest.raises(ValueError, match="begin with"):
        document.source_map.resolve("session/name")
