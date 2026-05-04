# NodalArc Developer Guide

This guide is for developers contributing to the NodalArc codebase. It covers the architecture, development workflow, testing, code conventions, and the invariants you must not violate.

Before making changes to any component, read the relevant sections here. NodalArc is a distributed system with strict architectural rules. Violating them causes subtle, hard-to-debug failures that may not surface until production.

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

NodalArc is built like aerospace software, not a startup MVP. The rules exist because violations were caught - often after hours of debugging subtle distributed system failures.

Key principles:

- **No bandaids.** If the root cause is architectural, fix the architecture. A point fix that defers the real fix costs more.
- **Fail loudly.** Silent failures are the most expensive bugs. Every error must be visible. No `except: pass`, no swallowed exceptions, no default-to-zero on failure.
- **Multi-node from day one.** Every decision must work with N Schedulers, M Node Agents, replicated services. If it only works on one node, redesign it.
- **Prove, don't guess.** A code change is not a fix until it's deployed, verified, and the problem is confirmed resolved with evidence. "I believe the issue is X, here's how to verify" - not "the fix is X."
- **Single source of truth.** Configuration, subjects, models - each has one authoritative location. If you need it in two places, import it from one.
