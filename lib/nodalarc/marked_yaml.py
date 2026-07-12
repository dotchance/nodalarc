"""YAML decoding with JSON-pointer source locations for authoring diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import yaml
from yaml.error import Mark
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from nodalarc.configuration_yaml import load_configuration_yaml


@dataclass(frozen=True, slots=True)
class YamlSourcePoint:
    """One-based line and column in a YAML source document."""

    line: int
    column: int


@dataclass(frozen=True, slots=True)
class YamlSourceSpan:
    """One-based, end-exclusive source span for one YAML node."""

    start: YamlSourcePoint
    end: YamlSourcePoint


@dataclass(frozen=True, slots=True)
class YamlNodeSpans:
    """Source spans for one JSON-pointer-addressed YAML value and its mapping key."""

    value: YamlSourceSpan
    key: YamlSourceSpan | None = None

    def preferred(self, *, prefer_key: bool = False) -> YamlSourceSpan:
        """Return the key span when available and requested, otherwise the value span."""

        return self.key if prefer_key and self.key is not None else self.value


@dataclass(frozen=True, slots=True)
class YamlPointerMatch:
    """The deepest mapped pointer matching one requested JSON pointer."""

    requested_pointer: str
    matched_pointer: str
    spans: YamlNodeSpans

    @property
    def exact(self) -> bool:
        return self.requested_pointer == self.matched_pointer


@dataclass(frozen=True, slots=True)
class YamlSourceMap:
    """JSON-pointer index over composed YAML node locations."""

    pointers: Mapping[str, YamlNodeSpans]

    def exact(self, pointer: str) -> YamlNodeSpans | None:
        """Return spans only when ``pointer`` exists in the composed document."""

        _validate_json_pointer(pointer)
        return self.pointers.get(pointer)

    def resolve(self, pointer: str) -> YamlPointerMatch | None:
        """Resolve a pointer, falling back to its deepest existing parent."""

        _validate_json_pointer(pointer)
        candidate = pointer
        while True:
            spans = self.pointers.get(candidate)
            if spans is not None:
                return YamlPointerMatch(
                    requested_pointer=pointer,
                    matched_pointer=candidate,
                    spans=spans,
                )
            if not candidate:
                return None
            candidate = candidate.rsplit("/", 1)[0]

    def span_for(self, pointer: str, *, prefer_key: bool = False) -> YamlSourceSpan | None:
        """Return the preferred span for a pointer or its deepest existing parent."""

        match = self.resolve(pointer)
        return match.spans.preferred(prefer_key=prefer_key) if match is not None else None


@dataclass(frozen=True, slots=True)
class MarkedYamlDocument:
    """Strictly decoded YAML data together with its composed source map."""

    data: Any
    source_map: YamlSourceMap


class MarkedYamlError(ValueError):
    """YAML composition or construction failure with exposed one-based marks."""

    def __init__(self, error: yaml.YAMLError) -> None:
        super().__init__(str(error))
        self.problem = getattr(error, "problem", None)
        self.problem_mark = _point(getattr(error, "problem_mark", None))
        self.context = getattr(error, "context", None)
        self.context_mark = _point(getattr(error, "context_mark", None))


def load_marked_yaml(source: str | bytes) -> MarkedYamlDocument:
    """Strictly decode one YAML document and index its nodes by JSON pointer."""

    try:
        root = yaml.compose(source, Loader=yaml.SafeLoader)
        data = load_configuration_yaml(source)
    except yaml.YAMLError as error:
        raise MarkedYamlError(error) from error

    pointers: dict[str, YamlNodeSpans] = {}
    if root is not None:
        _index_node(root, pointer="", key_node=None, pointers=pointers, ancestors=set())
    return MarkedYamlDocument(
        data=data,
        source_map=YamlSourceMap(pointers=MappingProxyType(pointers)),
    )


def _index_node(
    node: Node,
    *,
    pointer: str,
    key_node: Node | None,
    pointers: dict[str, YamlNodeSpans],
    ancestors: set[int],
) -> None:
    pointers[pointer] = YamlNodeSpans(
        value=_span(node),
        key=_span(key_node) if key_node is not None else None,
    )
    identity = id(node)
    if identity in ancestors:
        return
    nested_ancestors = {*ancestors, identity}
    if isinstance(node, MappingNode):
        for child_key, child_value in node.value:
            if not isinstance(child_key, ScalarNode):
                continue
            child_pointer = _join_pointer(pointer, child_key.value)
            _index_node(
                child_value,
                pointer=child_pointer,
                key_node=child_key,
                pointers=pointers,
                ancestors=nested_ancestors,
            )
    elif isinstance(node, SequenceNode):
        for index, child in enumerate(node.value):
            _index_node(
                child,
                pointer=_join_pointer(pointer, str(index)),
                key_node=None,
                pointers=pointers,
                ancestors=nested_ancestors,
            )


def _join_pointer(parent: str, token: str) -> str:
    escaped = token.replace("~", "~0").replace("/", "~1")
    return f"{parent}/{escaped}"


def _validate_json_pointer(pointer: str) -> None:
    if pointer and not pointer.startswith("/"):
        raise ValueError("JSON pointers must be empty or begin with '/'")


def _point(mark: Mark | None) -> YamlSourcePoint | None:
    if mark is None:
        return None
    return YamlSourcePoint(line=mark.line + 1, column=mark.column + 1)


def _span(node: Node) -> YamlSourceSpan:
    return YamlSourceSpan(
        start=YamlSourcePoint(line=node.start_mark.line + 1, column=node.start_mark.column + 1),
        end=YamlSourcePoint(line=node.end_mark.line + 1, column=node.end_mark.column + 1),
    )
