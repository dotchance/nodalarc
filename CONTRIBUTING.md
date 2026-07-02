# Contributing to NodalArc

We welcome contributions. Whether it's a bug fix, catalog improvement, routing-stack integration, visualization improvement, documentation update, or feature you want to see - we want your help.

NodalArc is an orbital network emulator. Its core job is to build a truthful emulated world: bodies, orbits, nodes, terminals, links, latency, loss, bandwidth, kernel state, and actuation failures. Routing stacks and router configuration consume that truth; they do not define it.

## Getting Started

1. Read the [Developer Guide](docs/dev/) - especially [Architecture](docs/dev/architecture.md), [Invariants](docs/dev/invariants.md), and [Conventions](docs/dev/conventions.md).
2. Set up your development environment: [Development Setup](docs/dev/getting-started.md).
3. Run the checks: `make lint` and `make test`.
4. If your change affects the running platform, deploy and verify it with the relevant `make` target.
5. Find something to work on in Issues, improve the catalog, or bring your own idea.

## Development Workflow

1. Fork the repository.
2. Create a feature branch from `main`.
3. Make your changes.
4. Run focused tests while you work.
5. Run `make lint` and `make test` before opening a PR.
6. Deploy and verify when behavior changes affect services, sessions, kernel wiring, routing, or the UI.
7. Open a pull request.

## Good First Contributions

Catalog work is the safest way to contribute useful domain value without understanding the whole runtime on day one. Good catalog contributions include:

- adding or correcting a site with sourced coordinates and facility details
- adding a terminal primitive with realistic physical limits and references
- adding or improving an orbit primitive
- adding a node or constellation primitive
- adding a session recipe that composes existing primitives
- improving references, notes, or factual accuracy for existing primitives

Catalog entries must follow the configuration grammar in [Configuration Grammar](docs/ops/configuration-grammar.md). A primitive has one schema whether it is loaded from a file or written inline in a session.

## Architecture Rules

These rules matter more than style preferences:

- NodalArc base emulation is vendor-neutral and routing-stack-neutral.
- Bodies, orbits, terminals, nodes, sites, links, and physical constraints belong in the emulated-world model.
- Router configuration is derived from resolved NodalArc facts or supplied through a higher configuration layer. It is not base session truth.
- Unsupported behavior must fail loudly with a clear error. Do not add silent fallbacks.
- Do not duplicate authority. If a value belongs in a catalog primitive or resolved session model, read it from there.
- Use the shared resolver and model boundaries. Production code must not construct divergent session views.
- NATS subjects belong in `lib/nodalarc/nats_channels.py`.
- Kernel network state is owned by Node Agent and must be proven after actuation.
- OME owns orbital/body-frame truth. Scheduler owns desired connectivity and dispatch ordering.

When in doubt, read [Invariants](docs/dev/invariants.md) before coding.

## Pull Request Guidelines

- You must accept the [NodalArc Contributor License Agreement](CLA.md) before a PR can merge. The GitHub CLA check will comment with the exact acceptance text when needed.
- One logical change per PR. Don't mix bug fixes with refactors.
- Tests must pass. Run the smallest useful focused tests while developing, then run `make lint` and `make test` before requesting review.
- Follow existing code conventions (see [Conventions](docs/dev/conventions.md)):
  - Python 3.14+, Pydantic v2, pyroute2 for netlink
  - All NATS subjects in `lib/nodalarc/nats_channels.py`
  - No new dependencies without discussion
  - Prefer existing local patterns over new abstractions
- Commit messages: describe what changed and why. No conventional commit prefixes.
- If your change touches architectural invariants, explain why in the PR description.
- If your change alters runtime behavior, include a test or explain the verification performed.
- If your change alters catalog/configuration semantics, update the relevant docs and grammar tests.

## What We're Looking For

### High Priority

- Catalog accuracy improvements for bodies, orbits, terminals, nodes, sites, constellations, and sessions
- Configuration grammar and resolver hardening
- Routing-stack support that consumes resolved NodalArc facts cleanly
- Visualization improvements and interaction polish
- Performance optimization for large constellations and multi-body sessions
- Test coverage expansion
- Documentation improvements

### Always Welcome

- Bug fixes with test cases
- Error message improvements
- Operational tooling
- CI/CD and local developer workflow improvements
- Small UI/UX improvements that preserve the operator-console design direction

### Please Discuss First

- Architectural changes, including new components or new messaging patterns
- New dependencies
- Changes to the session pod security model
- Changes to the data flow or NATS stream structure
- New configuration grammar primitives or closed-vocabulary values
- New routing-stack families or vendor-specific integration paths

Open an issue or discussion before starting work on these.

## Code of Conduct

Be respectful. Be constructive. Focus on the work.

## License

By contributing, you agree that your contributions are governed by the [NodalArc Contributor License Agreement](CLA.md). Your contributions may be distributed as part of NodalArc under the license or licenses chosen by .chance (dotchance) for the project.
