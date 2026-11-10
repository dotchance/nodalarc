// Copyright 2024-2026 .chance (dotchance)
// SPDX-License-Identifier: AGPL-3.0-only
/** A node's routing role and the routing instances it participates in. */

import { instanceDetail, instanceLabel, roleLabel } from "../routing/instances";
import type { NodeState } from "../generated/vsApi";

export function RoutingRows({ node }: { node: Pick<NodeState, "role" | "routing_instances"> }) {
  return (
    <>
      <div className="detail-row">
        <span className="detail-label">Role</span>
        <span className="detail-value" data-testid="routing-role">{roleLabel(node.role)}</span>
      </div>
      {node.routing_instances.length === 0 ? (
        <div className="detail-row">
          <span className="detail-label">Routing</span>
          <span className="detail-value">no routing instance</span>
        </div>
      ) : (
        node.routing_instances.map((instance) => (
          <div className="detail-row" key={instance.domain_id}>
            <span className="detail-label">
              {instanceLabel({ domainId: instance.domain_id, protocol: instance.protocol })}
            </span>
            <span className="detail-value" data-testid={`routing-instance-${instance.domain_id}`}>
              {instanceDetail(instance)}
            </span>
          </div>
        ))
      )}
    </>
  );
}
