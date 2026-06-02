# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Immutable mapping for deep-frozen model boundaries.

Pydantic ``frozen=True`` only blocks attribute reassignment; a ``dict`` field
value stays mutable (``cfg.config_overrides["x"] = 1`` succeeds). ``FrozenDict``
closes that leak for arbitrary-key config maps while still being a ``dict``
subclass, so it round-trips through Pydantic validation and serializes natively.
"""

import copy as _copy
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import AfterValidator


class FrozenDict(dict):
    """An immutable ``dict`` subclass. Serializes as a plain dict.

    Construction via ``dict.__init__`` (C-level) bypasses the blocked
    ``__setitem__``, so ``FrozenDict({...})`` works while in-place mutation does
    not. copy/deepcopy/pickle are supported (an immutable object must still be
    copyable — e.g. ``model_copy(deep=True)``).
    """

    __slots__ = ()
    # Unhashable like a normal dict (values may be unhashable); a frozen model
    # holding one simply isn't hashable, which we never require.
    __hash__ = None  # type: ignore[assignment]

    def _immutable(self, *args: Any, **kwargs: Any):
        raise TypeError("FrozenDict is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __ior__(self, other: Any):
        self._immutable()

    def __copy__(self) -> FrozenDict:
        return FrozenDict(self)

    def __deepcopy__(self, memo: dict) -> FrozenDict:
        return FrozenDict({k: _copy.deepcopy(v, memo) for k, v in self.items()})

    def __reduce__(self):
        return (FrozenDict, (dict(self),))


def deep_freeze(value: Any) -> Any:
    """Recursively make a value immutable: dicts -> FrozenDict, lists/tuples ->
    tuple, leaves unchanged. Needed because ``dict[str, Any]`` config maps can
    hold nested lists/dicts that would otherwise stay mutable."""
    if isinstance(value, Mapping):
        return FrozenDict({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    return value


def _freeze_mapping(value: Any) -> FrozenDict:
    return FrozenDict({k: deep_freeze(v) for k, v in value.items()})


# A str-keyed mapping that is deeply frozen after validation. Use in place of
# ``dict[str, Any]`` for config maps embedded in the frozen runtime view.
ImmutableStrDict = Annotated[dict[str, Any], AfterValidator(_freeze_mapping)]
