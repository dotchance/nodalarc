# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in NodalArc, **do not open a public issue.** Instead, report it privately via [GitHub Security Advisories](https://github.com/dotchance/nodalarc/security/advisories/new).

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected components (Node Agent, Operator, VS-API, etc.)
- Impact assessment if known

You should receive an acknowledgment within 72 hours. We will work with you to understand the issue, determine a fix timeline, and coordinate disclosure.

## Scope

NodalArc is a network emulation platform that runs real routing stacks in containers with kernel-level network manipulation. The following areas are particularly security-relevant:

- **Node Agent** — runs with `hostPID` and performs `nsenter`/`setns` operations across pod namespaces
- **Session pod hardening** — iptables rules, read-only root filesystem, service account restrictions
- **VS-API** — HTTP API with optional API key authentication, WebSocket terminal proxy
- **Operator** — generates Kubernetes resources (pods, secrets, ConfigMaps) from user-submitted session definitions
- **SSH terminal access** — per-session key generation, proxy through VS-API

## Security Model

NodalArc is designed for lab and research environments. It is **not designed to be exposed to untrusted networks or users.** The security model assumes:

- Cluster operators are trusted
- Session definitions come from trusted sources
- The Kubernetes API server is not publicly accessible
- Container images are pulled from a trusted registry

Session pods run with `SYS_ADMIN` capability (required by FRR) and `hostPID` access is granted to the Node Agent DaemonSet. These are necessary for the emulation to function but represent a larger attack surface than typical Kubernetes workloads.

## Supported Versions

Security fixes are applied to the latest release only. There are no long-term support branches.
