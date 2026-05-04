# Contributing to NodalArc

We welcome contributions. Whether it's a bug fix, new routing protocol support, visualization improvement, documentation, or a feature you want to see - we want your help.

## Getting Started

1. Read the [Developer Guide](docs/dev/) - especially [Architecture](docs/dev/architecture.md) and [Invariants](docs/dev/invariants.md)
2. Set up your development environment: [Development Setup](docs/dev/getting-started.md)
3. Run the tests: `make test`
4. Find something to work on (see Issues) or bring your own idea

## Development Workflow

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Run tests: `make test` (backend), `cd frontend && npm test` (frontend)
5. Deploy and verify: `sudo make deploy-<service>`
6. Open a pull request

## Pull Request Guidelines

- One logical change per PR. Don't mix bug fixes with refactors.
- Tests must pass. All 996+ unit tests for backend, 33+ for frontend.
- Follow existing code conventions (see [Conventions](docs/dev/conventions.md)):
  - Python 3.14+, Pydantic v2, pyroute2 for netlink
  - All NATS subjects in `lib/nodalarc/nats_channels.py`
  - No new dependencies without discussion
  - No abstraction layers
- Commit messages: describe what changed and why. No conventional commit prefixes.
- If your change touches architectural invariants, explain why in the PR description.

## What We're Looking For

### High Priority

- New routing protocol support (BGP policy, EIGRP, BIRD integration)
- Visualization improvements (new views, better interaction)
- Performance optimization (OME spatial indexing for large constellations)
- Test coverage expansion
- Documentation improvements

### Always Welcome

- Bug fixes with test cases
- Error message improvements
- Operational tooling
- CI/CD improvements

### Please Discuss First

- Architectural changes (new components, new messaging patterns)
- New dependencies
- Changes to the session pod security model
- Changes to the data flow or NATS stream structure

Open an issue or discussion before starting work on these.

## Code of Conduct

Be respectful. Be constructive. Focus on the work.

## License

By contributing, you agree that your contributions will be licensed under the NodalArc Source Available License 1.0.
