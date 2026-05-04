# NodalArc User Guide

This guide is for anyone using NodalArc through the web interface. You don't need to know how the backend works, what Kubernetes is, or how to write code. You need to know how to run orbital network experiments and interpret what you see.

## Contents

1. [Getting Started](getting-started.md) - What you see when you open NodalArc, and the first things to try
2. [Sessions](sessions.md) - Creating, switching, and configuring constellation sessions
3. [Globe View](globe-view.md) - The 3D visualization: what everything means, how to navigate
4. [Topology View](topology-view.md) - The network graph view
5. [Terminal Access](terminal.md) - Using the browser terminal to inspect routers
6. [Time Controls](time-controls.md) - Pause, resume, speed, seek
7. [Keyboard Shortcuts](keyboard-shortcuts.md) - Quick reference
8. [API for Power Users](api.md) - Scripting and automation via REST/WebSocket
9. [Troubleshooting](troubleshooting.md) - Common issues and how to fix them

## What is NodalArc?

NodalArc emulates satellite constellation networks. When you open the UI, you're looking at a real-time emulation of satellites orbiting Earth, forming mesh networks with inter-satellite laser links, connecting to ground stations, and running real routing protocols (IS-IS, OSPF, SR-MPLS) to forward traffic across the constellation.

Everything you see in the visualization corresponds to real network state. When a link appears between two satellites, those satellites have actually formed a routing adjacency. When you trace a path between two ground stations, that's the actual forwarding path packets would take. When you open a terminal to a satellite and run `show ip route`, you're looking at a real routing table computed by a real routing daemon.

You can:
- **Watch** how routing protocols behave on dynamic satellite topologies
- **Experiment** with different constellations, routing stacks, and ground station configurations
- **Measure** convergence time, path latency, and handoff disruption
- **Inspect** any router's state through an interactive terminal
- **Automate** experiments through the API
