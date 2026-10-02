"""Write the state feed's wire vocabularies for the frontend contract check.

Every string field of every model VS-API serves on the state feed (lib/nodalarc/models/vs_api.py)
is listed by model and field. A closed field (a Literal or a string enum) lists its values. A
field the backend leaves as an open str is listed as null: the page cannot branch on it, because
nothing says what it may hold.

    uv run python scripts/gen_state_wire_vocabularies.py          # write
    uv run python scripts/gen_state_wire_vocabularies.py --check  # exit 1 when stale
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import types
from enum import Enum
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

from nodalarc.models import vs_api
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend/src/generated/stateWireVocabularies.json"

Vocabulary = list[str] | None


def _vocabulary(annotation: object) -> Vocabulary | object:
    """The values a string-valued annotation admits; None when open; _NOT_STRING otherwise."""
    origin = get_origin(annotation)
    if origin is Literal:
        values = get_args(annotation)
        if all(isinstance(value, str) for value in values):
            return sorted(values)
        return _NOT_STRING
    if origin in (Union, types.UnionType):
        members = [member for member in get_args(annotation) if member is not type(None)]
        found: list[str] = []
        for member in members:
            vocabulary = _vocabulary(member)
            if vocabulary is _NOT_STRING:
                return _NOT_STRING
            if vocabulary is None:
                return None
            found.extend(vocabulary)
        return sorted(set(found))
    if annotation is str:
        return None
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        values = [member.value for member in annotation]
        if all(isinstance(value, str) for value in values):
            return sorted(values)
    return _NOT_STRING


_NOT_STRING = object()


def wire_vocabularies() -> dict[str, dict[str, Vocabulary]]:
    listed: dict[str, dict[str, Vocabulary]] = {}
    for name, model in inspect.getmembers(vs_api, inspect.isclass):
        if not issubclass(model, BaseModel) or model.__module__ != vs_api.__name__:
            continue
        fields: dict[str, Vocabulary] = {}
        for field_name, field in model.model_fields.items():
            vocabulary = _vocabulary(field.annotation)
            if vocabulary is _NOT_STRING:
                continue
            fields[field_name] = vocabulary  # type: ignore[assignment]
        listed[name] = fields
    return listed


def render() -> str:
    return json.dumps(wire_vocabularies(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when the file is stale")
    arguments = parser.parse_args()
    rendered = render()
    if arguments.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != rendered:
            print(
                f"{OUTPUT.relative_to(ROOT)} is stale; run {Path(__file__).name}", file=sys.stderr
            )
            return 1
        return 0
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
