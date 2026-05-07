# Getting Started

Open your browser to the NodalArc URL (typically http://localhost:3000). You'll see a 3D globe with satellites orbiting Earth.

![NodalArc Initial View](../images/user-initial-view.png)

## What You're Looking At

When NodalArc starts, a constellation session is already running. The default session deploys 36 satellites in a single orbital ring at 550 km altitude with OSPF routing and 7 ground stations.

On the globe you'll see:

- **Satellites** - dots moving along their orbital paths
- **ISL links** - lines connecting satellites that can currently see each other (inter-satellite links via optical laser terminals)
- **Ground stations** - fixed points on the Earth's surface
- **Ground links** - lines connecting ground stations to overhead satellites

Everything moves in real time. As satellites orbit, links appear when satellites enter line-of-sight range and disappear when they move out of range. Ground stations hand off between satellites as the constellation passes overhead.

## First Things to Try

### 1. Watch a ground station handoff

Ground stations connect to whichever satellite is currently overhead with the best elevation angle. As the constellation moves, the connected satellite changes. Watch a ground station (fixed point on the surface) - you'll see its link disconnect from one satellite and connect to the next one passing overhead.

### 2. Click a satellite

Click any satellite to select it. The detail panel shows its current state: position, active links, connected neighbors. You can see which ISLs are active and whether it has a ground station connection.

### 3. Open a terminal

With a satellite selected, open the terminal panel (bottom of screen). You land in a router CLI - the same interface network engineers use on physical routers. Try:

- `show ip route` - see the routing table
- `show ip ospf neighbor` - see which neighbors this satellite has formed adjacencies with
- `show interface brief` - see all interfaces and their state (UP/DOWN)

### 4. Trace a path

Select a source node and a destination node, then use the path trace feature to see the hop-by-hop forwarding path between them. The trace shows each router the packet passes through and the per-hop latency.

### 5. Switch to topology view

Press **Tab** to switch from the 3D globe to a 2D network topology graph. This shows the constellation as a traditional network diagram with nodes and links, making it easier to see the overall structure and routing relationships.

### 6. Try a different constellation

Open the session wizard to deploy a different constellation. You can change the number of satellites, the orbital geometry, the routing protocol, and the ground station set. See [Sessions](sessions.md) for details.

## Navigation

### Globe Controls

- **Left-click drag** - rotate the globe
- **Scroll wheel** - zoom in/out
- **Right-click drag** - pan
- **Click a satellite/ground station** - select it, show details

### Keyboard

Press any of these keys (when not typing in an input field):

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
| H | Toggle historical mode |
| N | Toggle globe rendering mode |
| 1 | Color by area |
| 2 | Color by plane |

See [Keyboard Shortcuts](keyboard-shortcuts.md) for the full reference.

## Understanding What You See

### Link Colors

ISL links are colored to convey information at a glance:

- **Color by plane** (press 2) - each orbital plane gets a distinct color, making it easy to see intra-plane vs cross-plane links
- **Color by area** (press 1) - links colored by routing area assignment

### Satellite Trails

Press **T** to toggle satellite trails. Trails show the recent path each satellite has traveled, making orbital motion visible. The trail fades from bright to transparent as it ages.

### Orbital Paths

Press **P** to toggle full orbital paths. These show the complete orbital ring for each satellite, colored by orbital plane.

### Link State Changes

When a link comes up or goes down, it appears/disappears in the visualization. The event log (bottom panel) shows a timestamped record of every link state change, ground station handoff, and convergence event.

## Next Steps

- [Sessions](sessions.md) - deploy different constellations and routing stacks
- [Globe View](globe-view.md) - detailed guide to the 3D visualization
- [Terminal Access](terminal.md) - using the browser terminal to inspect routing state
- [Time Controls](time-controls.md) - pause, speed up, and seek through the simulation
