// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Floating popover showing selected node info on the globe view. */

import { hexToCSS, AREA_COLORS, UNKNOWN_TINT } from "../config";
import { Button, IconButton } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import { Icon } from "../ui/icons/Icon";
import { TaxonomyChip } from "../ui/Badge";
import { REGIME_TINT, type Regime } from "../taxonomy/regime";
import type { StateSnapshot, Selection } from "../types";
import { isGroundLinkState } from "../networkIdentity";

interface NodePopoverProps {
  snapshot: StateSnapshot | null;
  selection: Selection;
  regime?: Regime;
  onClose: () => void;
  onOpenCli: () => void;
}

export function NodePopover({ snapshot, selection, regime, onClose, onOpenCli }: NodePopoverProps) {
  const node = snapshot?.nodes.find((n) => n.node_id === selection.id) ?? null;

  const connectedLinks = (node && snapshot) ? snapshot.links.filter(
    (l) => l.node_a === node.node_id || l.node_b === node.node_id,
  ) : [];
  const gndCount = connectedLinks.filter((l) => isGroundLinkState(l)).length;
  const islCount = connectedLinks.length - gndCount;

  let role = "";
  if (node) {
    const linkedAreas = new Set<string>();
    for (const l of connectedLinks) {
      const peer = snapshot!.nodes.find(
        (n) => n.node_id === (l.node_a === node.node_id ? l.node_b : l.node_a),
      );
      if (peer?.routing_area) linkedAreas.add(peer.routing_area);
    }
    if (node.routing_area) linkedAreas.add(node.routing_area);
    role = node.node_type === "ground_station"
      ? "Gateway"
      : linkedAreas.size > 1 ? "Router (ABR)" : "Router";
  }

  // Same area palette the scene materials use — popover and globe must agree.
  const areaColor = hexToCSS(
    node?.routing_area ? (AREA_COLORS[node.routing_area] ?? UNKNOWN_TINT) : UNKNOWN_TINT,
  );

  return (
    <div className="node-popover">
      <div className="node-popover-head">
        <span className="object-head-icon">
          <Icon name={node?.node_type === "ground_station" ? "satellite-dish" : "satellite"} size={14} />
        </span>
        <span className="node-popover-title">{node?.node_id ?? selection.id}</span>
        {regime && regime !== "unknown" && (
          <TaxonomyChip color={REGIME_TINT[regime].css}>{REGIME_TINT[regime].label}</TaxonomyChip>
        )}
        <IconButton icon="x" label="Close" onClick={onClose} />
      </div>
      {node ? (
        <>
          <KeyValueRow label="Type" mono={false}>{role}</KeyValueRow>
          <KeyValueRow label="Routing area">
            <span style={{ color: areaColor }}>{node.routing_area ?? "none"}</span>
          </KeyValueRow>
          <KeyValueRow label="Neighbors">{islCount} ISL, {gndCount} GND</KeyValueRow>
          <Button icon="terminal" className="node-popover-cli" onClick={onOpenCli}>
            Open CLI
          </Button>
        </>
      ) : (
        <div className="node-popover-missing">Node not in snapshot</div>
      )}
    </div>
  );
}
