#!/usr/bin/env python3
"""Generate Builder application-contract TypeScript from Pydantic schemas.

The emitted types describe browser/VS-API workflow DTOs only. They are not a
second session grammar and intentionally keep configuration documents as YAML
strings or generic JSON values. The backend validates canonical response fields
through its persisted Pydantic models before they cross the API boundary.

Usage:
  make generate-contracts
  make check-contracts
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, get_args

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "services"))

from nodalarc.catalog_refs import CatalogFamily, CatalogRef, SessionRef  # noqa: E402
from nodalarc.models.builder_api import (  # noqa: E402
    BuilderBlockedOperation,
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDeployVerdict,
    BuilderDigests,
    BuilderDraftEnvelope,
    BuilderDraftState,
    BuilderIssue,
    BuilderIssueSeverity,
    BuilderIssueStage,
    BuilderProposedCatalogDocument,
    BuilderSessionDeployAccepted,
    BuilderSessionDeployRefusal,
    BuilderSessionDeployRequest,
    BuilderSessionSaveRefusal,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderVerdict,
    DependencyClosureEntry,
    DependencyClosureInventory,
    Sha256Digest,
    WizardAreaStrategy,
    WizardAvailableStation,
    WizardAvailableStationResponse,
    WizardBfdMetadata,
    WizardCompileRefusal,
    WizardCompileRequest,
    WizardConstellationCapability,
    WizardConstellationGeometry,
    WizardConstellationPreset,
    WizardConstellationPresetResponse,
    WizardConstellationSourceKind,
    WizardCoverageRequest,
    WizardExtension,
    WizardExtensionMetadata,
    WizardExtensionRulesResponse,
    WizardGroundStationSetPreset,
    WizardGroundStationSetPresetResponse,
    WizardOrbitModelMetadata,
    WizardOrbitPropagator,
    WizardPhysicalIntent,
    WizardProtocolMetadata,
    WizardRoutingBooleanField,
    WizardRoutingProtocol,
    WizardRoutingTimerField,
    WizardRoutingTimerFieldMetadata,
    WizardRoutingTimerIntent,
    WizardSatelliteTerminalSummary,
    WizardSatelliteTypePreset,
    WizardSatelliteTypePresetResponse,
    WizardSessionIntent,
    WizardTerminalRole,
    WizardWalkerPattern,
    WizardWalkerPatternMetadata,
)
from nodalarc.models.builder_catalog_api import (  # noqa: E402
    BuilderCatalogBootstrap,
    BuilderCatalogCapabilities,
    BuilderVisualAuthoringFacts,
    BuilderVisualBoundaryAdapterMetadata,
    BuilderVisualForwardingClassMetadata,
    BuilderVisualLinkMediumMetadata,
    BuilderVisualMountRoleMetadata,
    BuilderVisualOrbitPropagatorMetadata,
    BuilderVisualOrbitShapeMetadata,
    BuilderVisualPhasingModeMetadata,
    BuilderVisualRoutingProtocolMetadata,
    BuilderVisualSchedulingPresetMetadata,
    BuilderVisualTopologyModeMetadata,
    CatalogClosureImportRequest,
    CatalogComponentDraftEnvelope,
    CatalogComponentFamily,
    CatalogDeleteRequest,
    CatalogDeleteResult,
    CatalogDependencyImpact,
    CatalogDependent,
    CatalogDependentsRequest,
    CatalogDocumentSummary,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftIssue,
    CatalogDraftIssueStage,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftPatchCommand,
    CatalogDraftPatchOperation,
    CatalogDraftPatchRequest,
    CatalogDraftReplaceObjectRequest,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogFamilyMetadata,
    CatalogForkRequest,
    CatalogForkResult,
    CatalogGetRequest,
    CatalogImportCollision,
    CatalogImportEntry,
    CatalogImportResult,
    CatalogImportWrite,
    CatalogListPage,
    CatalogListRequest,
    CatalogMutationResult,
    CatalogOperationRefusal,
    CatalogSessionExport,
    CatalogSessionExportRequest,
    PortableCatalogYaml,
)
from nodalarc.models.builder_visual_api import (  # noqa: E402
    BuilderVisualAddBoundaryCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualAddGroundCommand,
    BuilderVisualAddGroundSiteNodeCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
    BuilderVisualAddNodeEthernetPortCommand,
    BuilderVisualAddOrIncrementNodeTerminalCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualAuthorInlineSpaceNodeCommand,
    BuilderVisualCatalogRevision,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAffectedKind,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandOperation,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftMode,
    BuilderVisualDraftOpenRequest,
    BuilderVisualGroundBoresight,
    BuilderVisualGroundDraft,
    BuilderVisualGroundMember,
    BuilderVisualGroundReference,
    BuilderVisualGroundSiteIntent,
    BuilderVisualGroundStamp,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualNode,
    BuilderVisualOrbit,
    BuilderVisualOrbitPropagator,
    BuilderVisualOrbitShape,
    BuilderVisualPhasingMode,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingDomain,
    BuilderVisualSchedulingPreset,
    BuilderVisualSetGroundSiteNodeModelCommand,
    BuilderVisualSetGroundStampNodeModelCommand,
    BuilderVisualSetNodeTerminalRoleCommand,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualSite,
    BuilderVisualSiteNode,
    BuilderVisualSpaceBoresight,
    BuilderVisualSpaceDraft,
    BuilderVisualSpaceReference,
    BuilderVisualTerminalMount,
    BuilderVisualTopologyMode,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWalkerLayoutResult,
    BuilderVisualWorkspace,
)
from nodalarc.models.builder_world import BuilderWorld  # noqa: E402
from nodalarc.models.coverage import CoveragePreviewResult  # noqa: E402
from nodalarc.models.session_sources import (  # noqa: E402
    CatalogSessionBlocker,
    CatalogSessionSourceId,
    CatalogSessionSummary,
    CatalogSessionSwitchAccepted,
    CatalogSessionSwitchRequest,
    CatalogSessionYamlUploadRequest,
)
from pydantic import BaseModel, TypeAdapter  # noqa: E402
from vs_api.transition_operations import (  # noqa: E402
    TransitionOperation,
    TransitionOperationEvent,
    TransitionOperationFacts,
    TransitionOperationFailure,
    TransitionOperationSource,
    TransitionOperationSourceKind,
    TransitionOperationState,
    TransitionRuntimeResult,
)

OUT = ROOT / "frontend/src/builder/generated/builderApi.ts"

MODEL_TYPES: tuple[type[BaseModel], ...] = (
    BuilderIssue,
    BuilderCatalogDocument,
    BuilderProposedCatalogDocument,
    BuilderDraftState,
    BuilderDraftEnvelope,
    DependencyClosureEntry,
    DependencyClosureInventory,
    BuilderDigests,
    BuilderVerdict,
    BuilderDeployVerdict,
    BuilderCompileRequest,
    BuilderCompileResult,
    WizardConstellationGeometry,
    WizardConstellationCapability,
    WizardConstellationPreset,
    WizardConstellationPresetResponse,
    WizardOrbitModelMetadata,
    WizardWalkerPatternMetadata,
    WizardSatelliteTerminalSummary,
    WizardSatelliteTypePreset,
    WizardSatelliteTypePresetResponse,
    WizardGroundStationSetPreset,
    WizardGroundStationSetPresetResponse,
    WizardAvailableStation,
    WizardAvailableStationResponse,
    WizardBfdMetadata,
    WizardExtensionMetadata,
    WizardRoutingTimerFieldMetadata,
    WizardProtocolMetadata,
    WizardExtensionRulesResponse,
    WizardPhysicalIntent,
    WizardRoutingTimerIntent,
    WizardSessionIntent,
    WizardCompileRequest,
    WizardCoverageRequest,
    WizardCompileRefusal,
    BuilderSessionDeployRequest,
    BuilderSessionDeployAccepted,
    BuilderSessionDeployRefusal,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderSessionSaveRefusal,
    BuilderVisualCatalogRevision,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainResult,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualAuthorInlineSpaceNodeCommand,
    BuilderVisualAddOrIncrementNodeTerminalCommand,
    BuilderVisualSetNodeTerminalRoleCommand,
    BuilderVisualAddNodeEthernetPortCommand,
    BuilderVisualAddGroundCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
    BuilderVisualSetGroundStampNodeModelCommand,
    BuilderVisualSetGroundSiteNodeModelCommand,
    BuilderVisualAddGroundSiteNodeCommand,
    BuilderVisualGroundSiteIntent,
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualAddBoundaryCommand,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWalkerLayoutResult,
    BuilderVisualSpaceBoresight,
    BuilderVisualGroundBoresight,
    BuilderVisualTerminalMount,
    BuilderVisualNode,
    BuilderVisualOrbit,
    BuilderVisualSpaceDraft,
    BuilderVisualSpaceReference,
    BuilderVisualSiteNode,
    BuilderVisualSite,
    BuilderVisualGroundMember,
    BuilderVisualGroundStamp,
    BuilderVisualGroundDraft,
    BuilderVisualGroundReference,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualRoutingDomain,
    BuilderVisualRoutingBoundary,
    BuilderVisualWorkspace,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftOpenRequest,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftAssemblyResult,
    CatalogSessionSourceId,
    CatalogSessionBlocker,
    CatalogSessionSummary,
    CatalogSessionSwitchRequest,
    CatalogSessionSwitchAccepted,
    CatalogSessionYamlUploadRequest,
    TransitionOperationSource,
    TransitionOperationFacts,
    TransitionOperationEvent,
    TransitionOperationFailure,
    TransitionRuntimeResult,
    TransitionOperation,
    CatalogFamilyMetadata,
    BuilderCatalogCapabilities,
    BuilderVisualSchedulingPresetMetadata,
    BuilderVisualMountRoleMetadata,
    BuilderVisualLinkMediumMetadata,
    BuilderVisualForwardingClassMetadata,
    BuilderVisualRoutingProtocolMetadata,
    BuilderVisualBoundaryAdapterMetadata,
    BuilderVisualPhasingModeMetadata,
    BuilderVisualOrbitShapeMetadata,
    BuilderVisualOrbitPropagatorMetadata,
    BuilderVisualTopologyModeMetadata,
    BuilderVisualAuthoringFacts,
    BuilderCatalogBootstrap,
    CatalogListRequest,
    CatalogDocumentSummary,
    CatalogListPage,
    CatalogGetRequest,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogDraftIssue,
    CatalogComponentDraftEnvelope,
    CatalogDraftNewRequest,
    CatalogDraftOpenRequest,
    CatalogDraftPatchCommand,
    CatalogDraftPatchRequest,
    CatalogDraftReplaceObjectRequest,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogForkRequest,
    CatalogDependentsRequest,
    CatalogDependent,
    CatalogDependencyImpact,
    CatalogMutationResult,
    CatalogForkResult,
    CatalogDeleteRequest,
    CatalogDeleteResult,
    CatalogSessionExportRequest,
    PortableCatalogYaml,
    CatalogSessionExport,
    CatalogImportEntry,
    CatalogClosureImportRequest,
    CatalogImportWrite,
    CatalogImportCollision,
    CatalogImportResult,
    CatalogOperationRefusal,
)

# Schema roots whose complete nested wire contracts are part of the generated
# application API. Pydantic places their referenced models in ``$defs``; emit
# those definitions recursively so the browser never maintains handwritten
# twins of backend-owned response DTOs.
EMBEDDED_MODEL_TYPES: tuple[type[BaseModel], ...] = (BuilderWorld, CoveragePreviewResult)


def _alias_args(alias: Any) -> tuple[Any, ...]:
    """Return Literal members through ordinary and PEP 695 type aliases."""

    value = getattr(alias, "__value__", alias)
    return get_args(value)


LITERAL_ALIASES: tuple[tuple[str, tuple[Any, ...]], ...] = (
    ("CatalogFamily", _alias_args(CatalogFamily)),
    ("BuilderIssueStage", _alias_args(BuilderIssueStage)),
    ("BuilderIssueSeverity", _alias_args(BuilderIssueSeverity)),
    ("BuilderBlockedOperation", _alias_args(BuilderBlockedOperation)),
    ("WizardOrbitPropagator", _alias_args(WizardOrbitPropagator)),
    ("WizardConstellationSourceKind", _alias_args(WizardConstellationSourceKind)),
    ("WizardTerminalRole", _alias_args(WizardTerminalRole)),
    ("WizardExtension", _alias_args(WizardExtension)),
    ("WizardAreaStrategy", _alias_args(WizardAreaStrategy)),
    ("WizardRoutingProtocol", _alias_args(WizardRoutingProtocol)),
    ("WizardWalkerPattern", _alias_args(WizardWalkerPattern)),
    ("WizardRoutingBooleanField", _alias_args(WizardRoutingBooleanField)),
    ("WizardRoutingTimerField", _alias_args(WizardRoutingTimerField)),
    ("BuilderVisualDraftMode", _alias_args(BuilderVisualDraftMode)),
    ("BuilderVisualSchedulingPreset", _alias_args(BuilderVisualSchedulingPreset)),
    ("BuilderVisualPhasingMode", _alias_args(BuilderVisualPhasingMode)),
    ("BuilderVisualOrbitShape", _alias_args(BuilderVisualOrbitShape)),
    ("BuilderVisualOrbitPropagator", _alias_args(BuilderVisualOrbitPropagator)),
    ("BuilderVisualTopologyMode", _alias_args(BuilderVisualTopologyMode)),
    (
        "BuilderVisualDraftCommandOperation",
        _alias_args(BuilderVisualDraftCommandOperation),
    ),
    ("BuilderVisualDraftAffectedKind", _alias_args(BuilderVisualDraftAffectedKind)),
    ("CatalogComponentFamily", _alias_args(CatalogComponentFamily)),
    ("CatalogDraftPatchOperation", _alias_args(CatalogDraftPatchOperation)),
    ("CatalogDraftIssueStage", _alias_args(CatalogDraftIssueStage)),
    ("TransitionOperationState", tuple(state.value for state in TransitionOperationState)),
    (
        "TransitionOperationSourceKind",
        tuple(kind.value for kind in TransitionOperationSourceKind),
    ),
)
_LITERAL_ALIAS_BY_VALUES = {values: name for name, values in LITERAL_ALIASES}
_SHA256_PATTERN = TypeAdapter(Sha256Digest).json_schema()["pattern"]
_REFERENCE_ALIAS_BY_PATTERN = {
    TypeAdapter(CatalogRef).json_schema()["pattern"]: "CatalogRef",
    TypeAdapter(SessionRef).json_schema()["pattern"]: "SessionRef",
}
_GENERIC_CONFIGURATION_SCHEMA_REFS = {
    "ValidatedConfigurationJson",
    "ValidatedSessionJson",
}


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _literal_union(values: Sequence[Any]) -> str:
    return " | ".join(_literal(value) for value in values)


def _render_type(schema: dict[str, Any]) -> str:
    reference = schema.get("$ref")
    if reference is not None:
        name = reference.rsplit("/", 1)[-1]
        if name in _GENERIC_CONFIGURATION_SCHEMA_REFS:
            return "Readonly<Record<string, JsonValue>>"
        return name

    if schema.get("pattern") == _SHA256_PATTERN:
        return "Sha256Digest"
    reference_alias = _REFERENCE_ALIAS_BY_PATTERN.get(schema.get("pattern"))
    if reference_alias is not None:
        return reference_alias

    enum = schema.get("enum")
    if enum is not None:
        values = tuple(enum)
        return _LITERAL_ALIAS_BY_VALUES.get(values, _literal_union(values))

    if "const" in schema:
        return _literal(schema["const"])

    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives is not None:
        rendered = tuple(dict.fromkeys(_render_type(option) for option in alternatives))
        return " | ".join(rendered)

    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type == "boolean":
        return "boolean"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        prefix_items = schema.get("prefixItems")
        if prefix_items is not None:
            return "readonly [" + ", ".join(_render_type(item) for item in prefix_items) + "]"
        return f"ReadonlyArray<{_render_type(schema['items'])}>"
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return f"Readonly<Record<string, {_render_type(additional)}>>"
        pattern_properties = schema.get("patternProperties")
        if isinstance(pattern_properties, dict) and len(pattern_properties) == 1:
            pattern, value_schema = next(iter(pattern_properties.items()))
            key_type = _REFERENCE_ALIAS_BY_PATTERN.get(pattern, "string")
            return f"Readonly<Record<{key_type}, {_render_type(value_schema)}>>"

    if not schema:
        return "JsonValue"
    raise ValueError(f"unsupported Builder API JSON Schema fragment: {schema!r}")


def _render_model(model: type[BaseModel]) -> list[str]:
    schema = model.model_json_schema()
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError(f"{model.__name__} must remain a closed object contract")

    return _render_object_schema(model.__name__, schema)


def _render_object_schema(
    name: str,
    schema: dict[str, Any],
    *,
    serialized_defaults_are_present: bool = False,
) -> list[str]:
    if schema.get("type") != "object":
        raise ValueError(f"{name} must remain an object contract")

    description = " ".join(str(schema.get("description", "")).split())
    required = (
        frozenset(schema.get("properties", {}))
        if serialized_defaults_are_present
        else frozenset(schema.get("required", ()))
    )
    lines = [f"/** {description} */", f"export interface {name} {{"]
    for name, field_schema in schema["properties"].items():
        optional = "" if name in required else "?"
        lines.append(f"  readonly {name}{optional}: {_render_type(field_schema)};")
    lines.append("}")
    return lines


def _runtime_descriptor(
    schema: dict[str, Any],
    definitions: dict[str, Any],
) -> dict[str, Any]:
    """Describe one backend application DTO's JSON runtime contract."""

    if not schema:
        return {"kind": "json"}
    reference = schema.get("$ref")
    if reference is not None:
        return _runtime_descriptor(definitions[reference.rsplit("/", 1)[-1]], definitions)

    if "const" in schema:
        return {"kind": "literal", "value": schema["const"]}
    if "enum" in schema:
        return {"kind": "enum", "values": schema["enum"]}

    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives is not None:
        return {
            "kind": "union",
            "options": [_runtime_descriptor(option, definitions) for option in alternatives],
            "exclusive": "oneOf" in schema,
        }

    intersections = schema.get("allOf")
    if intersections is not None:
        return {
            "kind": "intersection",
            "options": [_runtime_descriptor(option, definitions) for option in intersections],
        }

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return {
            "kind": "union",
            "options": [
                _runtime_descriptor({**schema, "type": option}, definitions)
                for option in schema_type
            ],
            "exclusive": False,
        }
    if schema_type == "null":
        return {"kind": "null"}
    if schema_type == "boolean":
        return {"kind": "boolean"}
    if schema_type == "string":
        descriptor: dict[str, Any] = {"kind": "string"}
        for source, target in (
            ("pattern", "pattern"),
            ("minLength", "min_length"),
            ("maxLength", "max_length"),
        ):
            if source in schema:
                descriptor[target] = schema[source]
        return descriptor
    if schema_type in {"integer", "number"}:
        descriptor = {"kind": schema_type}
        for source, target in (
            ("minimum", "minimum"),
            ("maximum", "maximum"),
            ("exclusiveMinimum", "exclusive_minimum"),
            ("exclusiveMaximum", "exclusive_maximum"),
            ("multipleOf", "multiple_of"),
        ):
            if source in schema:
                descriptor[target] = schema[source]
        return descriptor
    if schema_type == "array":
        prefix_items = schema.get("prefixItems")
        if prefix_items is not None:
            descriptor = {
                "kind": "tuple",
                "items": [_runtime_descriptor(item, definitions) for item in prefix_items],
                "rest": (
                    False
                    if schema.get("items") is False
                    else _runtime_descriptor(schema.get("items", {}), definitions)
                ),
            }
        else:
            descriptor = {
                "kind": "array",
                "items": _runtime_descriptor(schema.get("items", {}), definitions),
            }
        for source, target in (
            ("minItems", "min_items"),
            ("maxItems", "max_items"),
            ("uniqueItems", "unique"),
        ):
            if source in schema:
                descriptor[target] = schema[source]
        return descriptor
    if schema_type == "object":
        additional = schema.get("additionalProperties")
        patterns = schema.get("patternProperties", {})
        return {
            "kind": "object",
            "fields": {
                name: _runtime_descriptor(field_schema, definitions)
                for name, field_schema in schema.get("properties", {}).items()
            },
            "additional": (
                False
                if additional is False
                else _runtime_descriptor(
                    additional if isinstance(additional, dict) else {},
                    definitions,
                )
            ),
            **(
                {
                    "patterns": [
                        {
                            "pattern": pattern,
                            "values": _runtime_descriptor(value_schema, definitions),
                        }
                        for pattern, value_schema in patterns.items()
                    ]
                }
                if patterns
                else {}
            ),
        }
    raise ValueError(f"unsupported runtime descriptor schema: {schema!r}")


def _render_runtime_descriptor(
    name: str,
    model: type[BaseModel],
) -> list[str]:
    schema = model.model_json_schema()
    descriptor = _runtime_descriptor(schema, schema.get("$defs", {}))
    encoded = json.dumps(descriptor, ensure_ascii=False, indent=2, separators=(",", ": "))
    return [
        "/** Runtime validator descriptor generated from this backend application DTO. */",
        f"export const {name} = {encoded} as const satisfies BuilderVisualRuntimeDescriptor;",
    ]


def render() -> str:
    lines = [
        "// GENERATED FILE — DO NOT EDIT BY HAND.",
        "// Sources of truth: backend Pydantic application contracts in",
        "// lib/nodalarc/models and services/vs_api/transition_operations.py.",
        "// Regenerate: make generate-contracts",
        "//",
        "// These are non-grammar application contracts. Configuration fields remain",
        "// generic JSON in TypeScript and are validated by backend Pydantic models.",
        "",
        "export type JsonValue =",
        "  | null",
        "  | boolean",
        "  | number",
        "  | string",
        "  | ReadonlyArray<JsonValue>",
        "  | { readonly [key: string]: JsonValue };",
        "",
        "/** Self-describing sha256:<64 lowercase hex> content identity. */",
        "export type Sha256Digest = string;",
        "/** Namespace-qualified catalog reference validated by the backend. */",
        "export type CatalogRef = string;",
        "/** Catalog reference whose family is sessions. */",
        "export type SessionRef = CatalogRef;",
        "/** Path-free deployable source selected by the browser. */",
        "export type SessionSourceId = CatalogSessionSourceId;",
        "/** Runtime validation descriptor for generated backend application DTOs. */",
        "export type BuilderVisualRuntimeDescriptor =",
        '  | { readonly kind: "json" }',
        '  | { readonly kind: "literal"; readonly value: JsonValue }',
        '  | { readonly kind: "enum"; readonly values: ReadonlyArray<JsonValue> }',
        '  | { readonly kind: "null" }',
        '  | { readonly kind: "boolean" }',
        '  | { readonly kind: "string"; readonly pattern?: string; readonly min_length?: number; readonly max_length?: number }',
        '  | { readonly kind: "integer" | "number"; readonly minimum?: number; readonly maximum?: number; readonly exclusive_minimum?: number; readonly exclusive_maximum?: number; readonly multiple_of?: number }',
        '  | { readonly kind: "array"; readonly items: BuilderVisualRuntimeDescriptor; readonly min_items?: number; readonly max_items?: number; readonly unique?: boolean }',
        '  | { readonly kind: "tuple"; readonly items: ReadonlyArray<BuilderVisualRuntimeDescriptor>; readonly rest: false | BuilderVisualRuntimeDescriptor; readonly min_items?: number; readonly max_items?: number; readonly unique?: boolean }',
        '  | { readonly kind: "object"; readonly fields: Readonly<Record<string, BuilderVisualRuntimeDescriptor>>; readonly additional: false | BuilderVisualRuntimeDescriptor; readonly patterns?: ReadonlyArray<{ readonly pattern: string; readonly values: BuilderVisualRuntimeDescriptor }> }',
        '  | { readonly kind: "union"; readonly options: ReadonlyArray<BuilderVisualRuntimeDescriptor>; readonly exclusive: boolean }',
        '  | { readonly kind: "intersection"; readonly options: ReadonlyArray<BuilderVisualRuntimeDescriptor> };',
        "",
    ]
    for name, values in LITERAL_ALIASES:
        lines.append(f"export type {name} = {_literal_union(values)};")
    lines.append("")

    for model in MODEL_TYPES:
        lines.extend(_render_model(model))
        lines.append("")
    emitted_names = {model.__name__ for model in MODEL_TYPES}
    for model in EMBEDDED_MODEL_TYPES:
        schema = model.model_json_schema()
        definitions = schema.get("$defs", {})
        for name, definition in definitions.items():
            if name in emitted_names:
                raise ValueError(f"duplicate generated Builder API contract {name}")
            lines.extend(
                _render_object_schema(
                    name,
                    definition,
                    serialized_defaults_are_present=True,
                )
            )
            lines.append("")
            emitted_names.add(name)
        if model.__name__ in emitted_names:
            raise ValueError(f"duplicate generated Builder API contract {model.__name__}")
        lines.extend(
            _render_object_schema(
                model.__name__,
                schema,
                serialized_defaults_are_present=True,
            )
        )
        lines.append("")
        emitted_names.add(model.__name__)
    for name, model in (
        (
            "BUILDER_VISUAL_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR",
            BuilderVisualDraftEnvelope,
        ),
        (
            "CATALOG_COMPONENT_DRAFT_ENVELOPE_RUNTIME_DESCRIPTOR",
            CatalogComponentDraftEnvelope,
        ),
        ("BUILDER_VISUAL_WORKSPACE_RUNTIME_DESCRIPTOR", BuilderVisualWorkspace),
        ("BUILDER_VISUAL_SPACE_DRAFT_RUNTIME_DESCRIPTOR", BuilderVisualSpaceDraft),
        ("BUILDER_VISUAL_GROUND_DRAFT_RUNTIME_DESCRIPTOR", BuilderVisualGroundDraft),
        ("BUILDER_VISUAL_LINK_RULE_RUNTIME_DESCRIPTOR", BuilderVisualLinkRule),
        ("BUILDER_VISUAL_ROUTING_DOMAIN_RUNTIME_DESCRIPTOR", BuilderVisualRoutingDomain),
        ("BUILDER_VISUAL_ROUTING_BOUNDARY_RUNTIME_DESCRIPTOR", BuilderVisualRoutingBoundary),
    ):
        lines.extend(_render_runtime_descriptor(name, model))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated output is stale")
    args = parser.parse_args(argv)

    generated = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != generated:
            print(
                "STALE: frontend/src/builder/generated/builderApi.ts differs from "
                "lib/nodalarc/models/builder_api.py. Regenerate with "
                "make generate-contracts",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print("builderApi.ts is up to date.")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generated, encoding="utf-8")
    print(
        f"wrote {OUT.relative_to(ROOT)} "
        f"({len(MODEL_TYPES)} application roots + "
        f"{len(EMBEDDED_MODEL_TYPES)} embedded schema roots)"
    )


if __name__ == "__main__":
    main()
