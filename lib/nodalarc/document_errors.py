"""The errors a grammar validation found in one document, in that document's terms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

_MAPPING_TYPES = frozenset({"dict_type", "model_type", "model_attributes_type"})
_LIST_TYPES = frozenset({"list_type", "tuple_type", "set_type", "frozen_set_type"})
_WHOLE_NUMBER_TYPES = frozenset({"int_type", "int_parsing", "int_from_float"})
_NUMBER_TYPES = frozenset({"float_type", "float_parsing"})
_BOOLEAN_TYPES = frozenset({"bool_type", "bool_parsing"})
# Errors whose last location part names a key the document lacks or must not hold.
_KEY_NAMING_TYPES = frozenset({"missing", "extra_forbidden"})


@dataclass(frozen=True, slots=True)
class DocumentError:
    """One error at its place in the validated document.

    ``path`` holds only keys and list indexes of the document, ``pointer`` is its
    JSON pointer ("" for the document root), and ``message`` names the field in
    YAML notation and states the problem.
    """

    type: str
    path: tuple[str | int, ...]
    pointer: str
    message: str


def document_errors(error: ValidationError, document: object) -> tuple[DocumentError, ...]:
    """Each error of ``error``, which came from validating ``document``."""
    found: list[DocumentError] = []
    for item in error.errors(include_url=False):
        path = _document_path(
            tuple(item["loc"]),
            document,
            names_key=item["type"] in _KEY_NAMING_TYPES,
        )
        place = _yaml_path(path)
        text = _text(item)
        found.append(
            DocumentError(
                type=item["type"],
                path=path,
                pointer="".join(f"/{_escape(part)}" for part in path),
                message=f"{place}: {text}" if place else text,
            )
        )
    return tuple(found)


def _document_path(
    loc: tuple[str | int, ...],
    document: object,
    *,
    names_key: bool,
) -> tuple[str | int, ...]:
    """The parts of ``loc`` that are keys or indexes of the document as it is walked.

    Any other part is a union tag or a validator label and is left out. The last part
    of an error that names a key is kept even though the document does not hold it.
    """
    path: list[str | int] = []
    value: Any = document
    last = len(loc) - 1
    for position, part in enumerate(loc):
        if _holds(value, part):
            value = value[part]
        elif not (names_key and position == last):
            continue
        path.append(part)
    return tuple(path)


def _holds(value: object, part: str | int) -> bool:
    """Whether ``part`` is a key of the mapping ``value`` or an index of the list ``value``."""
    if isinstance(value, Mapping):
        return isinstance(part, str) and part in value
    if isinstance(value, list | tuple):
        return isinstance(part, int) and 0 <= part < len(value)
    return False


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


def _text(item: ErrorDetails) -> str:
    """The problem in plain words, without Python type names."""
    error_type = item["type"]
    if error_type == "too_short":
        ctx = item["ctx"]
        return f"needs at least {_items(ctx['min_length'])}; it has {ctx['actual_length']}"
    if error_type == "too_long":
        ctx = item["ctx"]
        return f"holds at most {_items(ctx['max_length'])}; it has {ctx['actual_length']}"
    if error_type == "missing":
        return "is required"
    if error_type == "extra_forbidden":
        return "is not a field the grammar allows here"
    if error_type in _MAPPING_TYPES:
        return "should be a mapping"
    if error_type in _LIST_TYPES:
        return "should be a list"
    if error_type == "string_type":
        return "should be text"
    if error_type in _WHOLE_NUMBER_TYPES:
        return "should be a whole number"
    if error_type in _NUMBER_TYPES:
        return "should be a number"
    if error_type in _BOOLEAN_TYPES:
        return "should be true or false"
    if error_type == "value_error":
        return item["msg"].removeprefix("Value error, ")
    if error_type == "assertion_error":
        return item["msg"].removeprefix("Assertion failed, ")
    if item["msg"].startswith("Input should"):
        return item["msg"].replace("Input should", "should", 1)
    return item["msg"]


def _yaml_path(path: tuple[str | int, ...]) -> str:
    """``link_rules[0].endpoints[1].select`` for the path of that field."""
    text = ""
    for part in path:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text


def _escape(part: str | int) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")
