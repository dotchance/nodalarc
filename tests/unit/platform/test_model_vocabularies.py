"""A model field whose admitted values are written in a comment is an open vocabulary.

`state: str  # "active" or "inactive"` tells a reader what the field holds and tells no
program. Every consumer (another service, the page, a test) then compares against literals
nothing checks, and a value outside the comment passes every boundary. The vocabulary belongs in
the annotation: a Literal or a string enum.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODEL_FILES = sorted((ROOT / "lib/nodalarc/models").glob("*.py"))
_OPEN_STRING_WITH_LISTED_VALUES = re.compile(
    r'^\s*(?P<field>[a-z_]+):\s*(?:str|str \| None|None \| str)\s*(?:=\s*[^#]*?)?#.*"[a-z_]+"'
)


def test_no_model_field_keeps_its_vocabulary_in_a_comment() -> None:
    found = []
    for path in MODEL_FILES:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            match = _OPEN_STRING_WITH_LISTED_VALUES.match(line)
            if match:
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not found, (
        "fields typed str whose values are listed in a comment; make each a Literal or enum:\n"
        + "\n".join(found)
    )
