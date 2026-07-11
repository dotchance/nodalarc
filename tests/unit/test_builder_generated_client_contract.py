"""Pins for the generated public Builder and transition client contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "frontend/src/builder/generated/builderApi.ts"


def test_generated_contract_exposes_visual_drafts_and_path_free_session_sources() -> None:
    generated = GENERATED.read_text(encoding="utf-8")

    assert "export interface BuilderVisualDraftEnvelope" in generated
    assert "export interface BuilderVisualDraftAssemblyResult" in generated
    assert "export interface BuilderVisualCustomizeChainRequest" in generated
    assert "export interface BuilderVisualCustomizeChainResult" in generated
    assert "export interface BuilderVisualDraftCommandRequest" in generated
    assert "export interface BuilderVisualDraftCommandResult" in generated
    assert "export interface BuilderVisualAddGeneratedSpaceCommand" in generated
    assert "export interface BuilderVisualConnectSegmentsCommand" in generated
    assert "export interface BuilderVisualRederiveLinkCommand" in generated
    assert "export interface BuilderVisualSetSchedulingPresetCommand" in generated
    assert "export type BuilderVisualDraftCommandOperation =" in generated
    assert "export type BuilderVisualSchedulingPreset =" in generated
    assert "export interface CatalogSessionSourceId" in generated
    assert "export interface CatalogSessionSummary" in generated
    assert "export interface CatalogSessionYamlUploadRequest" in generated
    assert "export interface CatalogComponentDraftEnvelope" in generated
    assert "export interface CatalogDraftPatchRequest" in generated
    assert "export interface CatalogDraftAddSiteNodeRequest" in generated
    assert "export interface CatalogDraftCompileResult" in generated
    assert "export interface CatalogDraftSaveResult" in generated
    assert "export type CatalogComponentFamily =" in generated
    assert "export type CatalogDraftPatchOperation =" in generated
    assert "export interface CoveragePreviewResult" in generated
    assert "export interface CoverageInsight" in generated
    assert "export interface WizardConstellationCapability" in generated
    assert "export interface WizardConstellationPreset" in generated
    assert "export interface WizardConstellationPresetResponse" in generated
    assert "export type WizardOrbitPropagator =" in generated
    assert "export type WizardConstellationSourceKind =" in generated
    assert "export type WizardWalkerPattern =" in generated
    assert "export interface WizardWalkerPatternMetadata" in generated
    assert "export interface WizardSatelliteTypePresetResponse" in generated
    assert "export interface WizardGroundStationSetPresetResponse" in generated
    assert "export interface WizardAvailableStationResponse" in generated
    assert "export interface WizardExtensionRulesResponse" in generated
    assert "export interface WizardProtocolMetadata" in generated
    assert "export interface WizardExtensionMetadata" in generated
    assert "export interface WizardRoutingTimerFieldMetadata" in generated
    assert "export type WizardRoutingTimerField =" in generated
    assert "WizardProtocolExtensionCatalog" not in generated
    assert "WizardProtocolExtensionRule" not in generated
    assert 'readonly severity: "info" | "note" | "warning" | "error";' in generated
    assert "export type SessionSourceId = CatalogSessionSourceId;" in generated
    assert "LegacySession" not in generated


def test_generated_application_contract_keeps_configuration_json_generic() -> None:
    generated = GENERATED.read_text(encoding="utf-8")

    assert "configurationTypes" not in generated
    assert "readonly canonical_json: Readonly<Record<string, JsonValue>>;" in generated
    assert (
        "readonly canonical_session_json?: Readonly<Record<string, JsonValue>> | null;" in generated
    )
    assert "readonly canonical_json?: Readonly<Record<string, JsonValue>> | null;" in generated
    assert "readonly session: Readonly<Record<string, JsonValue>>;" in generated
    assert "export interface BodyDocument" not in generated
    assert "export interface ConfigurationDocument" not in generated
    assert "export interface SegmentSessionConfig" not in generated


def test_generated_contract_exposes_only_public_transition_operation_evidence() -> None:
    generated = GENERATED.read_text(encoding="utf-8")

    for public_model in (
        "TransitionOperationSource",
        "TransitionOperationFacts",
        "TransitionOperationEvent",
        "TransitionOperationFailure",
        "TransitionRuntimeResult",
        "TransitionOperation",
    ):
        assert f"export interface {public_model}" in generated
    assert "export type TransitionOperationState =" in generated
    assert "export type TransitionOperationSourceKind =" in generated
    assert "StoredTransitionOperation" not in generated
    assert "TransitionOperationProvenance" not in generated
    assert "TransitionOperationReservation" not in generated
