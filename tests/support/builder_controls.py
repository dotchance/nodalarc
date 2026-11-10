# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
# Additional terms under AGPL-3.0 Section 7 are in the NOTICE file.
"""Test-only walks over a backend-built Builder control tree."""

from __future__ import annotations

from collections.abc import Iterator

from nodalarc.models.builder_controls_api import (
    BuilderChoiceControl,
    BuilderControl,
    BuilderMapControl,
    BuilderObjectControl,
    BuilderScalarControl,
    BuilderSequenceControl,
)


def walk_controls(control: BuilderControl) -> Iterator[BuilderControl]:
    """Yield ``control`` and every control nested in it, add-item templates included."""
    yield control
    if isinstance(control, BuilderObjectControl):
        for field in control.fields:
            yield from walk_controls(field.control)
    elif isinstance(control, BuilderChoiceControl):
        for branch in control.branches:
            if branch.control is not None:
                yield from walk_controls(branch.control)
    elif isinstance(control, BuilderSequenceControl):
        for item in control.items:
            yield from walk_controls(item.control)
        if control.add_item_control is not None:
            yield from walk_controls(control.add_item_control)
    elif isinstance(control, BuilderMapControl):
        for entry in control.entries:
            yield from walk_controls(entry.key)
            yield from walk_controls(entry.value)
        yield from walk_controls(control.add_key_control)
        yield from walk_controls(control.add_value_control)


def control_at(root: BuilderControl, pointer: str) -> BuilderControl:
    """The first control the tree renders at ``pointer``."""
    return next(control for control in walk_controls(root) if control.json_pointer == pointer)


def scalar_at(root: BuilderControl, pointer: str) -> BuilderScalarControl:
    """The scalar control at ``pointer``, inside the choice an optional field renders."""
    return next(
        control
        for control in walk_controls(root)
        if control.json_pointer == pointer and isinstance(control, BuilderScalarControl)
    )


def as_browser_sends(value: object) -> object:
    """``value`` as the page sends it back: browser JSON writes an integral float as an int."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: as_browser_sends(item) for key, item in value.items()}
    if isinstance(value, list):
        return [as_browser_sends(item) for item in value]
    return value
