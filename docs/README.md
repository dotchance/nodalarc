# NodalArc Documentation

## [User Guide](user/) - Using NodalArc

For anyone interacting with NodalArc through the web interface. No backend knowledge required.

- [Getting Started](user/getting-started.md) - what you see when you open NodalArc, first things to try
- [Sessions](user/sessions.md) - creating, switching, and configuring constellation sessions
- [Globe View](user/globe-view.md) - the 3D visualization, what everything means, navigation
- [Topology View](user/topology-view.md) - the 2D network graph view
- [Terminal Access](user/terminal.md) - browser terminal for inspecting routers (vtysh)
- [Time Controls](user/time-controls.md) - pause, resume, speed, seek
- [Keyboard Shortcuts](user/keyboard-shortcuts.md) - quick reference
- [API for Power Users](user/api.md) - scripting and automation via REST/WebSocket
- [Troubleshooting](user/troubleshooting.md) - common issues and fixes

## [Operations Guide](ops/) - Deploying and Maintaining NodalArc

For infrastructure engineers deploying NodalArc on Kubernetes clusters.

- [Getting Started](ops/getting-started.md) - prerequisites, installation, first deployment
- [Configuration](ops/configuration.md) - session YAML, constellations, ground stations, satellite types
- [Multi-Node Deployment](ops/multi-node.md) - registry setup, pod placement, VXLAN tunnels
- [Scaling](ops/scaling.md) - resource requirements, capacity planning, performance
- [Operations](ops/operations.md) - teardown, session switching, upgrades, health monitoring
- [Security](ops/security.md) - pod hardening, SSH key lifecycle, network isolation
- [Troubleshooting](ops/troubleshooting.md) - diagnosing and fixing deployment issues

## [Developer Guide](dev/) - Contributing to NodalArc

For developers working on the NodalArc codebase.

- [Development Setup](dev/getting-started.md) - clone, build, run, test
- [Architecture](dev/architecture.md) - components, data flow, threading models
- [Invariants](dev/invariants.md) - architectural rules that cannot be violated
- [Development Workflow](dev/dev-workflow.md) - make targets, build loop, branch discipline
- [Testing](dev/testing.md) - unit tests, integration tests, verification standards
- [Conventions](dev/conventions.md) - code standards, patterns, what not to do

### Component Reference

- [OME](dev/components/ome.md) - Orbital Mechanics Engine
- [Scheduler](dev/components/scheduler.md) - Topology Dispatcher
- [Node Agent](dev/components/node-agent.md) - DaemonSet, kernel operations
- [VS-API](dev/components/vs-api.md) - Visualization State API
- [Operator](dev/components/operator.md) - Session Lifecycle Manager
- [VF](dev/components/vf.md) - Visualization Frontend

### Extending NodalArc

- [Adding Routing Stacks](dev/extending/routing-stacks.md) - integrating new routing daemons
- [Extending Propagators](dev/extending/propagators.md) - replacing or extending orbital mechanics
- [Building Visualization Clients](dev/extending/visualization-clients.md) - custom frontends and dashboards
