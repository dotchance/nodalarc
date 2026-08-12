"""Canonical catalog-family registry and configuration document union."""

from __future__ import annotations

from types import MappingProxyType

from pydantic import BaseModel

from nodalarc.catalog_refs import CatalogFamily
from nodalarc.models.catalog import (
    BodyDocument,
    ConstellationDocument,
    NodeDocument,
    OrbitDocument,
    PayloadDocument,
    ProfileDocument,
    SiteDocument,
    SiteSetDocument,
    SpaceNodeSetDocument,
    TerminalDocument,
)
from nodalarc.models.segment_session import SegmentSessionConfig

type ConfigurationDocument = (
    BodyDocument
    | TerminalDocument
    | PayloadDocument
    | ProfileDocument
    | OrbitDocument
    | NodeDocument
    | SiteDocument
    | SiteSetDocument
    | ConstellationDocument
    | SpaceNodeSetDocument
    | SegmentSessionConfig
)


CONFIGURATION_DOCUMENT_MODELS: MappingProxyType[CatalogFamily, type[BaseModel]] = MappingProxyType(
    {
        "bodies": BodyDocument,
        "terminals": TerminalDocument,
        "payloads": PayloadDocument,
        "profiles": ProfileDocument,
        "orbits": OrbitDocument,
        "nodes": NodeDocument,
        "sites": SiteDocument,
        "site-sets": SiteSetDocument,
        "constellations": ConstellationDocument,
        "space-node-sets": SpaceNodeSetDocument,
        "sessions": SegmentSessionConfig,
    }
)
