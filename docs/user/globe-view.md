# Globe View

The globe view is the primary visualization. It shows the full constellation orbiting Earth in real time with all active links, ground stations, and satellite motion.

![Globe View Overview](../images/user-globe-overview.png)

## Navigation

| Action | Control |
|--------|---------|
| Rotate globe | Left-click + drag |
| Zoom | Scroll wheel |
| Pan | Right-click + drag |
| Select node | Click a satellite or ground station |
| Deselect | Press Escape or click empty space |
| Follow selected node | Press F |
| Top-down view | Press V |

## Visual Elements

### Satellites

Satellites appear as dots moving along their orbital paths. Their color depends on the active color mode:

- **Color by plane** (press 2) — each orbital plane gets a distinct color. Satellites in the same plane share a color, making orbital structure visible at a glance.
- **Color by area** (press 1) — satellites colored by their routing area assignment. Useful for visualizing how the constellation is partitioned for flooding scope.

### ISL Links

Inter-satellite links are drawn between connected satellite pairs. There are two types:

- **Intra-plane ISLs** — links between satellites in the same orbital plane (forward and backward neighbors in the ring). These are always active while satellites remain in the plane.
- **Cross-plane ISLs** — links between satellites in adjacent orbital planes. These appear and disappear as the relative geometry between planes changes.

ISL links curve slightly (bowed arc) to distinguish them from straight ground links and to prevent visual overlap when two satellites are at similar altitudes.

Toggle ISL link visibility with **L**.

### Ground Stations

Ground stations appear as fixed points at their geographic coordinates on the Earth's surface. They don't move — the constellation moves over them.

### Ground Links

Straight lines connecting ground stations to their currently-connected satellite. Ground links are dynamic — they appear when a satellite enters the ground station's coverage cone (elevation angle exceeds the minimum threshold) and disappear when the satellite passes out of coverage.

Toggle ground link visibility with **G**.

### Satellite Trails

Press **T** to show trails. Each satellite leaves a fading trace showing its recent path. The trail color matches the satellite's color (by plane or by area) and fades from bright to transparent over time.

Trails make orbital motion intuitive — you can see the direction and speed of each satellite at a glance.

### Orbital Paths

Press **P** to show full orbital paths. These are the complete orbital rings (not trails — the full predicted path). Colored by orbital plane so you can see the constellation's geometric structure.

## Panels

### Detail Panel

Click any satellite or ground station to open the detail panel (right side). It shows:

- **Node ID** — the satellite or ground station name
- **Position** — latitude, longitude, altitude
- **Active links** — ISL count and ground connection count
- **Neighbors** — routing protocol neighbors (IS-IS or OSPF adjacencies)

### Event Log

The bottom panel shows a timestamped event log. Events include:

- **Link Up** — a new ISL or ground link activated
- **Link Down** — a link deactivated (satellite moved out of range)
- **Handoff** — ground station connection transferred from one satellite to another

Use the event filter to search for specific nodes or event types. Press **/** to focus the filter input.

### Terminal Panel

Open the terminal panel to access any node's router CLI. See [Terminal Access](terminal.md) for details.

## Display Toggles

| Key | Toggle | Default |
|-----|--------|---------|
| L | ISL links | On |
| G | Ground links | On |
| P | Orbital paths | Off |
| T | Satellite trails | Off |
| N | Globe rendering mode | Standard |
| ; | Satellite labels | Off |
| ' | Ground station labels | Off |

## Color Modes

| Key | Mode | Description |
|-----|------|-------------|
| 1 | Area | Colored by routing area assignment |
| 2 | Plane | Colored by orbital plane (default) |

## Views

| Key | View | Description |
|-----|------|-------------|
| Tab | Toggle | Switch between globe view and topology view |
| V | Top view | Camera above the North Pole, looking down |
| F | Follow | Camera tracks the selected node |
| Escape | Reset | Deselect node, return to free camera |
