#!/bin/bash
# Copyright 2024-2026 .chance (dotchance)
# Push all NodalArc images to a remote K3s node.
# Usage: ./scripts/push-images-to-node.sh <node-ip>
# Requires: ssh access to node-ip, sudo on remote
set -e
NODE_IP=${1:?Usage: $0 <node-ip>}

IMAGES=(
  "docker.io/nodalarc/ome:latest"
  "docker.io/nodalarc/scheduler:latest"
  "docker.io/nodalarc/node-agent:latest"
  "docker.io/nodalarc/vs-api:latest"
  "docker.io/nodalarc/vf:latest"
  "docker.io/nodalarc/operator:latest"
  "docker.io/nodalarc/frr:10"
  "docker.io/nodalarc/probe:1"
)

for IMAGE in "${IMAGES[@]}"; do
  echo "Pushing $IMAGE to $NODE_IP..."
  sudo k3s ctr images export - "$IMAGE" \
    | ssh "$NODE_IP" "sudo k3s ctr images import -"
  echo "Done: $IMAGE"
done

echo "All images pushed to $NODE_IP"
