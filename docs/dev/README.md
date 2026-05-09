# NodalArc Developer Guide

This guide is for people who are going to touch the machine.

NodalArc works because its pieces do different jobs and do not pretend
otherwise. The OME moves the sky. The Scheduler decides which links should
exist. The Node Agent touches the kernel. FRR routes. VS-API gathers state. VF
draws the result.

Miss that shape and the code starts to rot. You get a shortcut here, a helper
there, a second dispatch path because it was convenient at the time, and then
the next person spends a night chasing a bug that should never have been
possible.

Read the architecture before changing behavior. Read the invariants before
changing boundaries. They are not ceremony. They are the load-bearing parts of
the system.

## Contents

### Getting Started
- [Development Setup](getting-started.md) - clone, build, run, test, deploy for development

### Architecture
- [System Architecture](architecture.md) - components, data flow, threading models
- [Architectural Invariants](invariants.md) - rules that cannot be violated

### Components
- [OME](components/ome.md) - Orbital Mechanics Engine
- [Scheduler](components/scheduler.md) - Topology Dispatcher
- [Node Agent](components/node-agent.md) - DaemonSet, kernel operations
- [VS-API](components/vs-api.md) - Visualization State API
- [Operator](components/operator.md) - Session Lifecycle Manager
- [VF](components/vf.md) - Visualization Frontend

### Workflow
- [Development Workflow](dev-workflow.md) - build loop, make targets, branch discipline
- [Testing](testing.md) - unit tests, integration tests, verification standards
- [Conventions](conventions.md) - code standards, patterns, what not to do

### Extending
- [Adding Routing Stacks](extending/routing-stacks.md) - integrating new routing daemons
- [Extending Propagators](extending/propagators.md) - replacing or extending orbital mechanics
- [Building Visualization Clients](extending/visualization-clients.md) - custom frontends and dashboards

## Philosophy

NodalArc is built like a system that has to keep its stories straight.

A lot of the code is ordinary. Kubernetes starts pods. NATS moves messages. FRR
routes packets. Linux moves interfaces. None of that is magic. The value is in
how those pieces fit together, and the fit only holds while the boundaries stay
clean.

The rules exist because breaking them already hurt.

Key principles:

- **No bandaids.** If the root cause is architectural, fix the architecture. A
  point fix that defers the real fix costs more.
- **Fail loudly.** Silent failures are the most expensive bugs. Every error must
  be visible. No `except: pass`, no swallowed exceptions, no default-to-zero on
  failure.
- **Multi-node from day one.** Every decision must work with N Schedulers, M
  Node Agents, and replicated services. If it only works on one node, redesign
  it.
- **Prove, don't guess.** A code change is not a fix until it is deployed,
  verified, and the problem is confirmed resolved with evidence.
- **Single source of truth.** Configuration, subjects, models, and ownership
  boundaries each have one authoritative home. If you need the same fact in two
  places, import it from one.
