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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# The repository root carries the adapter packages that session resolution
# reads; lib takes precedence over it.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from nodalarc.catalog_refs import (  # noqa: E402
    CatalogRef,
    SessionRef,
)
from nodalarc.models import (  # noqa: E402
    decision_explanation,
    events,
    link_decisions,
    scheduler_ops,
    vs_api,
)
from nodalarc.models.api_refusal import ApiRefusal, LegacyRefusal  # noqa: E402
from nodalarc.models.builder_api import (  # noqa: E402
    BuilderCatalogDocument,
    BuilderCompileRequest,
    BuilderCompileResult,
    BuilderDeployVerdict,
    BuilderDigests,
    BuilderDraftEnvelope,
    BuilderDraftState,
    BuilderIssue,
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
)
from nodalarc.models.builder_catalog_api import (  # noqa: E402
    BuilderCatalogBootstrap,
    BuilderCatalogCapabilities,
    BuilderVisualAuthoringFacts,
    BuilderVisualBoundaryAdapterMetadata,
    BuilderVisualForwardingClassMetadata,
    BuilderVisualLinkMediumMetadata,
    BuilderVisualMountRoleMetadata,
    BuilderVisualPhasingModeMetadata,
    BuilderVisualRoutingProtocolMetadata,
    BuilderVisualSchedulingPresetMetadata,
    BuilderVisualTopologyModeMetadata,
    CatalogComponentDraftEnvelope,
    CatalogComponentDraftState,
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
    CatalogDraftApplyYamlRequest,
    CatalogDraftApplyYamlResult,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftControlMutationRequest,
    CatalogDraftNewRequest,
    CatalogDraftOpenProposalRequest,
    CatalogDraftOpenRequest,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogDraftSessionContext,
    CatalogFamilyMetadata,
    CatalogGetRequest,
    CatalogListPage,
    CatalogListRequest,
    CatalogMutationResult,
    CatalogOperationRefusal,
    CatalogSessionYamlExport,
    CatalogSessionYamlExportRequest,
    CatalogSessionYamlImportRequest,
    CatalogSessionYamlImportResult,
    CatalogYamlFile,
    CatalogYamlImportCollision,
    CatalogYamlImportFile,
    CatalogYamlImportWrite,
)
from nodalarc.models.builder_controls_api import (  # noqa: E402
    BuilderChoiceBranch,
    BuilderChoiceControl,
    BuilderControlTree,
    BuilderInsertItemCommand,
    BuilderInsertMapEntryCommand,
    BuilderMapControl,
    BuilderMapEntry,
    BuilderMoveItemCommand,
    BuilderObjectControl,
    BuilderObjectField,
    BuilderRemoveItemCommand,
    BuilderRemoveMapEntryCommand,
    BuilderRenameMapKeyCommand,
    BuilderScalarConstraints,
    BuilderScalarControl,
    BuilderSelectChoiceCommand,
    BuilderSequenceControl,
    BuilderSequenceItem,
    BuilderSetPresentCommand,
    BuilderSetScalarCommand,
)
from nodalarc.models.builder_visual_api import (  # noqa: E402
    BuilderVisualAddBoundaryCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
    BuilderVisualAddRoutingDomainCommand,
    BuilderVisualConnectSegmentsCommand,
    BuilderVisualControlMutationRequest,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainResult,
    BuilderVisualDraftAcceptStateRequest,
    BuilderVisualDraftApplyEditsRequest,
    BuilderVisualDraftApplyEditsResult,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftApplyYamlResult,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCommandResult,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftOpenRequest,
    BuilderVisualDraftRetargetRequest,
    BuilderVisualGroundBoresight,
    BuilderVisualGroundSiteIntent,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualLinkRuleListing,
    BuilderVisualLinkRuleWidget,
    BuilderVisualMintGroundMembersCommand,
    BuilderVisualObjectControlEdit,
    BuilderVisualObjectEditRequest,
    BuilderVisualObjectEditResult,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualRederiveLinkCommand,
    BuilderVisualRoutingBoundariesWidget,
    BuilderVisualRoutingBoundary,
    BuilderVisualRoutingBoundaryListing,
    BuilderVisualRoutingDomain,
    BuilderVisualRoutingDomainListing,
    BuilderVisualRoutingDomainWidget,
    BuilderVisualSessionListing,
    BuilderVisualSetSchedulingPresetCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualWalkerLayoutRequest,
    BuilderVisualWalkerLayoutResult,
)
from nodalarc.models.builder_world import BuilderWorld  # noqa: E402
from nodalarc.models.session_sources import (  # noqa: E402
    CatalogSessionSourceId,
    CatalogSessionSummary,
    CatalogSessionSwitchAccepted,
    CatalogSessionSwitchRequest,
    CatalogSessionYamlQuery,
    CatalogSessionYamlUploadRequest,
    TransitionOperation,
    TransitionOperationEvent,
    TransitionOperationFacts,
    TransitionOperationFailure,
    TransitionOperationSource,
    TransitionRuntimeResult,
)
from nodalarc.project_info import ProjectAbout  # noqa: E402
from pydantic import BaseModel, TypeAdapter  # noqa: E402
from pydantic.json_schema import GenerateJsonSchema  # noqa: E402

BUILDER_OUT = ROOT / "frontend/src/builder/generated/builderApi.ts"
RUNTIME_OUT = ROOT / "frontend/src/generated/vsApi.ts"

# The Builder, catalog and session-switch contracts.
BUILDER_ROOTS: tuple[type[BaseModel], ...] = (
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
    BuilderSessionDeployRequest,
    BuilderSessionDeployAccepted,
    BuilderSessionDeployRefusal,
    BuilderSessionSaveRequest,
    BuilderSessionSaveResult,
    BuilderSessionSaveRefusal,
    BuilderVisualControlMutationRequest,
    BuilderVisualCustomizeChainRequest,
    BuilderVisualCustomizeChainEntry,
    BuilderVisualCustomizeChainResult,
    BuilderVisualPlaceSpaceReferenceCommand,
    BuilderVisualPlaceGroundReferenceCommand,
    BuilderVisualAddGeneratedSpaceCommand,
    BuilderVisualSetSpacePopulationCommand,
    BuilderVisualAddGroundSiteReferenceCommand,
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
    BuilderVisualGroundBoresight,
    BuilderVisualLinkEndpoint,
    BuilderVisualLinkRule,
    BuilderVisualRoutingDomain,
    BuilderVisualRoutingBoundary,
    BuilderVisualDraftEnvelope,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftOpenRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftApplyYamlResult,
    BuilderVisualDraftRetargetRequest,
    BuilderVisualDraftCompileRequest,
    BuilderVisualLinkRuleListing,
    BuilderVisualRoutingDomainListing,
    BuilderVisualRoutingBoundaryListing,
    BuilderVisualSessionListing,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualLinkRuleWidget,
    BuilderVisualRoutingDomainWidget,
    BuilderVisualRoutingBoundariesWidget,
    BuilderVisualObjectControlEdit,
    BuilderVisualObjectEditRequest,
    BuilderVisualObjectEditResult,
    BuilderVisualDraftApplyEditsRequest,
    BuilderVisualDraftApplyEditsResult,
    BuilderVisualDraftAcceptStateRequest,
    ApiRefusal,
    CatalogSessionSourceId,
    CatalogSessionSummary,
    CatalogSessionSwitchRequest,
    CatalogSessionSwitchAccepted,
    CatalogSessionYamlUploadRequest,
    CatalogSessionYamlQuery,
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
    BuilderVisualTopologyModeMetadata,
    BuilderVisualAuthoringFacts,
    BuilderCatalogBootstrap,
    BuilderScalarConstraints,
    BuilderScalarControl,
    BuilderObjectField,
    BuilderObjectControl,
    BuilderChoiceBranch,
    BuilderChoiceControl,
    BuilderSequenceItem,
    BuilderSequenceControl,
    BuilderMapEntry,
    BuilderMapControl,
    BuilderControlTree,
    BuilderSetScalarCommand,
    BuilderSetPresentCommand,
    BuilderSelectChoiceCommand,
    BuilderInsertItemCommand,
    BuilderRemoveItemCommand,
    BuilderMoveItemCommand,
    BuilderInsertMapEntryCommand,
    BuilderRemoveMapEntryCommand,
    BuilderRenameMapKeyCommand,
    CatalogListRequest,
    CatalogDocumentSummary,
    CatalogListPage,
    CatalogGetRequest,
    CatalogDocumentWriteRequest,
    CatalogDraftAddNodeTerminalMountRequest,
    CatalogDraftAddNodeEthernetPortRequest,
    CatalogDraftAddSiteNodeRequest,
    CatalogComponentDraftState,
    CatalogComponentDraftEnvelope,
    CatalogDraftNewRequest,
    CatalogDraftOpenProposalRequest,
    CatalogDraftOpenRequest,
    CatalogDraftSessionContext,
    CatalogDraftControlMutationRequest,
    CatalogDraftApplyYamlRequest,
    CatalogDraftApplyYamlResult,
    CatalogDraftCompileRequest,
    CatalogDraftCompileResult,
    CatalogDraftSaveRequest,
    CatalogDraftSaveResult,
    CatalogDependentsRequest,
    CatalogDependent,
    CatalogDependencyImpact,
    CatalogMutationResult,
    CatalogDeleteRequest,
    CatalogDeleteResult,
    CatalogSessionYamlExportRequest,
    CatalogYamlFile,
    CatalogSessionYamlExport,
    CatalogYamlImportFile,
    CatalogSessionYamlImportRequest,
    CatalogYamlImportWrite,
    CatalogYamlImportCollision,
    CatalogSessionYamlImportResult,
    CatalogOperationRefusal,
    BuilderWorld,
)

# The runtime contracts: the state socket and terminal socket messages, and every
# other route's request, query and answer.
RUNTIME_ROOTS: tuple[tuple[str, Any], ...] = (
    ("StateFeedFrame", vs_api.StateFeedFrame),
    ("StateSocketCommand", vs_api.StateSocketCommand),
    ("TerminalMessage", vs_api.TerminalMessage),
    ("SocketCloseCode", vs_api.SocketCloseCode),
    ("DebugSourceControl", vs_api.DebugSourceControl),
    ("HealthStatus", vs_api.HealthStatus),
    ("ProjectAbout", ProjectAbout),
    ("AuthToken", vs_api.AuthToken),
    ("LegacyRefusal", LegacyRefusal),
    ("ActuationHealth", vs_api.ActuationHealth),
    ("StampedOpsEvent", vs_api.StampedOpsEvent),
    ("OpsEventsQuery", vs_api.OpsEventsQuery),
    ("OperatorRepairRequest", scheduler_ops.OperatorRepairRequest),
    ("OperatorRepairResponse", scheduler_ops.OperatorRepairResponse),
    ("LinkHistoryPage", vs_api.LinkHistoryPage),
    ("LinkHistoryQuery", vs_api.LinkHistoryQuery),
    ("LinkDecisionTrace", vs_api.LinkDecisionTrace),
    ("LinkDecisionTracesQuery", vs_api.LinkDecisionTracesQuery),
    ("DecisionExplanationFacts", decision_explanation.DecisionExplanationFacts),
    ("DecisionExplanationQuery", vs_api.DecisionExplanationQuery),
    ("GsDecisionTimelineFacts", decision_explanation.GsDecisionTimelineFacts),
    ("DecisionTimelineQuery", vs_api.DecisionTimelineQuery),
    ("GroundLinkDecisionSnapshot", link_decisions.GroundLinkDecisionSnapshot),
    ("GroundLinkDecisionsQuery", vs_api.GroundLinkDecisionsQuery),
    ("TraceRequest", vs_api.TraceRequest),
    ("TracedPath", vs_api.TracedPath),
    ("ContinuousTraceStarted", vs_api.ContinuousTraceStarted),
    ("ContinuousTraceStopped", vs_api.ContinuousTraceStopped),
    ("ContinuousTraceStatus", vs_api.ContinuousTraceStatus),
    ("PlaybackControlCommand", events.PlaybackControlCommand),
    ("PlaybackState", events.PlaybackState),
    ("IntrospectRequest", vs_api.IntrospectRequest),
    ("IntrospectResult", vs_api.IntrospectResult),
)

# Every model a client sends: route bodies, query strings and socket messages.
# A definition any of them reaches renders a field with a default as optional;
# a definition only answers reach renders every field, since VS-API sends them all.
REQUEST_ROOTS: frozenset[str] = frozenset(
    {
        "BuilderCompileRequest",
        "BuilderSessionDeployRequest",
        "BuilderSessionSaveRequest",
        "BuilderVisualControlMutationRequest",
        "BuilderVisualCustomizeChainRequest",
        "BuilderVisualDraftAcceptStateRequest",
        "BuilderVisualDraftApplyEditsRequest",
        "BuilderVisualDraftApplyYamlRequest",
        "BuilderVisualDraftCommandRequest",
        "BuilderVisualDraftCompileRequest",
        "BuilderVisualDraftCreateRequest",
        "BuilderVisualDraftOpenRequest",
        "BuilderVisualDraftRetargetRequest",
        "BuilderVisualObjectEditRequest",
        "BuilderVisualWalkerLayoutRequest",
        "CatalogDeleteRequest",
        "CatalogDependentsRequest",
        "CatalogDraftAddNodeEthernetPortRequest",
        "CatalogDraftAddNodeTerminalMountRequest",
        "CatalogDraftAddSiteNodeRequest",
        "CatalogDraftApplyYamlRequest",
        "CatalogDraftCompileRequest",
        "CatalogDraftControlMutationRequest",
        "CatalogDraftNewRequest",
        "CatalogDraftOpenProposalRequest",
        "CatalogDraftOpenRequest",
        "CatalogDraftSaveRequest",
        "CatalogGetRequest",
        "CatalogListRequest",
        "CatalogSessionSwitchRequest",
        "CatalogSessionYamlExportRequest",
        "CatalogSessionYamlImportRequest",
        "CatalogSessionYamlQuery",
        "CatalogSessionYamlUploadRequest",
        "DecisionExplanationQuery",
        "DecisionTimelineQuery",
        "GroundLinkDecisionsQuery",
        "IntrospectRequest",
        "LinkDecisionTracesQuery",
        "LinkHistoryQuery",
        "OperatorRepairRequest",
        "OpsEventsQuery",
        "PlaybackControlCommand",
        "StateSocketCommand",
        "TerminalMessage",
        "TraceRequest",
    }
)


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


# The generated names one module's types refer to, collected while it renders.
_used_names: set[str] = set()


def _named(name: str) -> str:
    _used_names.add(name)
    return name


def _render_type(schema: dict[str, Any]) -> str:
    reference = schema.get("$ref")
    if reference is not None:
        name = reference.rsplit("/", 1)[-1]
        if name in _GENERIC_CONFIGURATION_SCHEMA_REFS:
            return f"Readonly<Record<string, {_named('JsonValue')}>>"
        return _named(name)

    if schema.get("pattern") == _SHA256_PATTERN:
        return _named("Sha256Digest")
    reference_alias = _REFERENCE_ALIAS_BY_PATTERN.get(schema.get("pattern"))
    if reference_alias is not None:
        return _named(reference_alias)

    enum = schema.get("enum")
    if enum is not None:
        return _literal_union(enum)

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
            key_type = _REFERENCE_ALIAS_BY_PATTERN.get(pattern)
            key = _named(key_type) if key_type is not None else "string"
            return f"Readonly<Record<{key}, {_render_type(value_schema)}>>"
        if not schema.get("properties"):
            # A free-form object: any JSON object.
            return f"Readonly<Record<string, {_named('JsonValue')}>>"

    if not schema:
        return _named("JsonValue")
    raise ValueError(f"unsupported Builder API JSON Schema fragment: {schema!r}")


def _root_schemas() -> tuple[dict[str, Any], dict[str, dict[str, Any]], frozenset[str]]:
    """One schema pass over every root: each root's schema by name, every shared
    definition once, and the names of the Builder roots."""

    roots: list[tuple[str, Any]] = [(model.__name__, model) for model in BUILDER_ROOTS]
    roots.extend(RUNTIME_ROOTS)
    names = [name for name, _ in roots]
    if len(set(names)) != len(names):
        raise ValueError("a generated root is listed twice")
    unknown_requests = REQUEST_ROOTS - set(names)
    if unknown_requests:
        raise ValueError(f"request roots that are not generated: {sorted(unknown_requests)}")
    generator = GenerateJsonSchema(ref_template="#/$defs/{model}")
    by_key, definitions = generator.generate_definitions(
        [(name, "validation", TypeAdapter(root).core_schema) for name, root in roots]
    )
    schemas = {name: by_key[(name, "validation")] for name in names}
    return schemas, definitions, frozenset(model.__name__ for model in BUILDER_ROOTS)


def _references(schema: Any) -> set[str]:
    """The definitions a schema refers to directly."""

    found: set[str] = set()
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str):
            found.add(reference.rsplit("/", 1)[-1])
        for value in schema.values():
            found |= _references(value)
    elif isinstance(schema, list):
        for value in schema:
            found |= _references(value)
    return found


def _reach(start: dict[str, Any], definitions: dict[str, dict[str, Any]]) -> set[str]:
    """Every definition the given schemas reach. Configuration documents are generic
    JSON on the page, so the walk does not enter them."""

    reached: set[str] = set()
    pending = [name for schema in start.values() for name in _references(schema)]
    while pending:
        name = pending.pop()
        if name in reached or name in _GENERIC_CONFIGURATION_SCHEMA_REFS:
            continue
        reached.add(name)
        pending.extend(_references(definitions[name]))
    return reached


def _render_definition(
    name: str, schema: dict[str, Any], *, every_field_present: bool
) -> list[str]:
    description = " ".join(str(schema.get("description", "")).split())
    if schema.get("type") != "object":
        # A named value set: an enumeration, or a `type` alias of values or of other sets.
        rendered = _render_type(schema)
        return [f"/** {description} */", f"export type {name} = {rendered};"]
    if schema.get("additionalProperties") is not False:
        raise ValueError(f"{name} must remain a closed object contract")
    properties = schema.get("properties", {})
    required = (
        frozenset(properties) if every_field_present else frozenset(schema.get("required", ()))
    )
    lines = [f"/** {description} */", f"export interface {name} {{"]
    for field, field_schema in properties.items():
        optional = "" if field in required else "?"
        lines.append(f"  readonly {field}{optional}: {_render_type(field_schema)};")
    lines.append("}")
    return lines


_HEADER = [
    "// GENERATED FILE — DO NOT EDIT BY HAND.",
    "// Sources of truth: backend Pydantic application contracts in",
    "// lib/nodalarc/models.",
    "// Regenerate: make generate-contracts",
    "//",
]

# Names the Builder module declares by hand: JSON values and the reference aliases.
_BUILDER_PRELUDE_NAMES = frozenset(
    {"JsonValue", "Sha256Digest", "CatalogRef", "SessionRef", "SessionSourceId"}
)


def render() -> dict[Path, str]:
    """The generated text of each output module."""

    schemas, definitions, builder_names = _root_schemas()
    requested = _reach({name: schemas[name] for name in REQUEST_ROOTS}, definitions)
    in_builder = _reach({name: schemas[name] for name in builder_names}, definitions)
    in_runtime = _reach(schemas, definitions) - in_builder

    def definitions_of(names: set[str]) -> list[str]:
        lines: list[str] = []
        for name in definitions:
            if name not in names or name == "JsonValue":
                continue
            lines.extend(
                _render_definition(
                    name, definitions[name], every_field_present=name not in requested
                )
            )
            lines.append("")
        return lines

    _used_names.clear()
    builder = [
        *_HEADER,
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
        "",
    ]
    builder.extend(definitions_of(in_builder))

    _used_names.clear()
    runtime_body: list[str] = []
    for name, _root in RUNTIME_ROOTS:
        if "$ref" not in schemas[name]:
            runtime_body.append(f"export type {name} = {_render_type(schemas[name])};")
            runtime_body.append("")
    runtime_body.extend(definitions_of(in_runtime))
    from_builder = sorted(
        name for name in _used_names if name in in_builder or name in _BUILDER_PRELUDE_NAMES
    )
    runtime = [*_HEADER, "// The runtime contracts VS-API serves and the clients it accepts.", ""]
    if from_builder:
        runtime.append("import type {")
        runtime.extend(f"  {name}," for name in from_builder)
        runtime.append('} from "../builder/generated/builderApi";')
        runtime.append("")
    runtime.extend(runtime_body)
    return {
        BUILDER_OUT: "\n".join(builder).rstrip() + "\n",
        RUNTIME_OUT: "\n".join(runtime).rstrip() + "\n",
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated output is stale")
    args = parser.parse_args(argv)

    generated = render()
    if args.check:
        stale = [
            path.relative_to(ROOT)
            for path, text in generated.items()
            if (path.read_text(encoding="utf-8") if path.exists() else "") != text
        ]
        if stale:
            print(
                f"STALE: {', '.join(map(str, stale))} differ from lib/nodalarc/models. "
                "Regenerate with make generate-contracts",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print("generated contracts are up to date.")
        return

    for path, text in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
