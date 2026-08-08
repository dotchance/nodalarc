# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Typed references for workload documents.

The ``profiles`` and ``bindings`` families are deliberately absent from
``CatalogFamily`` and the configuration registry: they are not session-grammar
vocabulary, must not surface as authoring components, and resolve against a
workload package source, never the session catalog roots.
"""

from __future__ import annotations

from nodalarc.catalog_refs import CatalogRef


class ProfileRef(CatalogRef):
    allowed_families = frozenset({"profiles"})


class ImplementationBindingRef(CatalogRef):
    allowed_families = frozenset({"bindings"})
