// Copyright 2024-2026 .chance (dotchance)
// Licensed under the Apache License, Version 2.0. See LICENSE file.
/** Floating popover showing selected node info on the globe view. */

import { Button, IconButton } from "../ui/Button";
import { KeyValueRow } from "../ui/KeyValueRow";
import { Icon } from "../ui/icons/Icon";
import { TaxonomyChip } from "../ui/Badge";
import { REGIME_TINT, type Regime } from "../taxonomy/regime";
import type { StateSnapshot, Selection } from "../types";
import { instanceDetail, instanceLabel, roleLabel } from "../routing/instances";

interface NodePopoverProps {
  snapshot: StateSnapshot | null;
  selection: Selection;
  regime?: Regime;
  onClose: () => void;
  onOpenCli: () => void;
}

export function NodePopover({ snapshot, selection, regime, onClose, onOpenCli }: NodePopoverProps) {
  const node = snapshot?.nodes.find((n) => n.node_id === selection.id) ?? null;


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
          <KeyValueRow label="Type" mono={false}>
            {node.node_type === "ground_station" ? "Ground station" : "Satellite"}
          </KeyValueRow>
          <KeyValueRow label="Role" mono={false}>{roleLabel(node.role)}</KeyValueRow>
          {node.routing_instances.map((instance) => (
            <KeyValueRow
              key={instance.domain_id}
              label={instanceLabel({ domainId: instance.domain_id, protocol: instance.protocol })}
            >
              {instanceDetail(instance)}
            </KeyValueRow>
          ))}
          <KeyValueRow label="Neighbors">{node.isl_count} ISL, {node.gnd_count} GND</KeyValueRow>
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
