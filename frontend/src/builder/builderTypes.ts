// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Builder presentation types.
 *
 * Render-world DTOs come directly from the generated Builder API contract.
 * Only the small UI error projection remains local because it is assembled
 * from typed compile findings rather than transported as a backend DTO.
 */

export type {
  BuilderLinkEndpoint,
  BuilderLinkRule,
  BuilderNodeInterfaceFacts,
  BuilderPreviewPair,
  BuilderPreviewReasonCount,
  BuilderRuleAllocation,
  BuilderRulePreview,
  BuilderWorld,
  BuilderWorldNode,
  BuilderWorldSegment,
  ResolvedOriginatedPrefixes,
  ResolvedInterfaceAddress,
  ResolvedNodeInterfaces,
  ResolvedSurfacePosition,
  ResolvedTerminalBlock,
  SessionEphemeris,
  SessionMeta as BuilderSessionMeta,
} from "./generated/builderApi";

export interface BuilderErrorSubject {
  kind: string;
  id: string;
}

export interface BuilderUnsupportedFeature {
  category: string;
  value: string;
  message: string;
  support_note: string | null;
}

export interface BuilderResolveError {
  error: string;
  subject?: BuilderErrorSubject;
  segment_id?: string;
  node_id?: string;
  features?: BuilderUnsupportedFeature[];
}
