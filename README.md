# NodalArc

Networks are leaving the ground.

That sounds like a slogan until you have to route through it. A terrestrial
network lets you pretend the topology is mostly fixed. Links fail, routers
reboot, fiber gets cut, but the bones of the thing stay where you put them.

Orbit does not work that way.

A satellite in low Earth orbit is moving about seven and a half kilometers a
second. A cross-plane link can exist now and be gone a few minutes later. A
ground station can be the best exit from the network this pass, and useless on
the next. On the ground, topology change is an event. In orbit, topology change
is the medium you are swimming in.

That is the gap NodalArc is meant to fill.

NodalArc lets you run real routing stacks against orbital geometry. Not a
drawing of a network. Not a simulator guessing what IS-IS or OSPF might do.
Real Linux namespaces. Real FRR. Real kernel interfaces. Real link state changes
driven by satellites moving through space.

![NodalArc Globe View](docs/images/hero-globe.png)

## Run It

Clone the repo, bootstrap the host, and bring the system up.

```bash
git clone https://github.com/dotchance/nodalarc.git
cd nodalarc
sudo scripts/bootstrap-host.sh   # installs K3s, Docker, Helm, Node.js
make all                         # deps, build, load, install, session, status
```

Open `http://localhost:3000`.

You are looking at 36 satellites moving around Earth with OSPF routing, seven
ground stations, and live ISL links. Every satellite and ground station is a
real router process in a real network namespace. You can exec into a node and
ask FRR what it sees. You can trace a path. You can pause time, move it
forward, and watch the network rearrange itself.

For a square-one proof on a machine that already has K3s installed, run:

```bash
make nuke && make all
```

For an existing platform, use:

```bash
make build && make load && make upgrade
```

`make install` refuses existing platform state on purpose. Use `make upgrade`
when you want to update the running platform, `make reinstall` when you want a
destructive platform refresh, and `make nuke && make all` when you want to prove
the whole bring-up path from scratch.

## What You Are Running

NodalArc is an emulation platform for orbital networks.

Each satellite and ground station becomes a Linux network namespace running a
real routing stack. IS-IS hellos, OSPF LSAs, BGP updates, and MPLS label
operations happen in the kernel, not in a model. When a link drops because two
satellites moved out of range, FRR sees carrier loss on an interface and
reconverges the way it would on hardware.

The links are not decorative lines. The Node Agent builds veth pairs and VXLAN
tunnels, then shapes them with `tc netem` and `tc tbf`. Latency comes from the
range between the endpoints. Bandwidth comes from the terminal model. If a
packet moves from a ground station in Hawthorne to one in Frankfurt, it crosses
real kernel interfaces with the propagation delay the geometry gives it at that
moment.

The architecture is built from primitives:

- satellite types describe hardware: terminals, ranges, bandwidth, tracking
  limits
- constellation geometry describes where the satellites move
- ground stations describe where the network touches Earth and what prefixes
  enter there
- routing stacks describe what runs inside each node

Change one primitive and leave the others alone. Run a Walker Delta geometry
with one routing stack, then swap in Iridium geometry. Hold the sky fixed and
change IS-IS to OSPF. Move a ground station. Add segment routing. Watch what
changes.

That is the point. Measure what actually happens.

## What You Can Try

Once the system is running, you can:

- watch IS-IS or OSPF reconverge as orbital links appear and disappear
- measure ground station handoff impact instead of arguing about timers
- run the same constellation under IS-IS, OSPF, SR-MPLS, or NodalPath
- change altitude, inclination, plane count, and phase offset
- move ground stations and see what reachability you bought or lost
- run `ping`, `traceroute`, and `iperf` through the emulated constellation
- open a browser terminal to any satellite or ground station and use `vtysh`
- script experiments through the REST and WebSocket APIs

Start small. Demo-36 is enough to see the machinery. Starlink-176 and Iridium-66
start to show why the geometry matters. A Walker Delta gives you a steady
backbone and access handoffs. A Walker Star gives you global reach and a polar
seam that tears through the backbone on schedule.

That is where the interesting questions start.

## What The System Gives You

### A live globe

The browser view shows satellites, ground stations, orbital paths, ISL links,
and ground links in motion. The frontend renders positions locally from
ephemeris data, so it can run at 60fps without the backend publishing position
updates sixty times a second.

### Real router access

Open a terminal to any satellite or ground station from the browser. You land in
the same FRR CLI you would use on a physical router. Run `show ip route`,
`show isis neighbor`, `show ospf neighbor`, or whatever the running stack
supports.

### Session switching

The session wizard lets you choose a constellation, satellite type, ground
station set, and routing protocol. Deploy a new session without rebuilding the
platform. The Operator tears down the old session, creates the new pods, renders
the router configs, and hands the topology to the Node Agents.

### Time control

Pause, resume, change speed, and seek. If a failure happens at a certain point
in the orbit, move time there and inspect the state while the system is still.

### Multi-node scale

A single machine can run hundreds of nodes. A Kubernetes cluster can spread the
emulation across machines. Local links use host-mediated veths. Cross-node links
use VXLAN. Substrate latency compensation keeps the emulated delay tied to the
orbital path, not to the physical lab network.

### Multiple routing models

FRR gives you IS-IS, OSPF, BGP, SR-MPLS, LDP, and traffic engineering. NodalPath
provides centralized path computation for experiments where distributed routing
is not the model you want to test.

## Documentation

The docs are split by the work you are trying to do.

### [User Guide](docs/user/)

Use the visualization, launch sessions, inspect nodes, trace paths, and run
experiments from the browser.

### [Operations Guide](docs/ops/)

Install NodalArc, run it on Kubernetes, configure multi-node clusters, scale it,
tear it down, and keep it healthy.

### [Developer Guide](docs/dev/)

Work on the codebase. Read the architecture, then the invariants. The system is
modular because it has to be. If you bypass those boundaries, it will look fine
right up to the moment it does something expensive and confusing.

## Project Structure

```text
services/       Backend services: OME, Scheduler, Node Agent, VS-API, Operator
frontend/       Visualization frontend: React + Three.js
nodalpath/      NodalPath path computation engine
lib/            Shared Python library
images/         Container images: FRR, probe, forwarding sidecar
deploy/         Helm chart and deployment tooling
configs/        Constellations, ground stations, satellite types, sessions
tests/          Unit and integration tests
docs/           User, operations, and developer documentation
tools/          Lifecycle and operational tooling
scripts/        Host bootstrap and infrastructure scripts
```

## Community

NodalArc is source-available and welcomes useful contributions. Bugs, routing
stacks, constellation models, visualization improvements, documentation fixes -
all of it helps, as long as it respects the architecture.

- **Issues** - bug reports, feature requests, and questions
- **Pull Requests** - read the [Developer Guide](docs/dev/) before opening one
- **Discussions** - architecture proposals, use cases, and experiments

## License

NodalArc Source Available License 1.0. You can use, modify, and distribute it
subject to the license terms. You cannot offer it as a hosted or managed
service that provides access to a substantial set of NodalArc features. See
[LICENSE](LICENSE) for full terms and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
for bundled third-party notices.

Copyright 2024-2026 .chance (dotchance)
