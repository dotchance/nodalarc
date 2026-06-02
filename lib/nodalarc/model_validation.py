# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Shared field validators for selector/list semantics.

A selector or override list that is empty, contains duplicates, or holds an
invalid index is a "valid object that does nothing" — it silently matches
nothing or encodes ambiguous intent. Under the no-fallback rule these must fail
at parse time, not become no-op behavior the resolver has to interpret. Use
these as Pydantic ``AfterValidator``s on the field type.
"""

from typing import Any


def nonempty(values: Any) -> Any:
    """A present sequence must be non-empty (``None`` is allowed = filter absent)."""
    if values is not None and len(values) == 0:
        raise ValueError("must not be empty")
    return values


def nonempty_unique(values: Any) -> Any:
    """A present sequence must be non-empty and free of duplicates."""
    if values is None:
        return values
    if len(values) == 0:
        raise ValueError("must not be empty")
    seen: set = set()
    dups: list = []
    for value in values:
        if value in seen:
            dups.append(value)
        seen.add(value)
    if dups:
        raise ValueError(f"must not contain duplicate entries: {dups}")
    return values
