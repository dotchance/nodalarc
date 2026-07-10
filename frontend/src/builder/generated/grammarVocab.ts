// GENERATED FILE — DO NOT EDIT BY HAND.
// Source of truth: lib/nodalarc/models/*.py Literal grammar enums.
// Regenerate: uv run python scripts/gen_builder_grammar_vocab.py
//
// This aligns shared enums/vocabulary between the frontend and the Python
// grammar. It does NOT make the browser parser/serializer authoritative and
// does NOT prove full grammar parity.

/** catalog.TerminalMedium */
export const TERMINAL_MEDIUM = ["rf", "optical"] as const;
export type TerminalMedium = (typeof TERMINAL_MEDIUM)[number];

/** link_rules.MountRole */
export const MOUNT_ROLE = ["access", "isl", "crosslink", "backbone"] as const;
export type MountRole = (typeof MOUNT_ROLE)[number];

/** catalog.ForwardingClass */
export const FORWARDING_CLASS = ["routed", "host", "bridge", "control_only"] as const;
export type ForwardingClass = (typeof FORWARDING_CLASS)[number];

/** catalog.Propagator */
export const PROPAGATOR = ["two_body", "j2_mean_elements", "sgp4_tle", "crtbp"] as const;
export type Propagator = (typeof PROPAGATOR)[number];

/** catalog.PhasingMode */
export const PHASING_MODE = ["walker_delta", "walker_star", "evenly_spaced_mean_anomaly"] as const;
export type PhasingMode = (typeof PHASING_MODE)[number];

/** catalog.BoresightMode */
export const BORESIGHT_MODE = ["local_vertical", "configured_topocentric", "steerable_envelope"] as const;
export type BoresightMode = (typeof BORESIGHT_MODE)[number];

/** catalog.LagrangePoint */
export const LAGRANGE_POINT = ["l1", "l2", "l3", "l4", "l5"] as const;
export type LagrangePoint = (typeof LAGRANGE_POINT)[number];

/** link_rules.LinkMedium */
export const LINK_MEDIUM = ["rf", "optical", "terrestrial", "mixed"] as const;
export type LinkMedium = (typeof LINK_MEDIUM)[number];

/** link_rules.LinkLabel */
export const LINK_LABEL = ["access", "isl", "relay", "backbone", "inter_body"] as const;
export type LinkLabel = (typeof LINK_LABEL)[number];

/** link_rules.LinkRelation */
export const LINK_RELATION = ["intra_segment", "inter_segment", "inter_body"] as const;
export type LinkRelation = (typeof LINK_RELATION)[number];

/** link_rules topology variants .mode */
export const TOPOLOGY_MODE = ["visible_candidates", "nearest_visible", "nearest_n", "explicit_pairs"] as const;
export type TopologyMode = (typeof TOPOLOGY_MODE)[number];

/** ground_policy.SelectionPolicyName */
export const SELECTION_POLICY_NAME = ["highest-elevation", "lowest-elevation", "longest-remaining-pass"] as const;
export type SelectionPolicyName = (typeof SELECTION_POLICY_NAME)[number];

/** ground_policy.HandoverPolicyName */
export const HANDOVER_POLICY_NAME = ["hysteresis", "none"] as const;
export type HandoverPolicyName = (typeof HANDOVER_POLICY_NAME)[number];

/** segments.GroundScheduling.handover_mode */
export const HANDOVER_MODE = ["mbb", "bbm"] as const;
export type HandoverMode = (typeof HANDOVER_MODE)[number];

/** segments.GroundScheduling.handover_concurrency */
export const HANDOVER_CONCURRENCY = ["one_at_a_time", "all_at_once"] as const;
export type HandoverConcurrency = (typeof HANDOVER_CONCURRENCY)[number];

/** ground_policy.RankingComponent */
export const RANKING_COMPONENT = ["service_priority", "selection_score", "per_gs_rank", "satellite_ground_terminal_capacity", "lex_pair"] as const;
export type RankingComponent = (typeof RANKING_COMPONENT)[number];

/** ground_policy.MbbPreemptionPolicy */
export const MBB_PREEMPTION_POLICY = ["off"] as const;
export type MbbPreemptionPolicy = (typeof MBB_PREEMPTION_POLICY)[number];

/** ground_policy.SuccessorAbortPolicy */
export const SUCCESSOR_ABORT_POLICY = ["hard_release", "soft_retain"] as const;
export type SuccessorAbortPolicy = (typeof SUCCESSOR_ABORT_POLICY)[number];

/** ground_policy.CrossTenantDisplacementPolicy */
export const CROSS_TENANT_DISPLACEMENT_POLICY = ["off"] as const;
export type CrossTenantDisplacementPolicy = (typeof CROSS_TENANT_DISPLACEMENT_POLICY)[number];

/** link_decisions.GroundHandoverModeName */
export const GROUND_HANDOVER_MODE = ["bbm", "mbb"] as const;
export type GroundHandoverMode = (typeof GROUND_HANDOVER_MODE)[number];
