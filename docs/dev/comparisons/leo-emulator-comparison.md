# NodalArc Compared With Contemporary LEO Network Emulators

Status: public comparison snapshot

Last source inspection: 2026-05-18

This document compares NodalArc with the LEO satellite network emulators called
out in the referenced ScienceDirect article: Celestial, StarryNet, OpenSN,
xeoverse, and LeoEM. It also includes bLEO because the article and follow-up
source review made bLEO directly relevant to the comparison.

The goal is not to declare a universal winner. The goal is to state, accurately,
what each system actually does, where its results are meaningful, and where its
implementation choices limit the claims that can be made from it.

## Audit Basis

Claims in this document are based on source inspection of the listed local
snapshots and on the referenced papers or project pages where source was not
available. Commit hashes identify the code snapshots inspected during this
review; they are not maintenance-status claims.

| System | Source basis | Inspected revision | Source status for this review |
| --- | --- | --- | --- |
| NodalArc | Local repository | `2015543` | Code-verified |
| bLEO | `github.com/leonetlab-upct/bleo` | `08644b1` | Code-verified |
| Celestial | `github.com/OpenFogStack/celestial` | `13e7911` | Code-verified |
| StarryNet | `github.com/SpaceNetLab/StarryNet` | `91d87bd` | Code-verified |
| OpenSN | `github.com/OpenSN-Library/OpenSN-Library` | `aaf9cba` | Code-verified |
| LeoEM | `github.com/XuyangCaoUCSD/LeoEM` | `4488b00` | Code-verified |
| xeoverse | Paper and project page only | Not applicable | Paper-derived only |

Important interpretation rules:

- "Code-verified" means the claim was checked against the source snapshot
  listed above. It does not mean the system was executed in a full lab run.
- "Paper-derived" means the claim comes from the paper or project page, not
  from source code inspection.
- NodalArc is licensed under the Apache License 2.0. License statements in
  this document describe the inspected snapshot and should be rechecked if
  upstream projects change their terms.
- This is a dated snapshot, not a live maintenance tracker.

## External References

- ScienceDirect article:
  <https://www.sciencedirect.com/science/article/pii/S1389128626003956>
- bLEO repository: <https://github.com/leonetlab-upct/bleo>
- bLEO paper page: <https://www.sciencedirect.com/science/article/pii/S1570870526000272>
- Celestial repository: <https://github.com/OpenFogStack/celestial>
- Celestial documentation: <https://openfogstack.github.io/celestial/>
- StarryNet repository: <https://github.com/SpaceNetLab/StarryNet>
- OpenSN repository: <https://github.com/OpenSN-Library/OpenSN-Library>
- OpenSN project page: <https://opensn-library.github.io/>
- OpenSN APNet paper:
  <https://conferences.sigcomm.org/events/apnet2024/papers/OpenSNAnOpenSourceLibraryforEmulatingLEOSatelliteNetworks.pdf>
- xeoverse paper: <https://arxiv.org/abs/2406.11366>
- xeoverse project page: <https://netsys.surrey.ac.uk/softwares/xeoverse/>
- LeoEM repository: <https://github.com/XuyangCaoUCSD/LeoEM>
- SaTCP / LeoEM paper:
  <https://xyzhang.ucsd.edu/papers/Xuyang.Cao_INFOCOM23_SaTCP.pdf>

## Executive Summary

NodalArc is materially larger than the other inspected codebases, but the other
systems do not provide the same surface area with less code. Most peer systems
pick a narrower point in the design space: single-host Mininet, offline route
replay, script-driven Docker, or workload-focused microVM orchestration.

NodalArc's differentiators are:

- Kubernetes-native deployment with pod-per-node network namespaces.
- Real routing implementations for current IGP modes: OSPF and IS-IS through
  FRR. BGP is not implemented today; the frontend marks it as "Coming Soon" and
  `stack_resolver.py` resolves only `ospf` and `isis`.
- OME, Scheduler, and Node Agent separation. Orbital mechanics, topology
  reconciliation, and kernel mutation are separate services.
- Proof-oriented dispatch. The Scheduler advances active state only after exact
  verified Node Agent replies; dirty-kernel and stale-generation states fail
  closed.
- Multi-node data-plane support through VXLAN and `tc`, with measured substrate
  latency subtracted from emulated orbital delay.
- Operator-facing UX: session wizard, 3D globe, topology view, logs, panels,
  time controls, and node/terminal inspection.

NodalArc's current limitations are also real:

- Apache 2.0 is permissive. It allows proprietary forks and hosted services
  subject to the license's notice, license, patent, and trademark terms.
- The Scheduler and other control-plane services are deployed as single
  replicas today. The code explicitly notes that scheduler horizontal scaling
  needs a NATS queue group or leader election.
- BGP is not supported today.
- It is not yet provider-neutral for Juniper cRPD, Arista cEOS, Cisco router
  containers, or mixed NOS experiments. FRR-specific assumptions still exist in
  config generation, templates, measurement adapters, terminal UX, and routing
  stack resolution.
- Link delay updates use Linux `tc`/netem. bLEO and Celestial demonstrate an
  eBPF-based delay path that can update faster than qdisc churn.

The honest position is: NodalArc is stronger as a full-system, proof-oriented,
operator-facing LEO routing emulator. bLEO is stronger at fast single-host
delay/drop mutation. Celestial is stronger at microVM workload isolation.
OpenSN has the closest peer UX and multi-host container story. StarryNet and
LeoEM are smaller and useful, but narrower. xeoverse cannot be evaluated at
code level from public source.

## Architecture Summaries

### NodalArc

NodalArc runs a Kubernetes-native emulator with one session pod per satellite or
ground node. The OME computes orbital visibility and publishes facts over NATS.
The Scheduler consumes visibility and desired topology snapshots, reconciles
desired state against active state, and dispatches fenced operations to Node
Agents. Node Agents run as privileged DaemonSets and perform host kernel work:
veths, VXLAN, carrier state, `tc netem`, `tc tbf`, namespace entry, ground
bridge attachment, and proof checks.

Routing is real for the supported distributed modes. In OSPF or IS-IS mode,
FRR inside the pod reacts to real Linux interface state.

Current routing protocol support is OSPF and IS-IS. BGP is visible in the UI as
disabled future work, but is not accepted by the current resolver.

### bLEO

bLEO is a single-host Docker emulator focused on MPTCP/ECMP experiments and
fast LEO link updates. It creates containers, veth pairs, a ground bridge, and
optional FRR OSPF. Its key implementation feature is a TC egress eBPF program
that reads a pinned BPF hash map keyed by interface index. A missing map entry
passes traffic, a zero value drops traffic, and a positive value sets
`skb->tstamp` so Linux fair queueing defers transmission by the requested delay.

This is an efficient design for high-frequency single-host delay/drop updates.
It is not a proof-bearing distributed emulator. The generated shell scripts use
`set -e`, but there is no Node Agent style reconciliation, per-entry proof ACK,
dirty-kernel reporting, or multi-host substrate model. OSPF is optional and,
when enabled, bLEO exempts OSPF protocol traffic from delay. That helps routing
converge quickly, but it also means OSPF control traffic is not experiencing the
same LEO delay model as data traffic.

### Celestial

Celestial runs Firecracker microVMs and is strongest when the research question
needs guest-kernel or workload isolation. A Python orchestrator drives Go host
daemons. Celestial supports `tc` and eBPF network emulation backends and uses
WireGuard for cross-host connectivity. It measures peer latency and accounts for
physical host latency in its distributed setup.

Celestial does not orchestrate FRR, BIRD, BGP, OSPF, or IS-IS as emulator-owned
routing protocols. Users can put arbitrary software in a guest root filesystem,
including a routing daemon, but Celestial's emulator logic precomputes topology
and path information rather than managing a distributed routing control plane.
That makes Celestial useful for edge workload experiments over LEO-like
connectivity, but not a first-class routing-protocol emulator.

### StarryNet

StarryNet is a Python/Docker emulator with a CLI and generated BIRD OSPF
configuration. It precomputes satellite positions and delay data, creates Docker
networks and container interfaces, starts BIRD, and applies `tc` changes through
shell commands.

StarryNet gives real OSPF behavior through BIRD, which is a meaningful strength.
Its implementation is script-heavy: many operations use `os.system` or
`os.popen`, topology changes are sequential, and cleanup/error handling is not
proof-oriented. The repository has remote-machine configuration and SSH helpers,
but the inspected source did not show a robust cross-host link emulation layer
equivalent to NodalArc VXLAN, Celestial WireGuard, or OpenSN VXLAN.

### OpenSN

OpenSN is a Docker/containerd, Go-daemon, etcd-coordinated emulator with a React
UI. It is not Kubernetes-native. Host daemons use Docker and containerd sockets
directly, coordinate through etcd, and create VXLAN devices for cross-machine
links. The UI includes a 3D earth view, topology configurator, instance detail
views, WebShell support, and link packet-capture workflows.

OpenSN runs FRR in its router container images and uses `vtysh` batch
configuration. That makes it a real-routing peer for OSPF-style experiments. Its
topology configurator uses wall-clock polling at a five-second cadence in the
inspected standard configurator. Multi-host VXLAN exists, but the inspected code
did not show NodalArc/Celestial-style measured substrate latency subtraction.

### LeoEM

LeoEM is a Mininet-based emulator built around the SaTCP research question. Its
pipeline computes constellation data, precomputes routes with shortest-path
logic, and then replays route/link changes into Mininet with `TCLink`
properties. It is useful for transport-layer experiments around handover and
SaTCP behavior.

LeoEM is not a distributed routing-daemon emulator. It does not run FRR or BIRD
as the route authority. Routing paths are precomputed and replayed through the
Mininet experiment.

### xeoverse

xeoverse is treated as paper-derived only in this document because no public
source snapshot was available for code inspection. The paper describes a
Mininet-based emulator with a back-stage route/topology computation component
and a main-stage emulator that applies delta updates. Those claims may be valid,
but they cannot be checked here at implementation level.

## Capability Matrix

| Capability | NodalArc | bLEO | Celestial | StarryNet | OpenSN | LeoEM | xeoverse |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Source status in this review | Code-verified | Code-verified | Code-verified | Code-verified | Code-verified | Code-verified | Paper-only |
| Primary runtime | K3s/Kubernetes pods | Docker containers | Firecracker microVMs | Docker containers | Docker/containerd | Mininet | Mininet, paper-derived |
| Single-host or single-node mode | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Multi-host data plane | Yes | No | Yes | Not verified as robust | Yes | No | Not claimed from source |
| Control-plane horizontal scaling | Not today | Not applicable | Limited by orchestrator/client model | No | Host daemons coordinate through etcd | No | Paper-only |
| Real Linux network stack | Yes | Yes | Yes, guest kernels | Yes | Yes | Yes, Mininet namespaces | Paper-only |
| Emulator-owned routing daemon | FRR OSPF/IS-IS | Optional FRR OSPF | No | BIRD OSPF | FRR OSPF | No | Paper describes route replay |
| BGP support | No today | No verified BGP path | User-provided only, not emulator-managed | No verified BGP path | No verified BGP path | No | Paper-only |
| Offline route replay | No for IGP modes | No for OSPF mode | Yes, emulator path model | Partly precomputed topology | Topology polling and reconfiguration | Yes | Yes, paper-derived |
| Dynamic ISL/GSL changes | Yes | Yes | Yes | Yes | Yes | Yes | Paper-derived |
| Make-before-break handoff model | Yes | No verified MBB proof | No first-class MBB | No verified MBB | No verified MBB | Handover modeled for SaTCP | Paper-only |
| Delay injection backend | `tc netem`/`tbf` | TC eBPF timestamping + `fq` | `tc` or eBPF backend | `tc netem` | `tc`/netlink paths | Mininet `TCLink` | Paper-only |
| Substrate latency compensation | Yes | Not needed single-host | Yes | No | Not verified | No | Paper-only |
| Proof-bearing kernel ACKs | Yes | No | No equivalent | No | No equivalent observed | No | Paper-only |
| Dirty-kernel/fail-closed contract | Yes | No | No equivalent | No | No equivalent observed | No | Paper-only |
| Integrated operator GUI | Yes | No | Animation/HTTP data, not live operator GUI | CLI/API | Yes | No | Not verified |
| Routing provider abstraction | Partial foundation, not provider-neutral | No | Guest image freedom only | No | Container/image customization, not abstraction | No | Paper-only |

## Result Truthfulness

"Truth" here means whether an experiment result represents the phenomenon it
claims to measure, and whether the emulator can tell the operator when the host
state did not actually match the requested topology.

NodalArc is strongest where the question involves real IGP behavior under a
dynamic LEO topology. FRR sees real carrier transitions and real interface
latency. The Scheduler does not mark links active unless Node Agents return
verified results. Stale generation, malformed command, unverified success, and
dirty-kernel paths are treated as failures instead of being hidden as active
topology. Substrate latency is measured and subtracted for cross-node links.

bLEO is truthful for a different scope: single-host data-plane delay/drop
experiments, optional FRR OSPF over veth links, MPTCP/ECMP behavior, and fast
delay mutation. Its eBPF design is technically strong. Its limits are that it
does not prove per-link kernel state back to a scheduler, does not distribute
across hosts, and intentionally exempts OSPF control traffic from emulated
delay.

Celestial is truthful for workload isolation and LEO-like network conditions
inside microVMs. It is not truthful as evidence of emulator-managed routing
protocol convergence unless the researcher supplies, instruments, and validates
that routing stack inside the guest separately.

StarryNet is truthful for small to moderate BIRD OSPF experiments where a
script-driven Docker environment is acceptable. It is weaker for failure
honesty: shell calls, sequential mutation, and limited postcondition proof mean
the operator must do more independent validation.

OpenSN is a meaningful peer for container-based FRR experiments and has the
closest external UX surface to NodalArc. Its truth gaps are mostly around
substrate latency accounting, polling cadence, and lack of a proof-bearing
dispatch contract comparable to Node Agent ACKs.

LeoEM is truthful for the SaTCP/transport scenario it was built for. It should
not be cited as evidence of distributed routing protocol behavior.

xeoverse cannot be code-audited here. Its paper claims should remain labeled as
paper-derived unless source becomes available for inspection.

## UX And Operator Surface

NodalArc and OpenSN are the only inspected systems with full integrated operator
web interfaces.

NodalArc's UX is broader for live operations: session catalog/wizard,
protocol/timer choices, 3D globe, topology graph, live panels, logs, time
controls, terminal/command panels, and integrated state visualization. It is
designed as an operator tool, not only an experiment script.

OpenSN also has a substantial GUI: 3D earth view, topology configurator, node
detail pages, WebShell support, link detail views, and packet capture workflow.
It is the strongest external UX peer.

Celestial provides visualization/animation and HTTP-accessible data, but not a
live emulator operator GUI comparable to NodalArc or OpenSN.

StarryNet exposes CLI/API workflows. bLEO is script/config driven. LeoEM is
script/Mininet driven. xeoverse UX cannot be assessed from source in this
review.

## Routing Provider And NOS Flexibility

None of the inspected systems currently provide a complete provider-neutral NOS
abstraction for swapping or intermixing FRR, Juniper cRPD, Arista cEOS, Cisco
IOS-XR/IOS-XE containers, or similar router images.

NodalArc has the best foundation for this, but it is not done. The current stack
resolver, generated templates, pod model, Scheduler/Node Agent separation,
terminal UX, and measurement adapters create a place to add provider contracts.
They do not yet make providers interchangeable. FRR-specific assumptions remain
in the routing stack resolver, templates, generated config, management/terminal
behavior, and measurement adapters.

To make NodalArc provider-neutral, the project would need an explicit
`RoutingProvider` contract covering at least:

- Image, command, environment, mounts, capabilities, and license/secret needs.
- Interface naming and namespace expectations.
- Config rendering and validation.
- Readiness and convergence probes.
- Route, adjacency, label, and telemetry extraction.
- CLI/terminal adapter behavior.
- Per-provider failure semantics and proof expectations.
- Mixed-provider session schema and tests.

OpenSN can change container images and environment configuration, and its UI has
container/environment drawers, but the inspected source still centers on FRR
router images and `vtysh` operations rather than a general provider contract.

bLEO and StarryNet are more tightly script-bound to FRR/OSPF and BIRD/OSPF,
respectively. Celestial is flexible at the guest-image level, but the emulator
does not manage, inspect, or validate the routing daemon inside the guest.
LeoEM and xeoverse are not routing-provider systems.

## Scalability And Source Footprint

Raw source size is not a fair capability measure across these systems.
NodalArc has the largest inspected source surface because it includes a
Kubernetes operator path, multiple services, NATS contracts, a privileged Node
Agent, proof/failure semantics, a React/Three.js frontend, measurement adapters,
tests, deployment manifests, and documentation.

The smaller systems are smaller mostly because they choose narrower designs:

- bLEO keeps the core tight by using single-host Docker, generated shell
  scripts, and eBPF maps for delay/drop updates.
- LeoEM keeps the emulator small because routes are precomputed and replayed in
  Mininet.
- StarryNet keeps the system script-sized by using Docker/BIRD and shell-driven
  mutation.
- Celestial moves complexity into Firecracker, guest images, precomputed
  topology, and host daemons rather than a routing control-plane service.
- OpenSN is the closest broad peer, with Go daemons, Docker/containerd, etcd,
  React UI, WebShell, and packet capture.

The correct conclusion is not "the others provide the same capability with less
code." They mostly provide fewer operational guarantees, less proof, fewer UX
surfaces, fewer provider-extension points, or a narrower research target.

## Where NodalArc Is Stronger

NodalArc is strongest when the experiment needs:

- Real OSPF or IS-IS behavior from a production routing stack.
- Dynamic ISL/GSL events that are reconciled rather than blindly replayed.
- Proof that requested kernel state exists before active state advances.
- Explicit dirty-kernel and stale-generation failure semantics.
- Multi-node data-plane emulation with substrate latency compensation.
- A live operator GUI for inspecting topology, logs, time, and node state.
- A plausible path to provider abstraction, even though the current
  implementation is still FRR-centered.

## Where NodalArc Is Weaker

NodalArc is weaker when the experiment needs:

- Minimal setup and fastest time to first packet on one machine.
- Very fast delay/drop updates through eBPF maps.
- MicroVM guest-kernel isolation.
- A small scriptable research artifact rather than a full operator system.
- Horizontally scaled control-plane services today.
- BGP or commercial NOS containers today.

## Refactor Implications

The comparison points to several concrete NodalArc refactor priorities:

1. Add an explicit routing-provider boundary before adding more protocols.
   BGP and commercial NOS support should not be bolted onto FRR-specific
   assumptions.

2. Separate "routing protocol" from "routing implementation." OSPF over FRR,
   OSPF over BIRD, IS-IS over FRR, and BGP over a commercial NOS are different
   provider bindings, not just enum values.

3. Preserve the proof contract. NodalArc's main correctness advantage is that
   the Scheduler does not manufacture active topology from unverified host
   operations.

4. Consider an eBPF delay backend for high-churn single-host or same-node
   experiments. bLEO and Celestial both show that eBPF can avoid expensive qdisc
   churn, but adopting it should not weaken Node Agent proof semantics.

5. Make control-plane scaling explicit. The Scheduler is single-replica today
   by design. Horizontal scaling requires queue ownership or leader election,
   and the public docs should continue to say that clearly until it changes.

6. Keep UX as a real differentiator. OpenSN is the only close external GUI peer;
   most other systems do not have an operator surface comparable to NodalArc.

## Bottom Line

NodalArc should be positioned as an Apache-2.0-licensed, Kubernetes-native,
proof-oriented LEO routing emulator with a full operator interface. It is not
the smallest emulator, not the fastest single-host delay engine, and not yet a
provider-neutral NOS lab. Its strongest public claim is narrower and more
defensible: for OSPF and IS-IS experiments that need real routing behavior,
honest host-state verification, dynamic LEO topology, and operator visibility,
NodalArc provides a broader and more rigorous system than the inspected peers.
