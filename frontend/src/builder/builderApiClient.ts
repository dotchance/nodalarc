import { REST_URL, authHeaders } from "../config";
import type {
  BuilderCatalogBootstrap,
  BuilderCatalogDocument,
  BuilderCompileResult,
  BuilderSessionDeployAccepted,
  BuilderSessionDeployRequest,
  BuilderSessionSaveRequest,
  BuilderSessionSaveResult,
  BuilderVisualCustomizeChainRequest,
  BuilderVisualCustomizeChainResult,
  BuilderVisualControlMutationRequest,
  BuilderVisualDraftAssemblyResult,
  BuilderVisualDraftApplyWorkspaceRequest,
  BuilderVisualDraftApplyYamlRequest,
  BuilderVisualDraftApplyYamlResult,
  BuilderVisualDraftCommandRequest,
  BuilderVisualDraftCommandResult,
  BuilderVisualDraftCompileRequest,
  BuilderVisualDraftCreateRequest,
  BuilderVisualDraftEnvelope,
  BuilderVisualDraftOpenRequest,
  BuilderVisualDraftRetargetRequest,
  BuilderVisualWalkerLayoutRequest,
  BuilderVisualWalkerLayoutResult,
  CatalogComponentDraftEnvelope,
  CatalogDeleteRequest,
  CatalogDeleteResult,
  CatalogDependentsRequest,
  CatalogDependencyImpact,
  CatalogDraftAddNodeEthernetPortRequest,
  CatalogDraftAddNodeTerminalMountRequest,
  CatalogDraftAddSiteNodeRequest,
  CatalogDraftApplyYamlRequest,
  CatalogDraftApplyYamlResult,
  CatalogDraftCompileRequest,
  CatalogDraftCompileResult,
  CatalogDraftControlMutationRequest,
  CatalogDraftNewRequest,
  CatalogDraftOpenRequest,
  CatalogDraftPatchRequest,
  CatalogDraftSaveRequest,
  CatalogDraftSaveResult,
  CatalogGetRequest,
  CatalogListPage,
  CatalogListRequest,
  CatalogSessionYamlExport,
  CatalogSessionYamlExportRequest,
  CatalogSessionYamlImportRequest,
  CatalogSessionYamlImportResult,
  CoveragePreviewResult,
  TransitionOperation,
  WizardCompileRequest,
  WizardAvailableStationResponse,
  WizardConstellationPresetResponse,
  WizardCoverageRequest,
  WizardExtensionRulesResponse,
  WizardGroundStationSetPresetResponse,
  WizardSatelliteTypePresetResponse,
} from "./generated/builderApi";

export class BuilderApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "BuilderApiError";
    this.status = status;
    this.detail = detail;
  }
}

function errorMessage(payload: unknown, status: number): string {
  const value =
    payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : payload;
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.message === "string") return record.message;
    if (typeof record.error === "string") return record.error;
  }
  return `request failed (${status})`;
}

async function apiRequest<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${REST_URL}/api/v1${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: authHeaders(body === undefined ? undefined : { "Content-Type": "application/json" }),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new BuilderApiError(response.status, errorMessage(payload, response.status), payload);
  }
  return (await response.json()) as T;
}

function request<T>(path: string, body?: unknown): Promise<T> {
  return apiRequest(`/builder${path}`, body);
}

export function getBuilderBootstrap(): Promise<BuilderCatalogBootstrap> {
  return request("/bootstrap");
}

export function createCatalogDraft(
  input: CatalogDraftNewRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/new", input);
}

export function openCatalogDraft(
  input: CatalogDraftOpenRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/open", input);
}

export function patchCatalogDraft(
  input: CatalogDraftPatchRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/patch", input);
}

export function mutateCatalogDraftControls(
  input: CatalogDraftControlMutationRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/controls/mutate", input);
}

export function addCatalogDraftSiteNode(
  input: CatalogDraftAddSiteNodeRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/site-node/add", input);
}

export function addCatalogDraftNodeTerminal(
  input: CatalogDraftAddNodeTerminalMountRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/node-terminal/add", input);
}

export function addCatalogDraftNodeEthernet(
  input: CatalogDraftAddNodeEthernetPortRequest,
): Promise<CatalogComponentDraftEnvelope> {
  return request("/catalog/draft/node-ethernet/add", input);
}

export function applyCatalogDraftYaml(
  input: CatalogDraftApplyYamlRequest,
): Promise<CatalogDraftApplyYamlResult> {
  return request("/catalog/draft/apply-yaml", input);
}

export function compileCatalogDraft(
  input: CatalogDraftCompileRequest,
): Promise<CatalogDraftCompileResult> {
  return request("/catalog/draft/compile", input);
}

export function saveCatalogDraft(
  input: CatalogDraftSaveRequest,
): Promise<CatalogDraftSaveResult> {
  return request("/catalog/draft/save", input);
}

export function createVisualDraft(
  input: BuilderVisualDraftCreateRequest,
): Promise<BuilderVisualDraftEnvelope> {
  return request("/draft/new", input);
}

export function openVisualDraft(
  input: BuilderVisualDraftOpenRequest,
): Promise<BuilderVisualDraftEnvelope> {
  return request("/draft/open", input);
}

export function compileVisualDraft(
  input: BuilderVisualDraftCompileRequest,
): Promise<BuilderVisualDraftAssemblyResult> {
  return request("/draft/compile", input);
}

export function applyVisualDraftYaml(
  input: BuilderVisualDraftApplyYamlRequest,
): Promise<BuilderVisualDraftApplyYamlResult> {
  return request("/draft/apply-yaml", input);
}

export function applyVisualDraftWorkspace(
  input: BuilderVisualDraftApplyWorkspaceRequest,
): Promise<BuilderVisualDraftAssemblyResult> {
  return request("/draft/apply-workspace", input);
}

export function mutateVisualDraftControls(
  input: BuilderVisualControlMutationRequest,
): Promise<BuilderVisualDraftAssemblyResult> {
  return request("/draft/control-mutate", input);
}

export function retargetVisualDraft(
  input: BuilderVisualDraftRetargetRequest,
): Promise<BuilderVisualDraftAssemblyResult> {
  return request("/draft/retarget", input);
}

export function applyVisualDraftCommand(
  input: BuilderVisualDraftCommandRequest,
): Promise<BuilderVisualDraftCommandResult> {
  return request("/draft/command", input);
}

export function deriveVisualWalkerLayout(
  input: BuilderVisualWalkerLayoutRequest,
): Promise<BuilderVisualWalkerLayoutResult> {
  return request("/defaults/walker-layout", input);
}

export function customizeVisualDraftChain(
  input: BuilderVisualCustomizeChainRequest,
): Promise<BuilderVisualCustomizeChainResult> {
  return request("/draft/customize-chain", input);
}

export function compileWizardSession(input: WizardCompileRequest): Promise<BuilderCompileResult> {
  return request("/wizard/compile", input);
}

export function getWizardConstellationPresets(): Promise<WizardConstellationPresetResponse> {
  return apiRequest("/presets/constellations");
}

export function getWizardSatelliteTypes(): Promise<WizardSatelliteTypePresetResponse> {
  return apiRequest("/presets/satellite-types");
}

export function getWizardGroundStationSets(): Promise<WizardGroundStationSetPresetResponse> {
  return apiRequest("/presets/ground-stations");
}

export function getWizardAvailableStations(): Promise<WizardAvailableStationResponse> {
  return apiRequest("/presets/ground-stations/stations");
}

export function getWizardExtensionRules(): Promise<WizardExtensionRulesResponse> {
  return apiRequest("/wizard/extensions");
}

export function previewWizardCoverage(
  input: WizardCoverageRequest,
): Promise<CoveragePreviewResult> {
  return apiRequest("/session/preview-coverage", input);
}

export function getSessionTransition(operationId: string): Promise<TransitionOperation> {
  return apiRequest(`/session-transitions/${encodeURIComponent(operationId)}`);
}

export function saveBuilderSession(
  input: BuilderSessionSaveRequest,
): Promise<BuilderSessionSaveResult> {
  return request("/session/save", input);
}

export function deployBuilderSession(
  input: BuilderSessionDeployRequest,
): Promise<BuilderSessionDeployAccepted> {
  return request("/session/deploy", input);
}

export function listCatalog(input: CatalogListRequest): Promise<CatalogListPage> {
  return request("/catalog/list", input);
}

export function getCatalogDocument(input: CatalogGetRequest): Promise<BuilderCatalogDocument> {
  return request("/catalog/get", input);
}

export function getCatalogDependents(
  input: CatalogDependentsRequest,
): Promise<CatalogDependencyImpact> {
  return request("/catalog/dependents", input);
}

export function deleteCatalogDocument(input: CatalogDeleteRequest): Promise<CatalogDeleteResult> {
  return request("/catalog/delete", input);
}

export function exportCatalogSessionYaml(
  input: CatalogSessionYamlExportRequest,
): Promise<CatalogSessionYamlExport> {
  return request("/session/yaml/export", input);
}

export function importCatalogSessionYaml(
  input: CatalogSessionYamlImportRequest,
): Promise<CatalogSessionYamlImportResult> {
  return request("/session/yaml/import", input);
}
