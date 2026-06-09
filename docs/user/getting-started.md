# Getting Started

Open your browser to the NodalArc URL, typically:

```text
http://localhost:3000
```

You will see a 3D view of the running session. Most users start with an Earth
LEO session because it is small enough to understand while still exercising the
full stack: orbital motion, link changes, ground handoff, real routing, and
kernel actuation.

![NodalArc Initial View](../images/user-initial-view.png)

## What You're Looking At

When NodalArc starts, a session is already running. The default session is
`earth-leo-simple.yaml`: a 36-satellite Earth LEO starter with OSPF routing and
MBB-capable Earth ground nodes.

On the globe you will see:

- **Satellites** - moving nodes in orbital segments
- **ISL links** - inter-satellite links that are currently active
- **Ground sites and ground nodes** - fixed nodes on a body surface
- **Ground links** - active links from ground terminals to visible satellites
- **Segments** - the configured groups that organize the session, such as LEO,
  MEO, GEO, lunar relay, or ground access

Everything moves in simulation time. As satellites orbit, links appear when the
declared link rule and geometry allow them, and disappear when visibility or
policy no longer supports them. Ground nodes hand off between satellites
according to their own terminal capacity and handoff policy.

## First Things to Try

### 1. Watch a ground handoff

Select a ground node and watch which satellite it is connected to. If the ground
node has enough compatible terminals, it can use make-before-break behavior. If
it only has one compatible terminal, break-before-make is the honest behavior.

### 2. Click a satellite or ground node

Click any node to open the detail panel. The panel shows its runtime node ID,
position, active links, candidates, segment metadata, and explainability details
for why links are up, down, expected, or faulted.

### 3. Open a terminal

With a node selected, open the terminal panel. You land in a router CLI on that
node. Try:

- `show ip route` - see the routing table
- `show isis neighbor` or `show ip ospf neighbor` - see routing adjacencies
- `show interface brief` - see interface state

This is not a simulated CLI. It is the FRR process running inside that node's
container.

### 4. Trace a path

Select a source and destination, then use path trace to inspect the forwarding
path. The result reflects the routing and kernel state that exists at that
moment. If a route is missing, that is useful information; NodalArc should show
you the failure instead of hiding it.

### 5. Switch sessions

Use the session picker or command line to deploy a different curated session:

```bash
make session DEFAULT_SESSION=sessions/nodalarc/earth-leo-heo-geo-luna-reachability.yaml
```

Try `earth-leo-heo-geo-luna-reachability.yaml` to see multiple Earth orbital
regimes plus a lunar relay path — Earth, Luna, and a cislunar relay — in the same
visualization.

## Navigation

### 3D Controls

| Action | Control |
|--------|---------|
| Rotate around focus | Left-click + drag |
| Zoom toward focus | Scroll wheel |
| Pan | Right-click + drag |
| Select node | Click a satellite or ground node |
| Fly to selected node/body | Double-click, or use the segment drawer |
| Deselect | Escape |
| Follow selected node | F |

The camera has a focus point. When you select or fly to an object, zoom and
rotation use that object as the reference instead of always using Earth as the
center. That matters for GEO, cislunar, and lunar views where Earth may be far
off-screen.

### Keyboard

Press these keys when not typing in a terminal or input field:

| Key | Action |
|-----|--------|
| Space | Pause/resume simulation |
| Tab | Toggle globe/topology view |
| T | Toggle satellite trails |
| V | Top-down view |
| P | Toggle orbital paths |
| L | Toggle ISL links |
| G | Toggle ground links |
| F | Follow selected node |
| N | Toggle rendering mode |
| Q | Toggle filter drawer |
| 1 | Color by area |
| 2 | Color by plane |

See [Keyboard Shortcuts](keyboard-shortcuts.md) for the full reference.

## Understanding What You See

### Expected No-Link vs Faulted

The most important visual distinction is:

- **Expected no-link** - the model says no connection should exist right now
  because of geometry, terminal limits, capacity, policy, or handoff state.
- **Faulted** - the model expected a link or proof, but kernel state,
  actuation, or authority checks failed.

Those states should look different. A ground node that is below the elevation
mask should not look like a ground node with dirty kernel state.

### Segment Filters

Press **Q** to open the filter drawer. Segments and tags let you focus on one
part of the session, such as Earth LEO, Earth GEO, lunar relay, or ground access
nodes. Filtering changes what you inspect; it does not change the running
network.

### Orbital and Body Scale

LEO, MEO, GEO, and lunar distances differ by orders of magnitude. The
visualization preserves the relationships needed for navigation and
explainability, but rendering scale is still a view. The authoritative physics
lives in the OME and the session ephemeris.

## Next Steps

- [Sessions](sessions.md) - session grammar, curated demos, and building blocks
- [Globe View](globe-view.md) - detailed guide to the 3D visualization
- [Terminal Access](terminal.md) - using the browser terminal to inspect routers
- [Time Controls](time-controls.md) - pause and speed controls
