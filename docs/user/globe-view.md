# Globe View

The globe view is the primary visualization. It shows the active session in 3D:
satellites, ground nodes, orbital paths, inter-satellite links, ground links,
body frames, and relay paths.

For a LEO-only session, this looks like a familiar Earth-centered satellite
view. For multi-segment sessions, the same view can scale out to MEO, GEO,
Luna, and cislunar relays.

![Globe View Overview](../images/user-globe-overview.png)

## Navigation

| Action | Control |
|--------|---------|
| Rotate around focus | Left-click + drag |
| Zoom toward focus | Scroll wheel |
| Pan | Right-click + drag |
| Select node | Click a satellite or ground node |
| Fly to node/body | Double-click, or select from the filter drawer |
| Deselect | Press Escape or click empty space |
| Follow selected node | Press F |
| Top-down view | Press V |

The camera uses a focus point. When you fly to Luna, a GEO satellite, or a
cislunar relay, orbit and zoom controls pivot around that target. That keeps
deep-space navigation usable instead of forcing every interaction around the
center of Earth.

## Visual Elements

### Bodies

Earth and Luna render as bodies with local surface frames. Body rendering is a
view concern; the OME owns the physical body positions and publishes the
ephemeris facts needed by the frontend.

### Satellites and Relay Nodes

Satellites appear as glyphs in their segment frame. LEO nodes move quickly near
Earth, MEO and GEO nodes sit farther out, and lunar relay nodes move in the
Luna frame. Relay nodes created by `space_node` segments are rendered like other
network nodes but may represent a single explicit gateway.

Node color communicates operational family first. Segment styling and tags are
secondary visual channels; they must not hide fault state.

### Segments and Tags

Every node belongs to a segment such as `leo`, `geo`, `earth-site`, or
`luna-relay`. Segments carry tags like `earth`, `leo`, `ground`, `cislunar`, or
`relay`.

Press **Q** to open the filter drawer. Use it to:

- show or emphasize one segment
- filter by tags
- fly to a segment or body frame
- inspect which link rules involve a selected segment

### ISL and Relay Links

Inter-satellite links are drawn between active space nodes. Same-body
constellation links use the configured ISL candidate rules. Cross-body links,
such as Earth relay to lunar relay, use `inter_body_relay` rules and carry their
own range-derived latency.

Toggle ISL and relay link visibility with **L**.

### Ground Links

Ground links connect a ground node terminal to a visible satellite or relay. A
site may contain multiple ground nodes with different terminals and policies.
For example, one site can have a LEO access router and a GEO gateway router at
the same latitude/longitude.

Toggle ground link visibility with **G**.

### Satellite Trails and Orbital Paths

Press **T** to show recent trails. Press **P** to show full orbital paths. These
help make orbital motion visible, especially in LEO and polar sessions.

## Selection and Explainability

Click a node to open the detail panel. The panel shows:

- runtime node ID and display name
- node type, segment, tags, and body/frame
- position and altitude
- active links and link latencies
- candidate links and why they are accepted or rejected
- terminal/handoff policy for ground nodes
- actuation state when the Scheduler or Node Agent reports a fault

For a selected ground node, the scene can highlight candidate satellites and
draw the effective coverage envelope. This is the visual answer to questions
like "why is that satellite overhead but not connected?" The panel should show
whether the stop reason is geometry, terminal capability, policy, capacity, or
actuation.

## Display Toggles

| Key | Toggle | Default |
|-----|--------|---------|
| L | ISL and relay links | On |
| G | Ground links | On |
| P | Orbital paths | Off |
| T | Satellite trails | Off |
| N | Rendering mode | Standard |
| ; | Satellite labels | Off |
| ' | Ground labels | Off |
| Q | Filter drawer | Closed |

## Color Modes

| Key | Mode | Description |
|-----|------|-------------|
| 1 | Area | Colored by routing area assignment |
| 2 | Plane | Colored by orbital plane where applicable |

Some nodes, such as explicit relays or ground nodes, do not have orbital plane
membership. In those cases the view uses their segment/style metadata while
preserving the operational fault color channel.

## Views

| Key | View | Description |
|-----|------|-------------|
| Tab | Toggle | Switch between globe view and topology view |
| V | Top view | Camera above the active body/reference frame |
| F | Follow | Camera tracks the selected node |
| Escape | Reset | Deselect node, return to free camera |

## Current Limits

Historical playback controls are not a complete product feature yet. Pause,
resume, and speed changes are live OME controls. If a history control appears
in the UI during development, treat it as experimental until the runtime
history path is explicitly shipped.
